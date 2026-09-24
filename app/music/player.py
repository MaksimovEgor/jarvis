"""Плеер одного устройства: очередь треков поверх выхода (mpv или браузер).

Плееров столько, сколько устройств (app/music/devices.py), но логика одна:

    play_youtube("Queen") ─► search → [трек]  ─► играет сразу
                                   └► mix (фоном) ─► +~20 похожих в очередь
    play_wave(mood)       ─► Wave.first() (лайк с диска) ─► играет сразу
                                   └► Wave.more() (фоном) ─► досыпает, когда впереди < 3
    выход сообщил «трек кончился» ─► следующий в очереди (скачан заранее)

Каждый короткий трек с YouTube пишется в журнал прослушиваний
(app/music/library.py): откуда звук, сколько ждали старта и чем кончилось —
дослушан, пропущен (раньше SKIP_BEFORE и половины), дизлайк, ошибка,
застрял. По журналу учится волна и считается метрика.

Длинное (книги, подкасты) начинается с сохранённой позиции минус 10с и
сохраняет позицию на паузе, стопе, переключении и раз в 15с
(app/music/positions.py).

Приглушение duck()/unduck() — только для listener на asus (сейчас выключен):
трек, включённый во время команды, ждёт unduck(). Веб приглушает себя сам,
на клиенте (useMusic.hold/release).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from app.config import settings
from app.music import positions, radio, sources, storage, taste, yandex, youtube
from app.music.library import artist_key, library
from app.music.models import (
    MOOD_RU, SLOT_RU, Entity, FromWhere, Kind, Mood, Outcome, Rating, Track, WaveSpec, split_title,
)
from app.music.outputs import Output
from app.music.wave import AHEAD, REFILL_BELOW, Wave

logger = logging.getLogger("jarvis.player")

LOCAL_EXTS = (".mp3", ".flac", ".ogg", ".m4a", ".wav")
# Страховка, если listener упал между duck и unduck: музыка не должна
# остаться на паузе навсегда. С запасом над таймаутом ответа ядра.
DUCK_TIMEOUT = 200.0
# Сколько битых треков подряд пропускать, прежде чем сдаться.
MAX_SKIPS = 3
AUTOSAVE_SECONDS = 15
# Переключил раньше — «пропуск» (слабый минус), позже — «дослушал».
SKIP_BEFORE = 30.0
# Трек волны качается перед игрой: дольше — пропускаем, волна не ждёт.
WAVE_DOWNLOAD_TIMEOUT = 25.0
# Исполнитель с таким числом дизлайков убирается из уже собранной очереди.
ARTIST_BAN_DISLIKES = 2
# Почему выход закончил трек → исход в журнале.
_END_OUTCOME: dict[str, Outcome] = {"eof": "finished", "error": "error", "stall": "stalled"}


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


UPCOMING_SHOWN = 3


def _display(track: Track) -> dict[str, Any]:
    """Что показать о треке на экране: песня и исполнитель раздельно, обложка,
    источник. coverCrop — превью YouTube 4:3 с полями (фронт обрежет), у
    Яндекса и SoundCloud обложка квадратная."""
    if track.source in ("radio", "local") or (track.source == "youtube" and track.is_long and track.kind != "music"):
        cover = f"media/cover/{track.ref}" if track.source == "youtube" else None
        return {"song": track.title, "artist": None, "cover": cover, "coverCrop": True,
                "service": {"radio": "Радио", "local": "Файл"}.get(track.source, "YouTube"), "note": None}
    if track.cover and track.source != "youtube":
        library.upsert(track)  # /media/cover берёт обложку Яндекса из библиотеки
    artist = track.artist or youtube.meta(track.ref)[0]
    if artist is None and (info := library.track(track.ref)) is not None:
        artist = info.artist
    song, artist = split_title(track.title, artist)
    note = "нет в Яндексе" if yandex.is_on() and track.source != "yandex" and track.origin == "query" else None
    return {"song": song, "artist": artist, "cover": f"media/cover/{track.ref}",
            "coverCrop": track.source == "youtube", "service": sources.SERVICE_LABEL[track.service], "note": note}


def _journaled(track: Track) -> bool:
    """В журнал и оценки — только песни: у радио и файлов нет id, книги
    оценивать бессмысленно."""
    return sources.is_song(track)


class Player:
    def __init__(self, device: str, output: Output) -> None:
        self.device = device
        self.output = output
        output.on_end = self._on_end
        self._queue: list[Track] = []
        self._pos = -1
        # Меняется при каждой новой очереди — фоновые задачи старой очереди
        # (догрузка микса, волны) по нему понимают, что опоздали.
        self._generation = 0
        self._lock = asyncio.Lock()
        # Счётчик, а не флаг: голосовая команда и объявление таймера могут
        # пересечься, музыка вернётся после последнего unduck.
        self._duck_depth = 0
        self._resume_on_unduck = False
        self._duck_guard: asyncio.Task[None] | None = None
        self._autosave: asyncio.Task[None] | None = None
        self.wave: Wave | None = None
        self._refilling = False
        self._play_id: int | None = None

    @property
    def _duck_active(self) -> bool:
        return self._duck_depth > 0

    @property
    def current(self) -> Track | None:
        return self._queue[self._pos] if 0 <= self._pos < len(self._queue) else None

    # --- позиции длинного ---------------------------------------------------

    async def _save_position(self, finished: bool = False) -> None:
        track = self.current
        if track is not None and track.is_long:
            positions.save(track, await self.output.position(), finished=finished)

    async def _autosave_loop(self) -> None:
        while True:
            await asyncio.sleep(AUTOSAVE_SECONDS)
            try:
                if self.current is not None and self.current.is_long and not await self.output.is_paused():
                    await self._save_position()
            except Exception:
                logger.exception("Не получилось сохранить позицию")

    # --- журнал прослушиваний ---------------------------------------------

    def _origin_label(self, track: Track) -> str | None:
        if track.origin == "wave" and self.wave is not None:
            return f"волна · {self.wave.label or MOOD_RU[self.wave.mood] or SLOT_RU[self.wave.slot]}"
        if track.origin == "liked":
            return "мои лайки"
        return None

    def _open_play(self, track: Track, where: FromWhere, start_ms: int) -> None:
        artist, duration = youtube.meta(track.ref)
        mood = self.wave.mood if self.wave is not None and track.origin == "wave" else None
        try:
            self._play_id = library.start_play(track, self.device, mood, where, start_ms)
            if artist or duration:
                library.set_artist(track.ref, None if track.artist else artist, duration)
        except Exception:
            self._play_id = None
            logger.exception("Не записал прослушивание")
        if self.wave is not None and track.origin == "wave":
            asyncio.create_task(self.wave.on_start(track))

    async def _close_play(self, outcome: Outcome | None = None) -> None:
        """outcome=None — решить по позиции: пропуск или дослушал."""
        play_id, track = self._play_id, self.current
        if play_id is None or track is None:
            return
        self._play_id = None
        listened = await self.output.position() or 0.0
        if outcome is None:
            duration = track.duration or youtube.meta(track.ref)[1] or 0
            early = listened < SKIP_BEFORE and (not duration or listened < duration / 2)
            outcome = "skipped" if early else "finished"
        try:
            library.finish_play(play_id, outcome, round(listened, 1))
        except Exception:
            logger.exception("Не записал исход прослушивания")
        if self.wave is not None and track.origin == "wave":
            asyncio.create_task(self.wave.on_end(track, outcome, listened))

    def _record_failed(self, track: Track) -> None:
        try:
            library.finish_play(library.start_play(track, self.device, None, "net", None), "error", 0)
        except Exception:
            logger.exception("Не записал ошибку трека")

    # --- загрузка ---------------------------------------------------------

    async def _play_index(self, index: int) -> Track:
        await self._save_position()
        if self._autosave is None:
            self._autosave = asyncio.create_task(self._autosave_loop())
        for i in range(index, min(index + MAX_SKIPS, len(self._queue))):
            track = self._queue[i]
            where: FromWhere | None = None
            if sources.is_downloadable(track):
                where = storage.where(track.ref)
            elif track.source == "youtube":
                where = "stream"  # длинное с YouTube — потоком
            started = time.monotonic()
            try:
                if track.origin == "wave" and where == "net":
                    await sources.download(track, timeout=WAVE_DOWNLOAD_TIMEOUT)
                await self.output.load(track, positions.start_for(track), paused=self._duck_active)
            except Exception as exc:
                logger.warning("Пропускаю «%s»: %s", track.title, exc)
                if _journaled(track):
                    self._record_failed(track)
                continue
            self._pos = i
            self._resume_on_unduck = self._duck_active
            if _journaled(track) and where is not None:
                self._open_play(track, where, int((time.monotonic() - started) * 1000))
            self.output.set_meta({
                "rating": library.rating(track.ref) if _journaled(track) else None,
                "origin": self._origin_label(track),
                "from": where,
                "quality": None,
                **_display(track),
                "upcoming": self._upcoming_meta(),
            })
            if where in ("liked", "cache", "net"):
                asyncio.create_task(self._publish_audio_info(track))
            if i + 1 < len(self._queue):
                self.output.prefetch(self._queue[i + 1])
            if self.wave is not None:
                asyncio.create_task(self._refill(self._generation))
            logger.info("%s играет: %s", self.device, track.title)
            return track
        raise RuntimeError("не получилось загрузить ни один трек")

    async def _start_queue(self, tracks: list[Track]) -> Track:
        await self._close_play("stopped")
        await self._save_position()
        self._generation += 1
        self._queue = tracks
        self._pos = -1
        return await self._play_index(0)

    async def _on_end(self, reason: str) -> None:
        """reason: eof — доиграл, error — не смог, stall — завис (сторож)."""
        track = self.current
        async with self._lock:
            if track is None or self.current is not track:
                return  # пока ждали lock, очередь сменили
            if reason == "error":
                logger.warning("%s: не смог проиграть «%s»", self.device, track.title)
            elif reason == "stall":
                logger.warning("%s: «%s» завис — дальше", self.device, track.title)
            elif track.is_long:
                positions.save(track, track.duration or 0, finished=True)
            await self._close_play(_END_OUTCOME.get(reason, "finished"))
            if self._pos + 1 < len(self._queue):
                try:
                    await self._play_index(self._pos + 1)
                    return
                except Exception:
                    logger.exception("Не получилось переключить трек")
            self._queue, self._pos = [], -1

    def _playable(self, tracks: list[Track]) -> list[Track]:
        """Дизлайкнутое не звучит нигде, исполнитель с 2+ дизлайками — тоже в миксах."""
        banned = library.banned_refs()
        artists = {a.casefold() for a, _ in library.disliked_artists(ARTIST_BAN_DISLIKES)}
        return [t for t in tracks if t.ref not in banned and artist_key(t.artist) not in artists]

    async def _extend_with_mix(self, seed: Track, generation: int) -> None:
        try:
            tracks = self._playable(await sources.similar(seed))
        except Exception:
            logger.exception("Не получилось получить микс для «%s»", seed.title)
            return
        async with self._lock:
            if generation != self._generation:
                return
            was_last = self._pos == len(self._queue) - 1
            self._queue.extend(replace(t, origin="mix") for t in tracks)
            if was_last and self._pos + 1 < len(self._queue):
                self.output.prefetch(self._queue[self._pos + 1])
            self._publish_upcoming()

    async def _refill(self, generation: int) -> None:
        """Волна: впереди меньше REFILL_BELOW — досыпать AHEAD треков."""
        wave = self.wave
        if wave is None or self._refilling or len(self._queue) - self._pos - 1 >= REFILL_BELOW:
            return
        self._refilling = True
        try:
            tracks = await wave.more(self._queue[max(0, self._pos - 20):], AHEAD)
        except Exception:
            logger.exception("Волна не досыпала треки")
            return
        finally:
            self._refilling = False
        async with self._lock:
            if generation != self._generation or self.wave is not wave:
                return
            was_last = self._pos == len(self._queue) - 1
            queued = {t.ref for t in self._queue}
            self._queue.extend(t for t in tracks if t.ref not in queued)
            if was_last and self._pos + 1 < len(self._queue):
                self.output.prefetch(self._queue[self._pos + 1])
            self._publish_upcoming()

    # --- включение --------------------------------------------------------

    async def play_query(self, query: str, kind: Kind = "music") -> str:
        """Песня/исполнитель по запросу: Яндекс, чего нет — YouTube, SoundCloud
        (sources.resolve); дальше похожие того же источника."""
        if kind != "music" and yandex.is_on():
            # Книги и подкасты — сначала каталог Яндекса (главы по порядку, с места остановки).
            from app.music import catalog

            try:
                sections = await catalog.search(query, "books" if kind == "audiobook" else "podcasts")
            except Exception:
                logger.warning("Яндекс не нашёл книгу/подкаст «%s»", query, exc_info=True)
                sections = []
            found = next((e for sec in sections for e in sec.items if e.source == "yandex"), None)
            if found is not None:
                return await self.play_entity(found)
        async with self._lock:
            candidates = await sources.resolve(query, kind)
            if kind == "music":
                candidates = self._playable(candidates)
            if not candidates:
                return f"Нигде ничего не нашлось по запросу «{query}»."
            self.wave = None
            # Первый, что загрузится (_play_index пропускает недоступные);
            # остальные кандидаты в очереди не нужны — дальше пойдёт микс.
            track = await self._start_queue(candidates[:MAX_SKIPS])
            self._queue, self._pos = [track], 0
            generation = self._generation
        if kind == "music":
            asyncio.create_task(self._extend_with_mix(track, generation))
            return f"Включаю: {track.title}. Дальше похожие треки."
        start = positions.start_for(track)
        return f"Включаю: {track.title}" + (f", продолжаю с {_clock(start)}." if start else ".")

    # Старое имя: MCP-инструмент и тесты зовут play_youtube.
    play_youtube = play_query

    async def play_wave(self, mood: Mood = "auto", spec: WaveSpec | None = None) -> str:
        """mood — наше настроение; spec — полные настройки волны Яндекса
        (станция занятия/жанра/эпохи, настроение, характер, язык)."""
        wave = Wave(self.device, mood, spec=spec)
        taste.refresh_seeds_soon(wave.slot, wave.mood)
        async with self._lock:
            first = await wave.first()
            tracks = [first] if first else await wave.more([], REFILL_BELOW, tag=False)
            if not tracks:
                return "Не получилось собрать волну: нет ни интернета, ни сохранённой музыки."
            self.wave = wave
            try:
                track = await self._start_queue(tracks)
            except RuntimeError:
                self.wave = None
                return "Не получилось включить волну — треки не загрузились."
        label = wave.label or MOOD_RU[wave.mood]
        what = f" — {label}" if label else ""
        return f"Включаю твою волну{what}: {track.title}."

    async def play_liked(self, query: str | None = None) -> str:
        items = library.liked(limit=1000, query=query)
        if not items:
            return "В «Моей музыке» пока пусто — скажи «лайк» на понравившемся треке." if not query \
                else f"Среди лайков нет «{query}»."
        tracks = [i.track("liked") for i in items]
        random.shuffle(tracks)
        async with self._lock:
            self.wave = None
            track = await self._start_queue(tracks)
        return f"Включаю твои лайки: {track.title}, всего {len(tracks)}."

    async def play_ref(self, ref: str) -> str:
        """Конкретный трек с экрана «Моя музыка», дальше — похожие."""
        info = library.track(ref)
        if info is None:
            return "Не знаю такой трек."
        async with self._lock:
            self.wave = None
            track = await self._start_queue([info.track("liked" if info.rating == 1 else "query")])
            generation = self._generation
        asyncio.create_task(self._extend_with_mix(track, generation))
        return f"Включаю: {track.title}."

    async def resume_listening(self, query: str | None) -> str:
        items = positions.unfinished(query)
        if not items:
            return "Недослушанного нет." if not query else f"Недослушанного по запросу «{query}» нет."
        async with self._lock:
            self.wave = None
            track = await self._start_queue([items[0].track()])
        return f"Продолжаю «{track.title}» с {_clock(positions.start_for(track))}."

    async def play_radio(self, name: str | None, genre: str | None, country_code: str | None) -> str:
        stations = await radio.search(name=name, genre=genre, country_code=country_code)
        if not stations:
            return "Не нашёл такую радиостанцию."
        async with self._lock:
            self.wave = None
            # Остальные найденные станции — в очередь: «следующая» переключит
            # на похожую, а битый поток сам пропустится.
            track = await self._start_queue(stations)
        return f"Включаю радио {track.title}."

    async def play_local(self, query: str) -> str:
        library_dir = Path(settings.music_library_dir)
        # Кэш и лайки YouTube лежат внутри библиотеки, но это не «свои файлы».
        skip = {storage.cache_dir().resolve(), storage.liked_dir().resolve()}
        needle = query.lower()
        files = sorted(
            p for p in library_dir.rglob("*")
            if p.suffix.lower() in LOCAL_EXTS and needle in str(p.relative_to(library_dir)).lower()
            and not skip & set(p.resolve().parents)
        ) if library_dir.exists() else []
        if not files:
            return f"В локальной библиотеке ничего не нашлось по запросу «{query}»."
        async with self._lock:
            self.wave = None
            track = await self._start_queue([Track(title=p.stem, source="local", ref=str(p)) for p in files])
        return f"Включаю {track.title}, всего {len(files)} в очереди."

    # --- поиск и очередь ----------------------------------------------------

    async def play_entity(self, entity: Entity) -> str:
        """Найденное в поиске: трек (дальше похожие), исполнитель, альбом,
        плейлист, книга (с недослушанной главы), подкаст (с последнего выпуска),
        станция (волна по ней)."""
        from app.music import catalog  # catalog → player нет, но держим импорт ленивым

        if entity.type == "station":
            spec = WaveSpec(station=entity.id, label=entity.title.lower())
            return await self.play_wave("auto", spec=spec)
        tracks, start = await catalog.resolve_entity(entity)
        if not tracks:
            return f"Не получилось включить «{entity.title}»."
        async with self._lock:
            self.wave = None
            await self._close_play("stopped")
            await self._save_position()
            self._generation += 1
            self._queue, self._pos = tracks, -1
            track = await self._play_index(start)
            generation = self._generation
        if entity.type == "track":
            asyncio.create_task(self._extend_with_mix(track, generation))
        if track.is_long:
            begin = positions.start_for(track)
            return f"Включаю «{track.title}»" + (f" с {_clock(begin)}." if begin else ".")
        return f"Включаю: {track.title}."

    async def add_next(self, track: Track) -> str:
        """«Играть следующим» — сразу за текущим; ничего не играет — включить."""
        async with self._lock:
            if self.current is None:
                self._queue, self._pos = [track], -1
                await self._play_index(0)
                return f"Включаю: {track.title}."
            self._queue.insert(self._pos + 1, replace(track, origin="query"))
            self.output.prefetch(track)
            self._publish_upcoming()
        return f"Следующим: {track.title}."

    def queue_view(self) -> dict[str, Any]:
        return {
            "pos": self._pos,
            "tracks": [{"ref": t.ref, **_display(t)} for t in self._queue],
        }

    async def queue_move(self, src: int, dst: int) -> None:
        """Переставить трек в будущей части очереди; текущий и сыгранные не трогаем."""
        async with self._lock:
            lo = self._pos + 1
            if not (lo <= src < len(self._queue) and lo <= dst < len(self._queue)):
                return
            self._queue.insert(dst, self._queue.pop(src))
            if lo in (src, dst):
                self.output.prefetch(self._queue[lo])
            self._publish_upcoming()

    async def queue_remove(self, index: int) -> None:
        async with self._lock:
            if self._pos < index < len(self._queue):
                del self._queue[index]
                self._publish_upcoming()

    async def play_at(self, index: int) -> str:
        async with self._lock:
            if not 0 <= index < len(self._queue) or index == self._pos:
                return "Уже играет." if index == self._pos else "Нет такого трека в очереди."
            await self._close_play()
            track = await self._play_index(index)
        return f"Включаю: {track.title}."

    # --- оценки -----------------------------------------------------------

    async def rate(
        self, value: Rating | None, ref: str | None = None,
        which: Literal["current", "previous"] = "current",
    ) -> str:
        """Лайк/дизлайк текущего (или предыдущего, или ref — с экрана).
        Дизлайк текущего сразу переключает дальше, как у Алисы.
        value=None — снять оценку («Отменить», «Вернуть»)."""
        target: Track | None = None
        if ref is None:
            target = self.current if which == "current" else (
                self._queue[self._pos - 1] if self._pos > 0 else None)
            if target is None:
                return "Сейчас ничего не играет." if which == "current" else "Предыдущего трека нет."
            if not _journaled(target):
                return "Оценить можно только песню — не радио, не книгу и не файл."
            ref = target.ref
        elif self.current is not None and self.current.ref == ref:
            target = self.current
        if target is not None:
            library.upsert(target)
        before = library.rating(ref)
        library.rate(ref, value)
        is_current = self.current is not None and self.current.ref == ref
        if is_current:
            self.output.set_meta({"rating": value})

        asyncio.create_task(yandex.set_like(ref, value, before))
        if value == 1:
            track = target or (info.track() if (info := library.track(ref)) else None)
            if track is not None:
                asyncio.create_task(self._save_liked(track))
            taste.tag_soon(ref)
            return "Сохранил в «Мою музыку»."
        if before == 1:
            storage.unpin(ref)
        if value is None:
            return "Убрал оценку."

        # Дизлайк: из очереди — сам трек и, после двух дизлайков, исполнитель.
        if self.wave is not None:
            self.wave.forget(ref)
        async with self._lock:
            head = self._queue[: self._pos + 1]
            self._queue = head + self._playable([t for t in self._queue[self._pos + 1:] if t.ref != ref])
            self._publish_upcoming()
            if is_current:
                await self._close_play("disliked")
                if self._pos + 1 < len(self._queue):
                    try:
                        await self._play_index(self._pos + 1)
                    except RuntimeError:
                        self._queue, self._pos = [], -1
                        await self.output.stop()
                else:
                    self._queue, self._pos = [], -1
                    await self.output.stop()
        storage.drop(ref)
        return "Понял, больше не включу."

    async def _save_liked(self, track: Track) -> None:
        """Лайк → файл навсегда в liked/ (Яндекс — в FLAC, если отдаст)."""
        try:
            await sources.save_liked(track)
        except Exception:
            logger.exception("Не сохранил лайкнутый «%s»", track.title)
            return
        if self.current is not None and self.current.ref == track.ref:
            self.output.set_meta({"from": "liked"})

    # --- управление -------------------------------------------------------

    async def pause(self) -> str:
        if self.current is None:
            return "Сейчас ничего не играет."
        self._resume_on_unduck = False
        await self.output.set_paused(True)
        await self._save_position()
        return "Пауза."

    async def resume(self) -> str:
        if self.current is None:
            return "Нечего продолжать — очередь пуста."
        if self._duck_active:
            self._resume_on_unduck = True
        else:
            await self.output.set_paused(False)
        return "Продолжаю."

    async def stop(self) -> str:
        async with self._lock:
            await self._close_play("stopped")
            await self._save_position()
            self._generation += 1
            self._queue, self._pos = [], -1
            self.wave = None
            self._resume_on_unduck = False
            await self.output.stop()
        return "Остановил."

    async def next(self) -> str:
        if self.wave is not None and self._pos + 1 >= len(self._queue):
            # Волна не успела досыпать — подождём её здесь.
            await self._refill(self._generation)
        async with self._lock:
            if self._pos + 1 >= len(self._queue):
                if self.wave is not None:
                    return "Волна не успела подобрать следующий трек, попробуй ещё раз."
                return "Дальше в очереди ничего нет."
            await self._close_play()
            track = await self._play_index(self._pos + 1)
        return f"Следующий: {track.title}."

    async def previous(self) -> str:
        async with self._lock:
            if self._pos <= 0:
                return "Это первый трек в очереди."
            await self._close_play()
            track = await self._play_index(self._pos - 1)
        return f"Предыдущий: {track.title}."

    async def seek(self, delta: float | None = None, position: float | None = None) -> str:
        track = self.current
        if track is None or track.source == "radio":
            return "Перематывать нечего."
        current = await self.output.position() or 0.0
        target = max(0.0, position if position is not None else current + (delta or 0))
        if track.duration:
            target = min(target, track.duration - 1)
        await self.output.seek(target)
        return f"Перемотал на {_clock(target)}."

    async def volume(self, level: int | None = None, delta: int | None = None) -> str:
        current = await self.output.volume()
        base = current if current is not None else settings.music_volume
        target = max(0, min(100, level if level is not None else base + (delta or 0)))
        if not await self.output.set_volume(target):
            return "На этом устройстве громкость меняется кнопками телефона."
        return f"Громкость {target} из 100."

    async def now_playing(self) -> str:
        track = self.current
        if track is None:
            return "Сейчас ничего не играет."
        paused = await self.output.is_paused() and not self._resume_on_unduck
        state = "На паузе" if paused else "Играет"
        if track.source == "radio":
            song = await self.output.stream_title()
            return f"{state} радио {track.title}" + (f", сейчас в эфире: {song}." if song else ".")
        if track.is_long:
            pos = await self.output.position() or 0
            total = f" из {_clock(track.duration)}" if track.duration else ""
            return f"{state}: {track.title}, {_clock(pos)}{total}."
        liked = " Это из твоих лайков." if library.rating(track.ref) == 1 else ""
        wave = " Играет твоя волна." if self.wave is not None else ""
        left = len(self._queue) - self._pos - 1
        return f"{state}: {track.title}.{liked}{wave} В очереди ещё {left}."

    def _upcoming_meta(self) -> list[dict[str, Any]]:
        upcoming = self._queue[self._pos + 1:self._pos + 1 + UPCOMING_SHOWN]
        for track in upcoming:
            if track.cover:
                library.upsert(track)  # обложку Яндекса /media/cover берёт из библиотеки
        return [{"ref": t.ref, **_display(t)} for t in upcoming]

    def _publish_upcoming(self) -> None:
        if self.current is not None:
            self.output.set_meta({"upcoming": self._upcoming_meta()})

    async def _publish_audio_info(self, track: Track) -> None:
        try:
            quality = await storage.quality(track.ref)
        except Exception:
            logger.exception("ffprobe не ответил про «%s»", track.title)
            return
        if quality and self.current is track:
            self.output.set_meta({"quality": quality})

    def upcoming(self, limit: int = 5) -> list[str]:
        return [t.title for t in self._queue[self._pos + 1:self._pos + 1 + limit]]

    # --- приглушение на время голосовой команды ---------------------------

    async def duck(self) -> None:
        self._duck_depth += 1
        if self._duck_depth == 1:
            playing = self.current is not None and not await self.output.is_paused()
            self._resume_on_unduck = playing
            if playing:
                await self.output.set_paused(True)
        if self._duck_guard:
            self._duck_guard.cancel()
        self._duck_guard = asyncio.create_task(self._auto_unduck())

    async def unduck(self, force: bool = False) -> None:
        if not self._duck_active:
            return
        self._duck_depth = 0 if force else self._duck_depth - 1
        if self._duck_active:
            return
        if self._duck_guard:
            self._duck_guard.cancel()
            self._duck_guard = None
        if self._resume_on_unduck and self.current is not None:
            await self.output.set_paused(False)
        self._resume_on_unduck = False

    async def _auto_unduck(self) -> None:
        await asyncio.sleep(DUCK_TIMEOUT)
        logger.warning("unduck не пришёл за %.0fс — возвращаю музыку сам", DUCK_TIMEOUT)
        self._duck_guard = None
        await self.unduck(force=True)

    async def state(self) -> dict[str, Any]:
        track = self.current
        return {
            "device": self.device,
            "track": track.title if track else None,
            "source": track.source if track else None,
            "paused": await self.output.is_paused() if track else None,
            "position": await self.output.position() if track else None,
            "ducked": self._duck_active,
            "volume": await self.output.volume(),
            "upcoming": self.upcoming(),
            "wave": (self.wave.label or self.wave.mood) if self.wave else None,
        }
