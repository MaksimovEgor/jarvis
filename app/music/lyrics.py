"""Тексты песен, как в Яндекс Музыке: построчно с таймингом.

    трек Яндекса ─► tracks_lyrics(LRC) ─ нет ─► LRCLIB /api/get ─ нет ─► /api/search
    остальное    ─────────────────────────────► LRCLIB (открытая база, без ключа)

Синхронный текст — LRC ([мм:сс.сс] строка), экран подсвечивает строку по
позиции <audio> сам. Результат (и «текста нет») кэшируется на диске:
data/music/lyrics/<ref>.json — мелкие файлы, вне лимита 10 ГБ.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import aiohttp

from app.config import settings
from app.music import yandex
from app.music.models import Track, split_title

logger = logging.getLogger("jarvis.lyrics")

LRCLIB = "https://lrclib.net/api"
# «Текста нет» перепроверяем раз в сутки — вдруг появится.
MISS_TTL = 86400
_STAMP = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


@dataclass(frozen=True)
class Lyrics:
    synced: list[tuple[float, str]] | None = None
    plain: str | None = None
    source: Literal["yandex", "lrclib"] | None = None


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """[00:10.50][01:20.00]припев → две строки; метаданные ([ar:…]) и пустые метки
    пропускаем, пустая строка с меткой — пауза (показывается как «♪»)."""
    lines: list[tuple[float, str]] = []
    for raw in text.splitlines():
        stamps = _STAMP.findall(raw)
        if not stamps:
            continue
        line = _STAMP.sub("", raw).strip()
        for minutes, seconds in stamps:
            lines.append((int(minutes) * 60 + float(seconds.replace(":", ".")), line))
    return sorted(lines, key=lambda x: x[0])


def _cache(ref: str) -> Path:
    return Path(settings.music_cache_dir).parent / "lyrics" / f"{ref}.json"


def _load(ref: str) -> Lyrics | None:
    path = _cache(ref)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("source") is None and time.time() - data.get("fetched_at", 0) > MISS_TTL:
        return None
    synced = [(float(t), line) for t, line in data["synced"]] if data.get("synced") else None
    return Lyrics(synced, data.get("plain"), data.get("source"))


def _save(ref: str, lyrics: Lyrics) -> None:
    path = _cache(ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**asdict(lyrics), "fetched_at": time.time()}, ensure_ascii=False))


async def _lrclib_request(path: str, params: dict[str, Any]) -> Any:
    headers = {"User-Agent": "Jarvis home speaker (github.com/lrclib)"}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(f"{LRCLIB}/{path}", params=params) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.json(content_type=None)


def _from_lrclib(item: dict[str, Any] | None) -> Lyrics | None:
    if not item:
        return None
    synced = parse_lrc(item["syncedLyrics"]) if item.get("syncedLyrics") else None
    plain = item.get("plainLyrics") or None
    return Lyrics(synced or None, plain, "lrclib") if synced or plain else None


async def _lrclib(artist: str, title: str, duration: float | None) -> Lyrics | None:
    params: dict[str, Any] = {"artist_name": artist, "track_name": title}
    if duration:
        params["duration"] = round(duration)
    if found := _from_lrclib(await _lrclib_request("get", params)):
        return found
    # Не совпала длительность или написание — поиск, лучший с синхронным текстом.
    results = await _lrclib_request("search", {"q": f"{artist} {title}"}) or []
    best = next((r for r in results if r.get("syncedLyrics")), results[0] if results else None)
    return _from_lrclib(best)


async def get(track: Track) -> Lyrics:
    if cached := _load(track.ref):
        return cached
    result: Lyrics | None = None
    if track.source == "yandex" and yandex.is_on():
        try:
            lrc, text = await yandex.lyrics(track.ref)
            if lrc or text:
                result = Lyrics(parse_lrc(lrc) if lrc else None, text, "yandex")
        except Exception as exc:
            logger.warning("Яндекс не дал текст %s: %s", track.ref, exc)
    if result is None:
        song, artist = split_title(track.title, track.artist)
        if artist:
            try:
                result = await _lrclib(artist, song, track.duration)
            except Exception as exc:
                logger.warning("LRCLIB не ответил про «%s»: %s", track.title, exc)
                return Lyrics()  # сеть — не кэшируем «нет текста»
    result = result or Lyrics()
    _save(track.ref, result)
    return result
