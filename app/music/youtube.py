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

import aiohttp
from aiohttp_socks import ProxyConnector
from yarl import URL

from app.config import settings
from app.music.models import LONG_SECONDS, Kind, Track

logger = logging.getLogger("jarvis.youtube")

AUDIO_EXTS = (".m4a", ".webm", ".opus", ".mp3")
# В выдаче поиска бывают каналы и плейлисты («Группа КИНО») — нужны только видео.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Длиннее — почти всегда часовые сборки и стримы, в очереди они не нужны.
MAX_TRACK_SECONDS = 15 * 60
MIN_TRACK_SECONDS = 60

_inflight: dict[str, asyncio.Task[Path]] = {}
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


async def _list(url: str, limit: int, kind: Kind = "music") -> list[Track]:
    out = await _run(
        "--flat-playlist", "--playlist-end", str(limit),
        "--print", "%(id)s\t%(duration)s\t%(title)s", url,
    )
    tracks = []
    for line in out.splitlines():
        video_id, duration, title = (line.split("\t", 2) + ["", ""])[:3]
        if not _VIDEO_ID.match(video_id):
            continue
        try:
            seconds: float | None = float(duration)
        except ValueError:
            seconds = None
        tracks.append(Track(title=title, source="youtube", ref=video_id, duration=seconds, kind=kind))
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


def _cached(video_id: str) -> Path | None:
    cache = Path(settings.music_cache_dir)
    return next((p for p in cache.glob(f"{video_id}.*") if p.suffix in AUDIO_EXTS), None)


def _prune_cache(keep: Path) -> None:
    """LRU по mtime: при каждом проигрывании файл «трогается» в download()."""
    files = sorted(
        (p for p in Path(settings.music_cache_dir).iterdir() if p.suffix in AUDIO_EXTS),
        key=lambda p: p.stat().st_mtime,
    )
    total = sum(p.stat().st_size for p in files)
    limit = settings.music_cache_max_mb * 1024 * 1024
    for path in files:
        if total <= limit:
            break
        if path != keep:
            total -= path.stat().st_size
            path.unlink(missing_ok=True)


async def _download(video_id: str) -> Path:
    if path := _cached(video_id):
        path.touch()
        return path
    cache = Path(settings.music_cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    await _run(
        "-f", "bestaudio[ext=m4a]/bestaudio", "--no-playlist", "--no-progress",
        "-o", str(cache / "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
        timeout=180,
    )
    path = _cached(video_id)
    if path is None:
        raise RuntimeError("yt-dlp ничего не скачал")
    _prune_cache(keep=path)
    return path


async def download(track: Track) -> Path:
    """Параллельные запросы одного трека (предзагрузка + «следующий»)
    ждут одну и ту же загрузку."""
    task = _inflight.get(track.ref)
    if task is None:
        task = asyncio.create_task(_download(track.ref))
        _inflight[track.ref] = task
        task.add_done_callback(lambda _: _inflight.pop(track.ref, None))
    return await task


def cached(track: Track) -> Path | None:
    return _cached(track.ref) if track.source == "youtube" else None


async def stream_url(video_id: str, refresh: bool = False) -> str:
    url, fetched = _stream_urls.get(video_id, ("", 0.0))
    if refresh or not url or time.time() - fetched > STREAM_URL_TTL:
        out = await _run("-f", "bestaudio[ext=m4a]/bestaudio", "-g", "--no-playlist",
                         f"https://www.youtube.com/watch?v={video_id}")
        url = out.strip().splitlines()[0]
        _stream_urls[video_id] = (url, time.time())
    return url


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
