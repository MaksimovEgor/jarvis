"""Яндекс Музыка — основной источник: качество (MP3 320 / FLAC) и «Моя волна».

Клиент — pip `yandex-music` (неофициальный, MarshalX). api.music.yandex.net
доступен с asus напрямую, без прокси.

Вход — кодом устройства, как на телевизоре: connect() берёт код, ты вводишь
его на ya.ru/device, фоновая задача опрашивает Яндекс до подтверждения.
Токен — в data/yandex_token.json (600), не в .env: он появляется из интерфейса.

    off ──connect()──► pending (код на экране) ──подтвердил──► on
     ▲                        └── код истёк ──► off
     └── disconnect()          on ── токен отозван/протух ──► broken
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from yandex_music import ClientAsync
from yandex_music.utils.sign_request import DEFAULT_SIGN_KEY

from app.config import settings
from app.music.models import Rating, Track, WaveSpec

logger = logging.getLogger("jarvis.yandex")

YM_PREFIX = "ym-"
State = Literal["off", "pending", "on", "broken"]
DEVICE_NAME = "Джарвис"


@dataclass(frozen=True)
class Status:
    state: State
    login: str | None = None
    plus: bool = False
    code: str | None = None
    url: str | None = None
    expires_at: float | None = None


_client: ClientAsync | None = None
_status = Status("off")
_poll_task: asyncio.Task[None] | None = None


def ref_of(track_id: int | str) -> str:
    return f"{YM_PREFIX}{str(track_id).split(':')[0]}"


def track_id(ref: str) -> str:
    return ref.removeprefix(YM_PREFIX)


def is_ref(ref: str) -> bool:
    return ref.startswith(YM_PREFIX)


def _token_file() -> Path:
    return Path(settings.yandex_token_file)


def _save_token(data: dict[str, Any]) -> None:
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    path.chmod(0o600)


def is_on() -> bool:
    return _status.state == "on" and _client is not None


def status() -> Status:
    return _status


def client() -> ClientAsync:
    if _client is None:
        raise RuntimeError("Яндекс Музыка не подключена")
    return _client


async def _login(token: str) -> None:
    """Проверить токен и узнать аккаунт; ошибка — broken (токен отозван)."""
    global _client, _status
    candidate = ClientAsync(token)
    try:
        await candidate.init()
        account = await candidate.account_status()
    except Exception as exc:
        logger.warning("Яндекс Музыка: токен не принят (%s)", exc)
        _client, _status = None, Status("broken")
        return
    login = getattr(getattr(account, "account", None), "login", None)
    plus = bool(getattr(getattr(account, "plus", None), "has_plus", False))
    _client, _status = candidate, Status("on", login=login, plus=plus)
    logger.info("Яндекс Музыка подключена: %s, Плюс: %s", login, plus)


async def start() -> None:
    """Lifespan: поднять сохранённый токен."""
    path = _token_file()
    if not path.exists():
        return
    try:
        token = json.loads(path.read_text())["token"]
    except (ValueError, KeyError):
        logger.error("Испорчен %s — переподключи Яндекс", path)
        return
    await _login(token)


async def connect() -> Status:
    """Код устройства для ya.ru/device и фоновый опрос до подтверждения."""
    global _status, _poll_task
    if _status.state == "pending" and _status.expires_at and _status.expires_at > time.time():
        return _status
    code = await ClientAsync().request_device_code(device_name=DEVICE_NAME)
    _status = Status("pending", code=code.user_code, url=code.verification_url,
                     expires_at=time.time() + code.expires_in)
    if _poll_task is not None:
        _poll_task.cancel()
    _poll_task = asyncio.create_task(_poll(code.device_code, code.interval, code.expires_in))
    return _status


async def _poll(device_code: str, interval: float, expires_in: float) -> None:
    global _status
    deadline = time.time() + expires_in
    probe = ClientAsync()
    while time.time() < deadline:
        await asyncio.sleep(max(interval, 2))
        try:
            token = await probe.poll_device_token(device_code)
        except Exception as exc:
            logger.warning("Яндекс отклонил вход по коду: %s", exc)
            break
        if token is None:
            continue  # ещё не подтвердили
        _save_token({"token": token.access_token, "refresh_token": token.refresh_token,
                     "connected_at": time.time()})
        await _login(token.access_token)
        if is_on():
            _save_token({"token": token.access_token, "refresh_token": token.refresh_token,
                         "connected_at": time.time(), "login": _status.login, "plus": _status.plus})
            asyncio.create_task(_after_connect())
        return
    if _status.state == "pending":
        _status = Status("off")


async def _after_connect() -> None:
    """Сразу после входа — лайки Яндекса в «Мою музыку»."""
    from app.music.library import library  # library → models; здесь без цикла, но лениво

    try:
        added = library.import_likes(await liked_tracks())
        logger.info("Яндекс: импортировано лайков: %d", added)
    except Exception:
        logger.exception("Не импортировал лайки Яндекса")


async def sync_likes_daily() -> None:
    """Задача lifespan: раз в сутки подтягивать новые лайки из приложения Яндекса."""
    while True:
        await asyncio.sleep(24 * 3600)
        if is_on():
            await _after_connect()


# --- треки -----------------------------------------------------------------

def _cover_url(uri: str | None, size: str = "400x400") -> str | None:
    return f"https://{uri.replace('%%', size)}" if uri else None


def to_track(t: Any, origin: Any = "query") -> Track | None:
    """Трек библиотеки yandex-music → наш Track. Недоступный (нет прав) — None."""
    if t is None or t.available is False or not t.id:
        return None
    title = t.title or "?"
    if t.version:
        title = f"{title} ({t.version})"
    artist = ", ".join(a.name for a in t.artists or [] if a.name) or None
    return Track(
        title=title, source="yandex", ref=ref_of(t.id), duration=(t.duration_ms or 0) / 1000 or None,
        artist=artist, origin=origin, service="yandex", cover=_cover_url(t.cover_uri or t.og_image),
    )


async def search(query: str, limit: int = 3) -> list[Track]:
    if not is_on():
        return []
    result = await client().search(query, type_="track")
    found = result.tracks.results if result and result.tracks else []
    return [t for t in (to_track(x) for x in found[:limit * 2]) if t is not None][:limit]


async def tracks(refs: list[str]) -> list[Track]:
    if not is_on() or not refs:
        return []
    full = await client().tracks([track_id(r) for r in refs])
    return [t for t in (to_track(x) for x in full) if t is not None]


# --- лайки ---------------------------------------------------------------------

async def set_like(ref: str, value: Rating | None, before: Rating | None) -> None:
    """Оценка в Джарвисе → та же оценка в Яндексе (вкус один на оба приложения)."""
    if not is_on() or not is_ref(ref):
        return
    c, tid = client(), track_id(ref)
    try:
        if before == 1 and value != 1:
            await c.users_likes_tracks_remove(tid)
        if before == -1 and value != -1:
            await c.users_dislikes_tracks_remove(tid)
        if value == 1:
            await c.users_likes_tracks_add(tid)
        elif value == -1:
            await c.users_dislikes_tracks_add(tid)
    except Exception as exc:
        logger.warning("Яндекс не принял оценку %s: %s", ref, exc)


async def liked_tracks() -> list[Track]:
    likes = await client().users_likes_tracks()
    if likes is None:
        return []
    full = await likes.fetch_tracks_async()
    return [t for t in (to_track(x, "liked") for x in full) if t is not None]


# --- волна и станции --------------------------------------------------------------

_stations: dict[str, str] = {}
_stations_at = 0.0


async def stations() -> dict[str, str]:
    """id станции → русское название (жанры, эпохи, занятия, настроения).
    Каталог отдаётся и без входа; держим сутки."""
    global _stations, _stations_at
    if _stations and time.time() - _stations_at < 86400:
        return _stations
    c = _client or await ClientAsync().init()
    found = {}
    for item in await c.rotor_stations_list():
        sid = f"{item.station.id.type}:{item.station.id.tag}"
        found[sid] = item.station.name
    _stations, _stations_at = found, time.time()
    return found


# Волна — сессионный API, как у нынешних приложений Яндекса: настройки идут
# «зёрнами» (settingMoodEnergy:sad …). Старый rotor/station/*/settings3 отвечает
# 415, а radioStarted — «condition is not met».
_API = "https://api.music.yandex.net"


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"OAuth {client().token}", "X-Yandex-Music-Client": "YandexMusicAndroid/24023621"}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(f"{_API}/{path}", json=body) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"{path}: {resp.status} {data}")
    return data.get("result", data)


def seeds(spec: WaveSpec) -> list[str]:
    out = [spec.station]
    if spec.mood_energy != "all":
        out.append(f"settingMoodEnergy:{spec.mood_energy}")
    if spec.diversity != "default":
        out.append(f"settingDiversity:{spec.diversity}")
    if spec.language != "any":
        out.append(f"settingLanguage:{spec.language}")
    return out


@dataclass
class WaveCursor:
    """Сессия «Моей волны» одного плеера: id сессии, последняя партия, что уже дали."""
    spec: WaveSpec
    session_id: str | None = None
    batch_id: str | None = None
    queue: list[str] | None = None


def _tracks_of(result: dict[str, Any], origin: Any) -> list[tuple[str, Track]]:
    from yandex_music import Track as YMTrack

    out = []
    for item in result.get("sequence") or []:
        raw = item.get("track") or {}
        track = to_track(YMTrack.de_json(raw, client()), origin)
        if track is not None:
            album = (raw.get("albums") or [{}])[0].get("id")
            out.append((f"{raw.get('id')}:{album}" if album else str(raw.get("id")), track))
    return out


async def wave_tracks(cursor: WaveCursor) -> list[Track]:
    """Следующие треки волны с настройками (станция + настроение, характер, язык)."""
    if cursor.session_id is None:
        result = await _post("rotor/session/new", {"seeds": seeds(cursor.spec), "includeTracksInResponse": True})
        logger.info("Волна Яндекса %s: принято %s", seeds(cursor.spec), result.get("acceptedSeeds"))
        cursor.session_id = result.get("radioSessionId")
    else:
        result = await _post(f"rotor/session/{cursor.session_id}/tracks", {"queue": (cursor.queue or [])[-5:]})
    cursor.batch_id = result.get("batchId") or cursor.batch_id
    pairs = _tracks_of(result, "wave")
    cursor.queue = (cursor.queue or []) + [pid for pid, _ in pairs]
    return [t for _, t in pairs]


async def feedback(cursor: WaveCursor, kind: Literal["started", "finished", "skip"],
                   ref: str, played: float = 0.0) -> None:
    """Сигналы волне Яндекса — она учится на том, что слушают в Джарвисе."""
    if not is_on() or not is_ref(ref) or cursor.session_id is None:
        return
    event: dict[str, Any] = {
        "type": {"started": "trackStarted", "finished": "trackFinished", "skip": "skip"}[kind],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "trackId": track_id(ref),
    }
    if kind != "started":
        event["totalPlayedSeconds"] = round(played, 1)
    try:
        await _post(f"rotor/session/{cursor.session_id}/feedback", {"event": event, "batchId": cursor.batch_id})
    except Exception as exc:
        logger.warning("Яндекс не принял фидбек %s %s: %s", kind, ref, exc)


async def similar(ref: str, limit: int = 20) -> list[Track]:
    """«Похожие треки» Яндекса; пусто — волна по треку."""
    found = await client().tracks_similar(track_id(ref))
    tracks = [t for t in (to_track(x, "mix") for x in (found.similar_tracks if found else []) or []) if t is not None]
    if not tracks:
        tracks = await wave_tracks(WaveCursor(WaveSpec(station=f"track:{track_id(ref)}")))
    return [t for t in tracks if t.ref != ref][:limit]


# --- скачивание ---------------------------------------------------------------

Quality = Literal["lossless", "mp3"]
# Ключи подписи get-file-info: десктопного клиента (им пользуются сторонние
# загрузчики) и Android — из самой библиотеки. Сменит Яндекс — будет MP3 320.
_FILE_INFO_KEYS = ("kzqU4XhfCaY6B6JTHODeq5", DEFAULT_SIGN_KEY)
_LOSSLESS_CODECS = "flac,flac-mp4,aac-mp4,aac,mp3"
_EXT = {"flac": ".flac", "flac-mp4": ".flac", "mp3": ".mp3", "aac": ".m4a", "aac-mp4": ".m4a", "he-aac": ".m4a", "he-aac-mp4": ".m4a"}


def _sign(key: str, params: dict[str, Any]) -> str:
    message = "".join(str(v) for v in params.values()).replace(",", "")
    digest = hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()[:-1]


async def _file_info(tid: str) -> dict[str, Any]:
    """Ссылка на lossless (или лучшее, что есть) — подписанный get-file-info."""
    last: Exception | None = None
    for key in _FILE_INFO_KEYS:
        params: dict[str, Any] = {
            "ts": int(time.time()), "trackId": tid, "quality": "lossless",
            "codecs": _LOSSLESS_CODECS, "transports": "encraw",
        }
        params["sign"] = _sign(key, params)
        try:
            result = await client()._request.get(f"{client().base_url}/get-file-info", params)
        except Exception as exc:
            last = exc
            continue
        info = (result or {}).get("download_info") or (result or {}).get("downloadInfo")
        if info:
            return info
    raise RuntimeError(f"get-file-info не ответил: {last}")


def decrypt(data: bytes, key_hex: str) -> bytes:
    """transport=encraw: AES-128/256-CTR, нулевой счётчик."""
    decryptor = Cipher(algorithms.AES(bytes.fromhex(key_hex)), modes.CTR(bytes(16))).decryptor()
    return decryptor.update(data) + decryptor.finalize()


async def _fetch(url: str) -> bytes:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def _remux(src: Path, dest: Path) -> None:
    """flac в mp4-контейнере → чистый .flac (так его играет Safari на iPhone)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:a", "-c:a", "copy", str(dest),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), 120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {err.decode(errors='replace')[-200:]}")


