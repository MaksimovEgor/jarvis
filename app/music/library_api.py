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

from app.music import catalog, devices, lyrics, storage, yandex
from app.music.library import TrackInfo, library
from app.music.models import Diversity, Entity, EntityType, Language, Mood, MoodEnergy, Rating, SearchTab, WaveSpec
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


class EntityIn(BaseModel):
    """Результат поиска, который прислал экран обратно — «включи это»."""
    source: Literal["yandex", "youtube", "soundcloud"]
    type: EntityType
    id: str
    title: str = ""


class PlayRequest(BaseModel):
    device: str
    mode: Literal["wave", "liked", "track", "entity", "next"]
    mood: Mood = "auto"
    ref: str | None = None
    wave: WaveSettings | None = None
    entity: EntityIn | None = None


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
    elif req.mode == "entity" and req.entity:
        text = await player.play_entity(Entity(**req.entity.model_dump()))
    elif req.mode == "next" and req.entity:
        tracks, _ = await catalog.resolve_entity(Entity(**req.entity.model_dump()))
        text = await player.add_next(tracks[0]) if tracks else "Не нашёл этот трек."
    else:
        text = await player.play_ref(req.ref or "")
    return {"status": "ok", "text": text}


def _entity_out(e: Entity) -> dict:
    return {
        "source": e.source, "type": e.type, "id": e.id, "title": e.title, "subtitle": e.subtitle,
        "cover": e.cover, "coverCrop": e.cover_crop,
        "rating": library.rating(e.id) if e.type == "track" else None,
    }


@router.get("/search")
async def search(q: str = "", tab: SearchTab = "yandex") -> dict:
    try:
        sections = await catalog.search(q, tab)
    except Exception as exc:
        return {"sections": [], "error": str(exc)[:200]}
    return {"sections": [{"kind": s.kind, "title": s.title, "items": [_entity_out(e) for e in s.items]}
                         for s in sections]}


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


@router.get("/lyrics")
async def track_lyrics(ref: str) -> dict:
    """Текст трека: synced — [{t, line}] для подсветки, иначе plain."""
    info = library.track(ref)
    if info is None:
        return {"synced": None, "plain": None, "source": None}
    found = await lyrics.get(info.track())
    return {
        "synced": [{"t": t, "line": line} for t, line in found.synced] if found.synced else None,
        "plain": found.plain, "source": found.source,
    }


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
