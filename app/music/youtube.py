"""YouTube через yt-dlp: поиск, «микс» похожих треков и скачивание в кэш.

Почему не отдаём mpv прямую ссылку: googlevideo с asus напрямую не
открывается, а ffmpeg внутри mpv не умеет SOCKS. Поэтому yt-dlp качает
аудио через туннель в data/music/cache, а mpv играет локальный файл —
заодно повторное прослушивание работает офлайн. Трек на 6 минут качается
за ~3с.

Очередь «как у Алисы» строится из YouTube Mix (плейлист RD<id>): для
первого найденного трека YouTube сам подбирает ~25 похожих.

Длинное (книги, подкасты — часы звука) не качается целиком, а отдаётся
потоком (app/music/media.py): mpv и Chrome — /media/yt/<id>, прямая ссылка
googlevideo (stream_url) с проксированием Range; Safari — HLS
(/media/hls/<id>/index.m3u8, hls_segments): DASH-m4a, который отдаёт YouTube,
iPhone не играет («формат не поддерживается»), а HLS — родной. Ссылки
привязаны к IP, с которого их получили, — поэтому и получение, и чтение
идут через один и тот же YOUTUBE_PROXY.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

import aiohttp
from aiohttp_socks import ProxyConnector
from yarl import URL

from app.config import settings
from app.music import storage
from app.music.models import LONG_SECONDS, Kind, Track, clean_title

logger = logging.getLogger("jarvis.youtube")

# В выдаче поиска бывают каналы и плейлисты («Группа КИНО») — нужны только видео.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Длиннее — почти всегда часовые сборки и стримы, в очереди они не нужны.
MAX_TRACK_SECONDS = 15 * 60
MIN_TRACK_SECONDS = 60

_inflight: dict[str, asyncio.Task[Path]] = {}
# id → (исполнитель, длительность) из метаданных скачанного.
_meta: dict[str, tuple[str | None, float | None]] = {}
_TOPIC = " - Topic"
_CHANNEL_NOISE = re.compile(r"\s*(?:VEVO|Official|Official Channel|Music)$", re.IGNORECASE)
# id → (прямая ссылка, когда получена). googlevideo живёт ~6ч.
_stream_urls: dict[str, tuple[str, float]] = {}
STREAM_URL_TTL = 4 * 3600
_HLS_FORMAT = "ba[protocol=m3u8_native][format_note*=original]/ba[protocol=m3u8_native]/w[protocol=m3u8_native]"
# id → (сегменты HLS-плейлиста: длительность и ссылка, когда получен).
_hls: dict[str, tuple[list[tuple[str, str]], float]] = {}


def _js_runtime() -> str | None:
    """Без JS-рантайма yt-dlp не видит часть форматов (bestaudio пропадает).
    Под systemd в PATH нет ~/.local/bin, поэтому смотрим туда отдельно."""
    for name in ("deno", "node"):
        local = Path.home() / ".local/bin" / name
        found = shutil.which(name) or (str(local) if local.exists() else None)
        if found:
            return f"{name}:{found}"
    return None


def _base_args() -> list[str]:
    # Системный /usr/bin/yt-dlp слишком старый для текущего YouTube — берём из venv.
    args = [str(Path(sys.executable).with_name("yt-dlp")), "--no-warnings"]
    if settings.youtube_proxy:
        args += ["--proxy", settings.youtube_proxy]
    if runtime := _js_runtime():
        args += ["--js-runtimes", runtime]
    return args


async def _run(*args: str, timeout: float = 60) -> str:
    proc = await asyncio.create_subprocess_exec(
        *_base_args(), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        raise RuntimeError("yt-dlp не уложился по времени")
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp: {err.decode(errors='replace').strip()[-300:]}")
    return out.decode()


def _artist(channel: str, title: str) -> str | None:
    """«Foo Fighters - Topic» → Foo Fighters; канал, если он есть в названии
    («In The End - Linkin Park» у канала Linkin Park); иначе «Кино - Группа
    крови» → Кино; иначе канал без «VEVO/Official»."""
    if channel.endswith(_TOPIC):
        return channel[: -len(_TOPIC)].strip() or None
    clean = _CHANNEL_NOISE.sub("", channel).strip() if channel and channel != "NA" else ""
    if clean and clean.casefold() in title.casefold():
        return clean
    if " - " in title:
        return title.split(" - ", 1)[0].strip() or None
    return clean or None


async def _list(
    url: str, limit: int, kind: Kind = "music", timeout: float = 60,
    service: Literal["youtube", "ytmusic"] = "youtube",
) -> list[Track]:
    out = await _run(
        "--flat-playlist", "--playlist-end", str(limit),
        "--print", "%(id)s\t%(duration)s\t%(channel)s\t%(title)s", url,
        timeout=timeout,
    )
    tracks = []
    for line in out.splitlines():
        video_id, duration, channel, title = (line.split("\t", 3) + ["", "", ""])[:4]
        if not _VIDEO_ID.match(video_id):
            continue
        try:
            seconds: float | None = float(duration)
        except ValueError:
            seconds = None
        if kind == "music":
            title = clean_title(title)
        tracks.append(Track(
            title=title, source="youtube", ref=video_id, duration=seconds, kind=kind,
            artist=_artist(channel, title) if kind == "music" else None, service=service,
        ))
    return tracks


def _fits(track: Track) -> bool:
    return track.duration is None or MIN_TRACK_SECONDS <= track.duration <= MAX_TRACK_SECONDS


async def search(query: str, kind: Kind = "music") -> list[Track]:
    """Кандидаты, лучший первым. Музыка — не часовые сборки: «включи Queen»
    иначе легко попадает на «Greatest Hits 1 hour». Книга/подкаст — наоборот,
    сначала достаточно длинные (иначе попадаются трейлеры и отрывки)."""
    if kind == "audiobook" and "аудиокниг" not in query.lower():
        # Иначе по названию книги первыми идут экранизации и разборы.
        query = f"{query} аудиокнига"
    results = await _list(f"ytsearch5:{query}", 5, kind)
    good = (lambda t: _fits(t)) if kind == "music" else (lambda t: (t.duration or 0) > LONG_SECONDS)
    return [t for t in results if good(t)] + [t for t in results if not good(t)]


async def mix(seed: Track, limit: int = 25) -> list[Track]:
    """Похожие треки после seed (сам seed исключён)."""
    tracks = await _list(f"https://www.youtube.com/watch?v={seed.ref}&list=RD{seed.ref}", limit)
    return [t for t in tracks if t.ref != seed.ref and _fits(t)]


# Волна (app/music/wave.py) берёт музыку из YouTube Music: там песни, а не
# клипы, лайвы и «1 hour loop». Короткие таймауты — волна не ждёт.
MUSIC_TIMEOUT = 25.0


async def music_search(query: str, limit: int = 8) -> list[Track]:
    """Песни YouTube Music. У выдачи нет исполнителя и длительности — они
    появятся при скачивании (meta)."""
    url = f"https://music.youtube.com/search?q={quote_plus(query)}#songs"
    return [t for t in await _list(url, limit, timeout=MUSIC_TIMEOUT, service="ytmusic") if _fits(t)]


async def music_radio(seed_ref: str, limit: int = 25) -> list[Track]:
    """Радио YouTube Music по треку — «похожее» для волны (seed исключён)."""
    url = f"https://music.youtube.com/watch?v={seed_ref}&list=RDAMVM{seed_ref}"
    tracks = await _list(url, limit, timeout=MUSIC_TIMEOUT, service="ytmusic")
    return [t for t in tracks if t.ref != seed_ref and _fits(t)]


async def _download(video_id: str, timeout: float, url: str | None = None) -> Path:
    """video_id — имя файла в кэше (для SoundCloud «sc-<id>»), url — откуда
    качать, если это не видео YouTube."""
    if path := storage.path_for(video_id):
        return path
    cache = storage.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    # after_move-печать не отменяет скачивание, а метаданные (исполнитель)
    # в выдаче поиска YouTube Music бывают пустыми — берём их отсюда.
    out = await _run(
        "-f", "bestaudio[ext=m4a]/bestaudio", "--no-playlist", "--no-progress",
        "--print", "after_move:%(artist)s\t%(channel)s\t%(duration)s\t%(title)s",
        "-o", str(cache / f"{video_id}.%(ext)s"),
        url or f"https://www.youtube.com/watch?v={video_id}",
        timeout=timeout,
    )
    _remember_meta(video_id, out)
    path = storage.path_for(video_id)
    if path is None:
        raise RuntimeError("yt-dlp ничего не скачал")
    storage.prune(keep=path)
    return path


def _remember_meta(video_id: str, out: str) -> None:
    line = out.strip().splitlines()[-1] if out.strip() else ""
    artist, channel, duration, title = (line.split("\t", 3) + ["", "", "", ""])[:4]
    artist = artist.split(",")[0].strip() if artist not in ("", "NA") else _artist(channel, title)
    try:
        seconds: float | None = float(duration)
    except ValueError:
        seconds = None
    _meta[video_id] = (artist, seconds)


def meta(video_id: str) -> tuple[str | None, float | None]:
    """Исполнитель и длительность, узнанные при скачивании (или (None, None))."""
    return _meta.get(video_id, (None, None))


async def download(track: Track, timeout: float = 180) -> Path:
    """Параллельные запросы одного трека (предзагрузка + «следующий»)
    ждут одну и ту же загрузку."""
    task = _inflight.get(track.ref)
    if task is None:
        task = asyncio.create_task(_download(track.ref, timeout, track.url))
        _inflight[track.ref] = task
        task.add_done_callback(lambda _: _inflight.pop(track.ref, None))
    return await asyncio.shield(task)


async def soundcloud_search(query: str, limit: int = 3) -> list[Track]:
    """SoundCloud — последний запасной источник (ремиксы, андеграунд)."""
    out = await _run(
        "--flat-playlist", "--playlist-end", str(limit),
        "--print", "%(id)s\t%(duration)s\t%(uploader)s\t%(webpage_url)s\t%(title)s",
        f"scsearch{limit}:{query}", timeout=MUSIC_TIMEOUT,
    )
    tracks = []
    for line in out.splitlines():
        sc_id, duration, uploader, url, title = (line.split("\t", 4) + ["", "", "", ""])[:5]
        if not sc_id.isdigit() or not url.startswith("http"):
            continue
        try:
            seconds: float | None = float(duration)
        except ValueError:
            seconds = None
        track = Track(title=clean_title(title), source="soundcloud", ref=f"sc-{sc_id}", duration=seconds,
                      artist=_artist(uploader, title), service="soundcloud", url=url)
        if _fits(track):
            tracks.append(track)
    return tracks


def cached(track: Track) -> Path | None:
    return storage.path_for(track.ref) if track.source in ("youtube", "soundcloud") else None


async def stream_url(video_id: str, refresh: bool = False) -> str:
    url, fetched = _stream_urls.get(video_id, ("", 0.0))
    if refresh or not url or time.time() - fetched > STREAM_URL_TTL:
        out = await _run("-f", "bestaudio[ext=m4a]/bestaudio", "-g", "--no-playlist",
                         f"https://www.youtube.com/watch?v={video_id}")
        url = out.strip().splitlines()[0]
        _stream_urls[video_id] = (url, time.time())
    return url


async def fetch_bytes(url: str, proxy: bool = True) -> bytes:
    use = proxy and settings.youtube_proxy
    connector = ProxyConnector.from_url(settings.youtube_proxy, rdns=True) if use else None
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def _fetch_text(url: str) -> str:
    # rdns и encoded=True — см. media._relay: иначе ссылки сегментов дают 403.
    connector = ProxyConnector.from_url(settings.youtube_proxy, rdns=True) if settings.youtube_proxy else None
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(URL(url, encoded=True)) as resp:
            resp.raise_for_status()
            return await resp.text()


async def hls_segments(video_id: str, refresh: bool = False) -> list[tuple[str, str]]:
    """[(строка #EXTINF, ссылка на сегмент)] звуковой HLS-дорожки.

    Форматы по протоколу, а не по itag: у многоязычных видео они называются
    234-0/234-1 (дорожки языков), «234/233» их не находил. Предпочтение —
    оригинальной дорожке, запасной вариант — обычный HLS с видео (Safari
    в <audio> играет из него только звук)."""
    segments, fetched = _hls.get(video_id, ([], 0.0))
    if refresh or not segments or time.time() - fetched > STREAM_URL_TTL:
        playlist_url = (await _run("-f", _HLS_FORMAT, "-g", "--no-playlist",
                                   f"https://www.youtube.com/watch?v={video_id}")).strip().splitlines()[0]
        lines = (await _fetch_text(playlist_url)).splitlines()
        segments = [(lines[i], lines[i + 1]) for i in range(len(lines) - 1) if lines[i].startswith("#EXTINF")]
        if not segments:
            raise RuntimeError("пустой HLS-плейлист")
        _hls[video_id] = (segments, time.time())
    return segments
