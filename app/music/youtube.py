"""YouTube через yt-dlp: поиск, «микс» похожих треков и скачивание в кэш.

Почему не отдаём mpv прямую ссылку: googlevideo с asus напрямую не
открывается, а ffmpeg внутри mpv не умеет SOCKS. Поэтому yt-dlp качает
аудио через туннель в data/music/cache, а mpv играет локальный файл —
заодно повторное прослушивание работает офлайн. Трек на 6 минут качается
за ~3с.

Очередь «как у Алисы» строится из YouTube Mix (плейлист RD<id>): для
первого найденного трека YouTube сам подбирает ~25 похожих.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

from app.config import settings
from app.music.models import Track

logger = logging.getLogger("jarvis.youtube")

AUDIO_EXTS = (".m4a", ".webm", ".opus", ".mp3")
# Длиннее — почти всегда часовые сборки и стримы, в очереди они не нужны.
MAX_TRACK_SECONDS = 15 * 60
MIN_TRACK_SECONDS = 60

_inflight: dict[str, asyncio.Task[Path]] = {}


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


async def _list(url: str, limit: int) -> list[Track]:
    out = await _run(
        "--flat-playlist", "--playlist-end", str(limit),
        "--print", "%(id)s\t%(duration)s\t%(title)s", url,
    )
    tracks = []
    for line in out.splitlines():
        video_id, duration, title = (line.split("\t", 2) + ["", ""])[:3]
        try:
            seconds: float | None = float(duration)
        except ValueError:
            seconds = None
        tracks.append(Track(title=title, source="youtube", ref=video_id, duration=seconds))
    return tracks


def _fits(track: Track) -> bool:
    return track.duration is None or MIN_TRACK_SECONDS <= track.duration <= MAX_TRACK_SECONDS


async def search(query: str) -> Track | None:
    """Лучший результат, но не часовая сборка: «включи Queen» иначе легко
    попадает на «Greatest Hits 1 hour»."""
    results = await _list(f"ytsearch5:{query}", 5)
    return next((t for t in results if _fits(t)), results[0] if results else None)


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
