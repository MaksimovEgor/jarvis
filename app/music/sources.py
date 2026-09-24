"""Источники музыки за одним фасадом: Яндекс → YouTube Music → YouTube → SoundCloud.

Плеер, волна и выходы не знают, откуда трек, — спрашивают здесь:

    resolve("кино группа крови") ─► Яндекс и YT Music параллельно
        ранжирование: (совпадение с запросом, округлено до 0.1) → приоритет источника
        ничего приличного → YouTube → SoundCloud
    download(track)              ─► Яндекс: MP3 320 в кэш / FLAC в лайки
                                    YouTube, SoundCloud: yt-dlp в кэш
    similar(track)               ─► Яндекс: станция track:<id>; YouTube: Mix

Совпадение — доля слов запроса в «исполнитель + название». Кавер, лайв,
ремикс, караоке, которых нет в запросе, — штраф: лучше точная песня с YouTube,
чем кавер из Яндекса.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Awaitable

from app.music import storage, yandex, youtube
from app.music.models import Kind, Service, Track

logger = logging.getLogger("jarvis.sources")

# Столько и выше — «это та песня»: дальше по списку источников не идём.
MATCH_THRESHOLD = 0.8
_PRIORITY: dict[Service, int] = {"yandex": 3, "ytmusic": 2, "youtube": 1, "soundcloud": 0}
SERVICE_LABEL: dict[Service, str] = {
    "yandex": "Яндекс Музыка", "ytmusic": "YouTube Music", "youtube": "YouTube", "soundcloud": "SoundCloud",
}
_VARIANTS = ("cover", "кавер", "live", "лайв", "концерт", "remix", "ремикс", "karaoke", "караоке",
             "instrumental", "инструментал", "acoustic", "акустик", "sped up", "slowed", "nightcore", "8d")
_WORD = re.compile(r"[\w']+")
_FILLER = {"песня", "песню", "трек", "музыка", "музыку", "группа", "группы", "the", "и", "a"}


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.casefold().replace("ё", "е")) if w not in _FILLER]


def match_score(query: str, track: Track) -> float:
    q = _words(query)
    if not q:
        return 0.0
    haystack = f"{track.artist or ''} {track.title}".casefold().replace("ё", "е")
    have = set(_words(haystack))
    score = sum(1 for w in q if w in have) / len(q)
    q_text = " ".join(q)
    if any(v in haystack and v not in q_text for v in _VARIANTS):
        score -= 0.3
    return max(0.0, score)


def _rank(query: str, tracks: list[Track]) -> list[Track]:
    return sorted(tracks, key=lambda t: (round(match_score(query, t), 1), _PRIORITY[t.service]), reverse=True)


async def _safe(coro: Awaitable[list[Track]], name: str) -> list[Track]:
    try:
        return await coro
    except Exception as exc:
        logger.warning("%s не ответил: %s", name, exc)
        return []


async def resolve(query: str, kind: Kind = "music") -> list[Track]:
    """Кандидаты, лучший первым. Книги и подкасты — только YouTube (длинное)."""
    if kind != "music":
        return await youtube.search(query, kind)
    ym, ytm = await asyncio.gather(
        _safe(yandex.search(query), "Яндекс"), _safe(youtube.music_search(query, limit=3), "YouTube Music"),
    )
    ranked = _rank(query, ym + ytm)
    if ranked and match_score(query, ranked[0]) >= MATCH_THRESHOLD:
        return ranked
    more = await _safe(youtube.search(query), "YouTube")
    ranked = _rank(query, ranked + more)
    if ranked and match_score(query, ranked[0]) >= MATCH_THRESHOLD:
        return ranked
    return _rank(query, ranked + await _safe(youtube.soundcloud_search(query), "SoundCloud"))


def is_song(track: Track) -> bool:
    """Песня, которую можно оценить, скачать и записать в журнал (не радио, не книга)."""
    return track.source in ("yandex", "soundcloud") or (track.source == "youtube" and not track.is_long)


async def download(track: Track, liked: bool = False, timeout: float = 180) -> Path:
    """Файл трека на диске. Яндекс: лайк — FLAC в liked/, иначе MP3 320 в кэш."""
    if track.source == "yandex":
        if path := storage.path_for(track.ref):
            return path
        folder = storage.liked_dir() if liked else storage.cache_dir()
        path = await asyncio.wait_for(yandex.download(track.ref, folder, "lossless" if liked else "mp3"), timeout)
        if not liked:
            storage.prune(keep=path)
        return path
    return await youtube.download(track, timeout=timeout)


async def save_liked(track: Track) -> None:
    """Лайк: Яндекс — докачать FLAC в liked/ и убрать MP3 из кэша; прочее —
    перенести файл из кэша в liked/ (скачав, если его нет)."""
    if track.source == "yandex":
        if storage.where(track.ref) == "liked":
            return
        cached = storage.path_for(track.ref)
        await download(track, liked=True)
        if cached is not None and cached.parent == storage.cache_dir():
            cached.unlink(missing_ok=True)
        storage.prune()
        return
    if not storage.pin(track.ref):
        await download(track)
        storage.pin(track.ref)


async def similar(track: Track) -> list[Track]:
    if track.source == "yandex":
        return await yandex.similar(track.ref)
    if track.source == "youtube":
        return await youtube.mix(track)
    return []
