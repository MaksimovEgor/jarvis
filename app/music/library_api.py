"""«Моя музыка» для веба: оценки, лайки, скрытое, запуск, место, метрика.

    POST /library/rate     — {device, ref?, value}: без ref — текущий трек на
                             устройстве; dislike текущего сразу переключает дальше
    POST /library/play     — {device, mode: wave|liked|track, mood?, ref?}
    GET  /library/likes    — лайки, новые сверху (offset, limit, q)
    GET  /library/hidden   — дизлайкнутые треки и приглушённые исполнители
    POST /library/unmute   — {artist}: снять все дизлайки исполнителя («Вернуть»)
    GET  /library/storage  — сколько заняли лайки и кэш из лимита
    GET  /library/stats    — метрика волны за days дней
    POST /library/yandex/connect     — код устройства для ya.ru/device
    GET  /library/yandex/status      — off | pending (код) | on (логин, Плюс) | broken
    POST /library/yandex/disconnect  — забыть токен

Снаружи — через Caddy (basic auth), как /player/*.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.music import devices, storage, yandex
from app.music.library import TrackInfo, library
from app.music.models import Diversity, Language, Mood, MoodEnergy, Rating, WaveSpec
from app.music.wave import MOOD_PRESET, spec_mood
from app.music.web_player import _web_device

router = APIRouter(prefix="/library")

_VALUES: dict[str, Rating | None] = {"like": 1, "dislike": -1, "none": None}


class RateRequest(BaseModel):
    device: str
    ref: str | None = None
    value: Literal["like", "dislike", "none"]


class UnmuteRequest(BaseModel):
    artist: str


class WaveSettings(BaseModel):
    """Настройки волны с экрана — как в «Моей волне» Яндекса."""
    station: str = "user:onyourwave"
    mood_energy: MoodEnergy = "all"
    diversity: Diversity = "default"
    language: Language = "any"
    label: str = ""


class PlayRequest(BaseModel):
    device: str
    mode: Literal["wave", "liked", "track"]
    mood: Mood = "auto"
    ref: str | None = None
    wave: WaveSettings | None = None


def _out(info: TrackInfo) -> dict:
    return {
        "ref": info.ref, "title": info.title, "artist": info.artist, "duration": info.duration,
        "addedAt": info.added_at, "rating": info.rating,
    }


@router.post("/rate")
async def rate(req: RateRequest) -> dict:
    player = devices.player_for(_web_device(req.device))
    value = _VALUES[req.value]
    text = await player.rate(value, req.ref)
    return {"status": "ok", "text": text, "rating": value}


@router.post("/play")
async def play(req: PlayRequest) -> dict:
    player = devices.player_for(_web_device(req.device))
    if req.mode == "wave":
        spec = WaveSpec(**req.wave.model_dump()) if req.wave else MOOD_PRESET[req.mood]
        text = await player.play_wave(spec_mood(spec), spec=spec)
    elif req.mode == "liked":
        text = await player.play_liked()
    else:
        text = await player.play_ref(req.ref or "")
    return {"status": "ok", "text": text}


@router.get("/likes")
async def likes(offset: int = 0, limit: int = 50, q: str | None = None) -> dict:
    return {
        "total": library.liked_count(),
        "tracks": [_out(t) for t in library.liked(offset, min(limit, 200), q or None)],
    }


@router.get("/hidden")
async def hidden() -> dict:
    return {
        "tracks": [_out(t) for t in library.disliked()],
        "artists": [{"artist": a, "dislikes": n} for a, n in library.disliked_artists()],
    }


@router.post("/unmute")
async def unmute(req: UnmuteRequest) -> dict:
    return {"status": "ok", "restored": library.unmute_artist(req.artist)}


@router.get("/storage")
async def storage_usage() -> dict:
    usage = storage.usage()
    return {"likedMb": usage.liked_mb, "cacheMb": usage.cache_mb, "limitMb": usage.limit_mb}


@router.get("/stats")
async def stats(days: int = 7) -> dict:
    return asdict(library.stats(days))


def _yandex(status: yandex.Status) -> dict:
    return {
        "state": status.state, "login": status.login, "plus": status.plus,
        "code": status.code, "url": status.url, "expiresAt": status.expires_at,
    }


@router.get("/yandex/status")
async def yandex_status() -> dict:
    return _yandex(yandex.status())


@router.post("/yandex/connect")
async def yandex_connect() -> dict:
    return _yandex(await yandex.connect())


@router.get("/yandex/stations")
async def yandex_stations() -> dict:
    """Станции «Моей волны» для чипов: занятия, настроения, эпохи, жанры."""
    try:
        catalog = await yandex.stations()
    except Exception:
        return {"stations": []}
    return {"stations": [{"id": sid, "name": name} for sid, name in catalog.items()]}


@router.post("/yandex/disconnect")
async def yandex_disconnect() -> dict:
    await yandex.disconnect()
    return _yandex(yandex.status())