async def _download_lossless(tid: str, folder: Path, name: str) -> Path:
    info = await _file_info(tid)
    codec = info.get("codec", "")
    url = info.get("url") or (info.get("urls") or [None])[0]
    if not url or codec not in _EXT:
        raise RuntimeError(f"get-file-info: нет ссылки или странный кодек {codec!r}")
    data = await _fetch(url)
    if info.get("transport") == "encraw" and info.get("key"):
        data = decrypt(data, info["key"])
    dest = folder / f"{name}{_EXT[codec]}"
    if codec.endswith("-mp4"):
        raw = folder / f"{name}.part.mp4"
        raw.write_bytes(data)
        try:
            if codec == "flac-mp4":
                await _remux(raw, dest)
            else:
                raw.rename(dest)
        finally:
            raw.unlink(missing_ok=True)
    else:
        dest.write_bytes(data)
    logger.info("Яндекс %s: %s, %d КБ", tid, codec, len(data) // 1024)
    return dest


async def _download_mp3(tid: str, folder: Path, name: str) -> Path:
    infos = await client().tracks_download_info(tid, get_direct_links=True)
    mp3 = [i for i in infos if i.codec == "mp3"] or infos
    if not mp3:
        raise RuntimeError("Яндекс не дал ссылок на скачивание")
    best = max(mp3, key=lambda i: i.bitrate_in_kbps or 0)
    dest = folder / f"{name}{_EXT.get(best.codec, '.mp3')}"
    dest.write_bytes(await _fetch(best.direct_link or await best.get_direct_link_async()))
    return dest


async def download(ref: str, folder: Path, quality: Quality) -> Path:
    """lossless — FLAC, если Яндекс отдаст (иначе MP3 320); mp3 — сразу MP3 320."""
    folder.mkdir(parents=True, exist_ok=True)
    tid = track_id(ref)
    if quality == "lossless":
        try:
            return await _download_lossless(tid, folder, ref)
        except Exception as exc:
            logger.warning("FLAC для %s не вышел (%s) — беру MP3 320", ref, exc)
    return await _download_mp3(tid, folder, ref)


async def disconnect() -> None:
    global _client, _status, _poll_task
    if _poll_task is not None:
        _poll_task.cancel()
        _poll_task = None
    _token_file().unlink(missing_ok=True)
    _client, _status = None, Status("off")
