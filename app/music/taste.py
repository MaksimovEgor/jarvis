"""Музыкальный вкус: профиль Hermes «dj» думает, волна (wave.py) играет.

dj — отдельный профиль Hermes со своими SOUL и памятью (scripts/hermes-dj/,
свой gateway на :8643). Всё здесь — в фоне, волна его никогда не ждёт:

    refresh_seeds_soon(slot, mood) ─► dj: «что ставить вечером в среду» ─► library.seeds
    tag_soon(ref)  (лайк)          ─► dj: энергия 1-5, жанры (пачкой)   ─► library.tracks
    run_digest_daily()  04:00      ─► dj: сводка дня → он сам пишет выводы в свою память

dj недоступен — тот же запрос уходит в LLM_* (без памяти); и он лёг — зёрен
и разметки просто не будет, волна играет на лайках и радио YouTube Music.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import settings
from app.music.library import Seed, TrackInfo, library
from app.music.models import Mood, Slot, Track, is_weekend
from app.services.hermes import parse_output
from app.services.llm import get_llm

logger = logging.getLogger("jarvis.taste")

SEEDS_TTL_HOURS = 6.0
TAG_DELAY = 30.0
TAG_BATCH = 20
DJ_TIMEOUT = 180.0

# Для запасного пути через LLM_*: у dj то же самое записано в SOUL.
_FALLBACK_SYSTEM = (
    "Ты музыкальный куратор умной колонки. Отвечай строго JSON по схеме из запроса, "
    "без пояснений вокруг."
)

_MOOD_HINT: dict[Mood, str] = {
    "auto": "под время суток и день недели",
    "energetic": "бодрое, энергичное",
    "calm": "спокойное, расслабляющее",
    "focus": "фон для работы: ровное, без отвлекающего вокала, можно инструментал",
    "sleep": "для сна: очень тихое и медленное",
    "discover": "новое для хозяина, но в его вкусе",
}
_SLOT_HINT: dict[Slot, str] = {
    "morning": "утро", "day": "день", "evening": "вечер", "night": "ночь",
}

_tasks: set[asyncio.Task[Any]] = set()
_seeds_inflight: set[tuple[Slot, Mood]] = set()
_pending_tags: set[str] = set()
_tag_task: asyncio.Task[None] | None = None


def _spawn(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


# --- транспорт ---------------------------------------------------------------

async def _ask_dj(prompt: str) -> str:
    if not settings.dj_hermes_api_key:
        raise RuntimeError("DJ_HERMES_API_KEY не задан")
    payload = {"model": "hermes-agent", "input": prompt, "store": False}
    headers = {"Authorization": f"Bearer {settings.dj_hermes_api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(DJ_TIMEOUT, connect=5.0)) as client:
        resp = await client.post(f"{settings.dj_hermes_url.rstrip('/')}/v1/responses", json=payload, headers=headers)
        resp.raise_for_status()
    return parse_output(resp.json())[0]


async def _ask_llm(prompt: str) -> str:
    message = await get_llm().chat([
        {"role": "system", "content": _FALLBACK_SYSTEM},
        {"role": "user", "content": prompt},
    ])
    return message.get("content") or ""


async def ask(prompt: str, dj_only: bool = False) -> str | None:
    """dj → LLM → None. dj_only — для того, что имеет смысл только с памятью."""
    try:
        return await _ask_dj(prompt)
    except Exception as exc:
        logger.warning("dj недоступен (%s)%s", exc, "" if dj_only else " — спрашиваю LLM")
    if dj_only:
        return None
    try:
        return await _ask_llm(prompt)
    except Exception as exc:
        logger.warning("LLM тоже недоступен: %s", exc)
        return None


# --- разбор ответов ------------------------------------------------------------

def _json_object(text: str) -> dict[str, Any] | None:
    """Первый JSON-объект в ответе: модели любят ```json и слова вокруг."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_seeds(text: str) -> list[Seed]:
    data = _json_object(text) or {}
    seeds: list[Seed] = []
    for item in data.get("seeds") or []:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        bucket = item.get("bucket") if item.get("bucket") in ("similar", "discover") else "discover"
        seeds.append(Seed(query=query[:120], bucket=bucket, reason=str(item.get("reason") or "")[:200]))
    return seeds[:12]


def parse_tags(text: str) -> list[tuple[str, str | None, int | None, list[str]]]:
    """[(ref, исполнитель, энергия 1-5, теги)]."""
    data = _json_object(text) or {}
    out = []
    for item in data.get("tracks") or []:
        if not isinstance(item, dict) or not item.get("ref"):
            continue
        try:
            energy: int | None = max(1, min(5, int(item.get("energy"))))
        except (TypeError, ValueError):
            energy = None
        tags = [str(t).strip().lower() for t in item.get("tags") or [] if str(t).strip()][:6]
        artist = str(item.get("artist") or "").strip() or None
        out.append((str(item["ref"]), artist, energy, tags))
    return out


# --- зёрна ---------------------------------------------------------------------

def _seeds_prompt(slot: Slot, mood: Mood, weekend: bool) -> str:
    context = {
        "время": f"{_SLOT_HINT[slot]}, {'выходной' if weekend else 'будний день'}",
        "настроение": _MOOD_HINT[mood],
        "вкус": library.taste_summary(slot),
    }
    return (
        "Подбери зёрна для «Моей волны» умной колонки — поисковые запросы в YouTube Music.\n"
        f"Контекст:\n{json.dumps(context, ensure_ascii=False, indent=1)}\n\n"
        "Учитывай свою память о вкусе хозяина, заметки и дизлайки (их не предлагай), время и настроение. "
        "Дай 4 зерна bucket=similar (конкретная песня «Исполнитель - Песня» в духе любимого — от неё "
        "построится радио похожих) и 5 зёрен bucket=discover (исполнители и песни, которых нет среди "
        "любимых, но которые ему скорее всего зайдут сейчас).\n"
        'Ответ — только JSON: {"seeds": [{"query": "...", "bucket": "similar|discover", "reason": "коротко почему"}]}'
    )


async def ask_seeds(slot: Slot, mood: Mood, weekend: bool) -> list[Seed]:
    text = await ask(_seeds_prompt(slot, mood, weekend))
    return parse_seeds(text) if text else []


async def _refresh_seeds(slot: Slot, mood: Mood) -> None:
    try:
        seeds = await ask_seeds(slot, mood, is_weekend(time.time()))
        if seeds:
            library.save_seeds(seeds, slot, mood)
            logger.info("dj: %d зёрен для %s/%s", len(seeds), slot, mood)
    finally:
        _seeds_inflight.discard((slot, mood))


def refresh_seeds_soon(slot: Slot, mood: Mood) -> None:
    """Зёрна под слот и настроение, если свежих нет. Одна задача на пару."""
    if (slot, mood) in _seeds_inflight or library.seeds(slot, mood, SEEDS_TTL_HOURS):
        return
    _seeds_inflight.add((slot, mood))
    _spawn(_refresh_seeds(slot, mood))


# --- разметка треков ------------------------------------------------------------

def _tags_prompt(tracks: list[TrackInfo]) -> str:
    items = [{"ref": t.ref, "title": t.title, "artist": t.artist} for t in tracks]
    return (
        "Разметь треки для рекомендаций по времени суток. Для каждого: artist — настоящий исполнитель "
        "(поправь, если в названии канал или ошибка), energy — энергия от 1 (колыбельная, эмбиент) до "
        "5 (драйв, тяжёлый рок, танцевальное), tags — 2-5 тегов: жанр, язык (russian/english/...), "
        "instrumental, если без вокала.\n"
        f"{json.dumps(items, ensure_ascii=False)}\n"
        'Ответ — только JSON: {"tracks": [{"ref": "...", "artist": "...", "energy": 3, "tags": ["rock", "english"]}]}'
    )


async def tag_tracks(tracks: list[TrackInfo]) -> int:
    if not tracks:
        return 0
    text = await ask(_tags_prompt(tracks))
    tagged = parse_tags(text) if text else []
    known = {t.ref for t in tracks}
    for ref, artist, energy, tags in tagged:
        if ref in known:
            library.set_tags(ref, artist, energy, tags)
    return len(tagged)


async def energies_for(tracks: list[Track], timeout: float = 45.0) -> dict[str, int]:
    """Энергия кандидатов волны: известная — из библиотеки, остальных (до
    TAG_BATCH*2) разметит dj прямо сейчас, пачками параллельно (20 треков —
    10-17 с). Не успел — вернём, что знаем."""
    known = library.energies([t.ref for t in tracks])
    unknown = [t for t in tracks if t.ref not in known][: TAG_BATCH * 2]
    if unknown:
        for track in unknown:
            library.upsert(track)
        infos = [i for i in (library.track(t.ref) for t in unknown) if i is not None]
        batches = [infos[i:i + TAG_BATCH] for i in range(0, len(infos), TAG_BATCH)]
        try:
            await asyncio.wait_for(asyncio.gather(*(tag_tracks(b) for b in batches)), timeout)
        except Exception as exc:
            logger.warning("Не разметил кандидатов волны: %s", str(exc) or type(exc).__name__)
        known = library.energies([t.ref for t in tracks])
    return known


async def _tag_pending() -> None:
    global _tag_task
    await asyncio.sleep(TAG_DELAY)
    refs = list(_pending_tags)[:TAG_BATCH]
    _pending_tags.difference_update(refs)
    try:
        await tag_tracks([t for t in (library.track(r) for r in refs) if t is not None])
    finally:
        _tag_task = None
        if _pending_tags:
            _tag_task = _spawn(_tag_pending())


def tag_soon(ref: str) -> None:
    """Лайк → разметить трек; лайки подряд склеиваются в одну пачку."""
    global _tag_task
    _pending_tags.add(ref)
    if _tag_task is None:
        _tag_task = _spawn(_tag_pending())


async def tag_backlog() -> None:
    try:
        await tag_tracks(library.untagged_liked(TAG_BATCH))
    except Exception:
        logger.exception("Не разметил лайки")


# --- суточная сводка -------------------------------------------------------------

async def digest() -> None:
    digest_data = library.day_digest()
    if not digest_data["plays"] and not digest_data["summary"]["notes"]:
        return
    prompt = (
        "Сводка музыки хозяина за сутки (слот/настроение: трек → чем кончилось; finished — дослушал, "
        "skipped — пропустил, disliked — дизлайк) и общая картина вкуса:\n"
        f"{json.dumps(digest_data, ensure_ascii=False, indent=1)}\n\n"
        "Обнови свою память о его музыкальном вкусе: запиши устойчивые выводы (что любит в какое время, "
        "что не заходит), не разовые случайности и не дублируй уже известное. "
        "Ответь одной строкой, что запомнил."
    )
    answer = await ask(prompt, dj_only=True)
    if answer:
        logger.info("dj после сводки: %s", answer[:300])


async def run_digest_daily(at_hour: int = 4) -> None:
    """Задача lifespan: сводка раз в сутки в at_hour, заодно разметка лайков."""
    await asyncio.sleep(60)
    await tag_backlog()
    while True:
        now = time.localtime()
        wait = ((at_hour - now.tm_hour) % 24) * 3600 - now.tm_min * 60 - now.tm_sec
        await asyncio.sleep(wait if wait > 0 else wait + 86400)
        try:
            await digest()
            await tag_backlog()
        except Exception:
            logger.exception("Суточная сводка dj не удалась")
