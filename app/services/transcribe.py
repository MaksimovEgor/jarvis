"""Расшифровка аудио любой длины: ссылки, то, что играет, подкасты и книги по
поиску, файлы. Короткое — моделью ядра (CPU), длинное — GPU-воркером
(app/transcribe_worker.py), который живёт только на время задачи.

    источник ──► файл ──► ≤ SHORT_S: stt.transcribe (ядро, CPU)
                     └──► длиннее:   воркер, cuda (GPU свободен) или cpu
                                         ▼
                          data/transcripts/<ключ>.txt  (кэш: повтор — мгновенно)

Одна и та же расшифровка не запускается дважды: второй запрос ждёт первую.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.music import devices, sources, youtube
from app.music.models import Kind, Track
from app.services import gpu, stt

logger = logging.getLogger(__name__)

# Короче — моделью ядра: поднимать процесс с CUDA (~6 с) дольше самой работы.
SHORT_S = 90.0

_jobs: dict[str, asyncio.Task[Transcript]] = {}


@dataclass
class Transcript:
    title: str
    text: str
    path: Path
    seconds: float | None


def _dir() -> Path:
    path = Path(settings.transcripts_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(raw: str) -> str:
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


async def duration(path: Path) -> float | None:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError:
        return None


async def _worker(src: Path, out: Path, device: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.transcribe_worker", str(src), str(out), device,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"воркер расшифровки ({device}): {err.decode(errors='replace').strip()[-300:]}")


async def transcribe_file(src: Path) -> str:
    """Текст файла. Длинное — с метками «[мм:сс]» на каждый кусок."""
    seconds = await duration(src)
    if seconds is not None and seconds <= SHORT_S:
        return await stt.transcribe(src)
    out = src.with_name(src.name + ".part.txt")
    started = time.monotonic()
    # GPU занят (резервная LLM) — не ждём его, CPU тоже справляется, медленнее.
    if gpu.lock.locked():
        await _worker(src, out, "cpu")
        device = "cpu"
    else:
        async with gpu.lock:
            try:
                await _worker(src, out, "cuda")
                device = "cuda"
            except RuntimeError:
                logger.exception("GPU-расшифровка упала — повтор на CPU")
                await _worker(src, out, "cpu")
                device = "cpu"
    logger.info("Расшифровка %s (%.0f с звука) на %s за %.0f с", src.name, seconds or 0, device, time.monotonic() - started)
    try:
        return out.read_text(encoding="utf-8").strip()
    finally:
        out.unlink(missing_ok=True)


Fetch = Callable[[], Awaitable[Path]]


async def _source(source: str, kind: Kind) -> tuple[str, str, Fetch]:
    """(ключ кэша, название, как получить файл). source — ссылка, путь,
    «current» или поиск. Файл качается, только если расшифровки ещё нет."""
    if source.startswith(("http://", "https://")):
        track = Track(title=source, source="youtube", ref=f"tr-{_key(source)}", url=source, kind="podcast")
        return source, source, lambda: youtube.download(track, timeout=600)
    if source.startswith(("/", "~")):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"нет файла {path}")

        async def local() -> Path:
            return path

        return f"file:{path}:{path.stat().st_mtime_ns}", path.name, local
    if source == "current":
        player = devices.current_player()
        found = player.current if player else None
        if found is None:
            raise LookupError("сейчас ничего не играет")
    else:
        results = await sources.resolve(source, kind)
        if not results:
            raise LookupError(f"ничего не нашёл по запросу «{source}»")
        found = results[0]
    track = found
    if track.source == "radio":
        raise LookupError("радио — поток без конца, расшифровывать нечего")
    title = f"{track.artist} — {track.title}" if track.artist else track.title
    if track.source == "youtube":
        return track.key, title, lambda: youtube.download(track, timeout=600)
    return track.key, title, lambda: sources.download(track, timeout=600)


async def _run(source: str, kind: Kind) -> Transcript:
    key, title, fetch = await _source(source, kind)
    cached = _dir() / f"{_key(key)}.txt"
    if cached.exists():
        head, _, text = cached.read_text(encoding="utf-8").partition("\n")
        return Transcript(head.removeprefix("# "), text.strip(), cached, None)
    path = await fetch()
    seconds = await duration(path)
    text = await transcribe_file(path)
    cached.write_text(f"# {title}\n{text}\n", encoding="utf-8")
    return Transcript(title, text, cached, seconds)


def start(source: str, kind: Kind = "podcast") -> asyncio.Task[Transcript]:
    """Задача расшифровки; повторный запрос того же источника ждёт первую."""
    job_key = f"{kind}:{source.strip()}"
    task = _jobs.get(job_key)
    if task is None:
        task = asyncio.create_task(_run(source.strip(), kind))
        _jobs[job_key] = task
        task.add_done_callback(lambda _: _jobs.pop(job_key, None))
    return task
