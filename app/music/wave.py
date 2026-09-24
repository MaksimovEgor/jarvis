"""«Моя волна»: бесконечная очередь под вкус, время суток и настроение.

Решает мгновенно и без LLM — профиль dj (app/music/taste.py) только
подкладывает зёрна поиска и размечает энергию треков в фоне.

    пул кандидатов                        compose() — чистая функция
    ├ liked    — лайки                    ├ корзины в пропорции BUCKETS[mood]
    ├ similar  — радио YouTube Music       ├ вес: энергия под слот/настроение,
    │            от лайков и зёрен dj      │   любимое в этот слот, штрафы
    └ discover — поиск по зёрнам dj        │   за дизлайки и пропуски исполнителя
                                           ├ бан, недавнее (3 ч) — вон
                                           └ исполнитель не чаще раза в ARTIST_GAP

Нет интернета — пул из лайков и дослушанного в кэше, волна не молчит.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.music import storage, taste, yandex, youtube
from app.music.library import ArtistStats, TrackInfo, artist_key, library
from app.music.models import Mood, Outcome, Slot, Track, WaveSpec, slot_at

logger = logging.getLogger("jarvis.wave")

Bucket = Literal["yandex", "liked", "similar", "discover"]

# Доли корзин (liked, similar, discover).
BUCKETS: dict[Mood, tuple[float, float, float]] = {
    "auto": (0.3, 0.5, 0.2),
    "energetic": (0.3, 0.5, 0.2),
    "calm": (0.3, 0.5, 0.2),
    "focus": (0.3, 0.5, 0.2),
    "sleep": (0.4, 0.5, 0.1),
    "discover": (0.1, 0.4, 0.5),
}
# С подключённым Яндексом: основа — его «Моя волна» (yandex, liked, similar, discover).
BUCKETS_YANDEX: dict[Mood, tuple[float, float, float, float]] = {
    "auto": (0.5, 0.25, 0.15, 0.1),
    "energetic": (0.5, 0.25, 0.15, 0.1),
    "calm": (0.5, 0.25, 0.15, 0.1),
    "focus": (0.6, 0.2, 0.15, 0.05),
    "sleep": (0.6, 0.25, 0.15, 0.0),
    "discover": (0.6, 0.05, 0.1, 0.25),
}

# Наши настроения → настройки волны Яндекса (и обратно — для энергии без Яндекса).
MOOD_PRESET: dict[Mood, WaveSpec] = {
    "auto": WaveSpec(),
    "energetic": WaveSpec(mood_energy="active", label="бодрое"),
    "calm": WaveSpec(mood_energy="calm", label="спокойное"),
    "focus": WaveSpec(station="activity:work-background", label="работаю"),
    "sleep": WaveSpec(station="activity:fall-asleep", label="засыпаю"),
    "discover": WaveSpec(diversity="discover", label="незнакомое"),
}
_ACTIVITY_MOOD: dict[str, Mood] = {
    "wake-up": "energetic", "run": "energetic", "workout": "energetic", "party": "energetic",
    "driving": "auto", "road-trip": "auto", "work-background": "focus", "study-background": "focus",
    "fall-asleep": "sleep", "romantic-date": "calm", "beloved": "calm", "sex": "calm",
}


def spec_mood(spec: WaveSpec) -> Mood:
    """Какой энергией вести нашу часть волны под эти настройки Яндекса."""
    kind, _, tag = spec.station.partition(":")
    if kind == "activity" and tag in _ACTIVITY_MOOD:
        return _ACTIVITY_MOOD[tag]
    if spec.mood_energy in ("active", "fun"):
        return "energetic"
    if spec.mood_energy in ("calm", "sad"):
        return "calm"
    if spec.diversity == "discover":
        return "discover"
    return "auto"


# Допустимая энергия трека (1 — колыбельная, 5 — драйв).
_SLOT_ENERGY: dict[Slot, tuple[int, int]] = {
    "morning": (3, 5), "day": (2, 5), "evening": (2, 4), "night": (1, 3),
}
_MOOD_ENERGY: dict[Mood, tuple[int, int]] = {
    "energetic": (4, 5), "calm": (1, 3), "focus": (2, 3), "sleep": (1, 2),
}
ARTIST_GAP = 4
RECENT_HOURS = 3.0
DISLIKED_ARTIST_FACTOR = 0.25
UNTAGGED_FACTOR = 0.5
AHEAD = 8
REFILL_BELOW = 3
# Без лайков и зёрен — с чего начать (музыкальный вкус хозяина из памяти Hermes).
COLD_START: dict[Mood, str] = {
    "auto": "Foo Fighters", "energetic": "rock hits energetic", "calm": "acoustic rock ballads",
    "focus": "instrumental post-rock", "sleep": "calm ambient piano", "discover": "new alternative rock",
}


def bucket_shares(values: tuple[float, ...]) -> dict[Bucket, float]:
    """Доли по порядку корзин: 4 числа — с Яндексом, 3 — без."""
    names: tuple[Bucket, ...] = ("yandex", "liked", "similar", "discover") if len(values) == 4 \
        else ("liked", "similar", "discover")
    return dict(zip(names, values))


def energy_range(slot: Slot, mood: Mood) -> tuple[int, int]:
    return _MOOD_ENERGY.get(mood, _SLOT_ENERGY[slot])


@dataclass(frozen=True)
class Candidate:
    track: Track
    bucket: Bucket
    energy: int | None = None


@dataclass
class Context:
    slot: Slot
    mood: Mood
    banned: set[str] = field(default_factory=set)
    recent: set[str] = field(default_factory=set)
    artists: dict[str, ArtistStats] = field(default_factory=dict)
    # Исполнители последних треков очереди — чтобы не шли подряд.
    recent_artists: list[str] = field(default_factory=list)
    # Доли корзин; None — BUCKETS[mood] (без Яндекса).
    shares: dict[Bucket, float] | None = None


def weight(c: Candidate, ctx: Context) -> float:
    """0 — нельзя ставить; иначе относительный вес для случайного выбора."""
    if c.track.ref in ctx.banned or c.track.ref in ctx.recent:
        return 0.0
    w = 1.0
    if c.energy is not None:
        low, high = energy_range(ctx.slot, ctx.mood)
        if not low <= c.energy <= high:
            return 0.0
    elif ctx.mood in _MOOD_ENERGY or ctx.slot == "night":
        w *= UNTAGGED_FACTOR
    stats = ctx.artists.get(artist_key(c.track.artist))
    if stats:
        if stats.dislikes >= 2:
            w *= DISLIKED_ARTIST_FACTOR
        heard = stats.skips_30d + stats.finished_30d
        if heard:
            w *= 1 - 0.7 * stats.skips_30d / (heard + 1)
        if c.bucket == "liked" and stats.finished_30d:
            # Любимое именно в это время суток — чаще (0.5..2).
            share = (stats.slot_finished.get(ctx.slot, 0) + 1) / (stats.finished_30d + 4)
            w *= min(2.0, max(0.5, share * 4))
    return w


def compose(pool: list[Candidate], ctx: Context, n: int, rng: random.Random) -> list[Track]:
    """n треков из пула: доли корзин по настроению, взвешенно, без повторов.
    Пустая корзина отдаёт свою долю другим."""
    shares = ctx.shares or bucket_shares(BUCKETS[ctx.mood])
    left: dict[Bucket, list[tuple[Candidate, float]]] = {"yandex": [], "liked": [], "similar": [], "discover": []}
    seen: set[str] = set()
    for c in pool:
        if c.track.ref in seen:
            continue
        seen.add(c.track.ref)
        w = weight(c, ctx)
        if w > 0:
            left[c.bucket].append((c, w))

    recent = [artist_key(a) for a in ctx.recent_artists]
    out: list[Track] = []
    while len(out) < n:
        buckets = [b for b in left if left[b]]
        if not buckets:
            break
        bucket = rng.choices(buckets, [shares.get(b, 0) or 0.01 for b in buckets])[0]
        items = left[bucket]
        blocked = set(recent[-(ARTIST_GAP - 1):]) - {""}
        allowed = [i for i, (c, _) in enumerate(items) if artist_key(c.track.artist) not in blocked]
        if not allowed:
            # В этой корзине все — недавние исполнители; попробуем другие.
            others = [b for b in buckets if b != bucket and any(
                artist_key(c.track.artist) not in blocked for c, _ in left[b])]
            if others:
                bucket = others[0]
                items = left[bucket]
                allowed = [i for i, (c, _) in enumerate(items) if artist_key(c.track.artist) not in blocked]
            else:
                allowed = list(range(len(items)))
        index = rng.choices(allowed, [items[i][1] for i in allowed])[0]
        chosen, _ = items.pop(index)
        out.append(chosen.track)
        recent.append(artist_key(chosen.track.artist))
    return out


def _candidate(info: TrackInfo, bucket: Bucket) -> Candidate:
    return Candidate(info.track("wave"), bucket, info.energy)


class Wave:
    """Источник очереди плеера. Держит, от чего уже строили «похожее»,
    чтобы следующие пачки не повторяли одно и то же радио."""

    def __init__(
        self, device: str, mood: Mood = "auto", rng: random.Random | None = None,
        spec: WaveSpec | None = None,
    ) -> None:
        self.device = device
        self.spec = spec or MOOD_PRESET[mood]
        self.mood = mood if spec is None else spec_mood(spec)
        self._rng = rng or random.Random()
        self._used_seeds: set[str] = set()
        self._cursor = yandex.WaveCursor(self.spec)

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def yandex_only(self) -> bool:
        """Конкретные настройки (грустное, русское, бег, 90-е) знает только
        Яндекс: наши лайки и YouTube про язык и жанр ничего не знают."""
        return yandex.is_on() and not self.spec.is_plain

    def _shares(self) -> dict[Bucket, float] | None:
        if self.yandex_only:
            return {"yandex": 1.0}
        return bucket_shares(BUCKETS_YANDEX[self.mood]) if yandex.is_on() else None

    @property
    def slot(self) -> Slot:
        return slot_at(time.time())

    def _context(self, queued: list[Track]) -> Context:
        return Context(
            slot=self.slot, mood=self.mood,
            banned=library.banned_refs(),
            recent=library.recent_refs(RECENT_HOURS) | {t.ref for t in queued},
            artists=library.artist_stats(),
            recent_artists=[t.artist or "" for t in queued[-ARTIST_GAP:]],
            shares=self._shares(),
        )

    async def first(self) -> Track | None:
        """Мгновенный старт: лайк, который уже лежит на диске (кроме волны с
        настройками — её начинает Яндекс)."""
        if self.yandex_only:
            return None
        local = [i for i in library.liked(limit=500) if storage.where(i.ref) == "liked"]
        picked = compose([_candidate(i, "liked") for i in local], self._context([]), 1, self._rng)
        return picked[0] if picked else None

    async def more(self, queued: list[Track], n: int = AHEAD, tag: bool = True) -> list[Track]:
        """tag — дать dj разметить энергию новых кандидатов (секунды): так
        «спокойное» не подсунет драйв. На самом старте волны не ждём."""
        ctx = self._context(queued)
        liked = library.liked(limit=500)
        pool = [_candidate(i, "liked") for i in liked]
        seeds = library.seeds(ctx.slot, self.mood)
        similar_seeds = [s.query for s in seeds if s.bucket == "similar"]
        discover_seeds = [s.query for s in seeds if s.bucket == "discover"]
        if not seeds and (not liked or (not tag and self.mood in _MOOD_ENERGY)):
            similar_seeds = [COLD_START[self.mood]]

        jobs = []
        if yandex.is_on():
            jobs.append(self._yandex(n))
        if self.yandex_only:
            return await self._yandex_only(queued, n, jobs[0])
        # «Похожее»: радио YouTube Music от лайков и зёрен dj. Лайк здесь только
        # отправная точка, сам не играет — фильтр «недавно звучал» к нему не нужен.
        # Строгое настроение без разметки (старт волны): радио от лайка не знает
        # настроения — берём только зёрна dj под него или запасной запрос.
        strict_start = not tag and self.mood in _MOOD_ENERGY
        like_seeds = [] if strict_start else [
            i.ref for i in liked if i.ref not in self._used_seeds and i.ref not in ctx.banned]
        for ref in self._rng.sample(like_seeds, min(2, len(like_seeds))):
            self._used_seeds.add(ref)
            jobs.append(self._radio(ref))
        for query in self._pick(similar_seeds, 1 if liked else 2):
            jobs.append(self._search_then_radio(query))
        for query in self._pick(discover_seeds, 2):
            jobs.append(self._search(query))
        if not jobs:
            jobs.append(self._search_then_radio(COLD_START[self.mood]))

        found = await asyncio.gather(*jobs, return_exceptions=True)
        for result in found:
            if isinstance(result, BaseException):
                logger.warning("Волна: источник не ответил: %s", result)
                continue
            pool.extend(result)
        if strict_start:
            # Лайки на старте — только размеченные под настроение.
            pool = [c for c in pool if c.bucket != "liked" or c.energy is not None]
        if len(pool) <= len(liked):
            # Сеть не дала ничего — дослушанное из кэша, чтобы не молчать.
            pool += [_candidate(i, "similar") for i in library.played_from_cache() if storage.where(i.ref) != "net"]
        if tag:
            energy = await taste.energies_for([c.track for c in pool if c.energy is None])
            pool = [replace(c, energy=energy.get(c.track.ref, c.energy)) for c in pool]
        tracks = compose(pool, ctx, n, self._rng)
        if not tracks:
            # Всё недавно звучало — лучше повтор, чем тишина.
            ctx.recent = {t.ref for t in queued}
            tracks = compose(pool, ctx, n, self._rng)
        return [replace(t, origin="wave") for t in tracks]

    def _pick(self, queries: list[str], k: int) -> list[str]:
        fresh = [q for q in queries if q not in self._used_seeds] or queries
        picked = self._rng.sample(fresh, min(k, len(fresh)))
        self._used_seeds.update(picked)
        return picked

    async def _yandex_only(self, queued: list[Track], n: int, job: Any) -> list[Track]:
        try:
            pool = await job
        except Exception as exc:
            logger.warning("Волна Яндекса не ответила: %s", exc)
            return []
        ctx = self._context(queued)
        tracks = compose(pool, ctx, n, self._rng) or compose(pool, replace(ctx, recent=set()), n, self._rng)
        return [replace(t, origin="wave") for t in tracks]

    async def _yandex(self, n: int) -> list[Candidate]:
        """«Моя волна» Яндекса (или станция занятия/жанра) — пачка ~5, берём две."""
        found = await yandex.wave_tracks(self._cursor)
        if len(found) < n:
            found += await yandex.wave_tracks(self._cursor)
        return [Candidate(t, "yandex") for t in found]

    async def _radio(self, ref: str) -> list[Candidate]:
        # Лайк из Яндекса — его станция «по треку», из YouTube — радио YT Music.
        tracks = await yandex.similar(ref) if yandex.is_ref(ref) else await youtube.music_radio(ref)
        return [Candidate(t, "similar") for t in tracks]

    async def _search(self, query: str) -> list[Candidate]:
        return [Candidate(t, "discover") for t in (await youtube.music_search(query))[:4]]

    async def _search_then_radio(self, query: str) -> list[Candidate]:
        found = await youtube.music_search(query, limit=3)
        if not found:
            return []
        return [Candidate(found[0], "similar")] + await self._radio(found[0].ref)

    async def on_start(self, track: Track) -> None:
        if track.source == "yandex" and yandex.is_on():
            await yandex.feedback(self._cursor, "started", track.ref)

    async def on_end(self, track: Track, outcome: Outcome, played: float) -> None:
        """Яндекс учится: дослушал — finished, пропустил/дизлайк — skip."""
        if track.source != "yandex" or not yandex.is_on() or outcome in ("error", "stalled"):
            return
        kind = "finished" if outcome in ("finished", "stopped") else "skip"
        await yandex.feedback(self._cursor, kind, track.ref, played)

    def forget(self, ref: str) -> None:
        """После дизлайка — не строить от этого трека «похожее»."""
        self._used_seeds.add(ref)
