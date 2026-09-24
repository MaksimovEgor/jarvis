"""Где лежат скачанные треки: лайки навсегда, остальное — кэш.

    data/music/liked/<id>.m4a  — лайкнутое, само не удаляется
    data/music/cache/<id>.m4a  — всё прочее, LRU по mtime

Общий лимит — MUSIC_STORAGE_MAX_MB (10 ГБ): кэш занимает то, что осталось
от лайков. Лайк переносит файл cache → liked, снятие лайка — обратно,
дизлайк удаляет из кэша.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.music.models import FromWhere

logger = logging.getLogger("jarvis.storage")

AUDIO_EXTS = (".m4a", ".webm", ".opus", ".mp3")
MB = 1024 * 1024


@dataclass(frozen=True)
class Usage:
    liked_mb: float
    cache_mb: float
    limit_mb: int


def cache_dir() -> Path:
    return Path(settings.music_cache_dir)


def liked_dir() -> Path:
    return Path(settings.music_liked_dir)


def _find(folder: Path, ref: str) -> Path | None:
    if not folder.exists():
        return None
    return next((p for p in folder.glob(f"{ref}.*") if p.suffix in AUDIO_EXTS), None)


def _files(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.suffix in AUDIO_EXTS] if folder.exists() else []


def _size(folder: Path) -> int:
    return sum(p.stat().st_size for p in _files(folder))


def path_for(ref: str) -> Path | None:
    """Файл трека, если он есть на диске. Кэшированный «трогаем» — LRU."""
    if path := _find(liked_dir(), ref):
        return path
    if path := _find(cache_dir(), ref):
        path.touch()
        return path
    return None


def where(ref: str) -> FromWhere:
    if _find(liked_dir(), ref):
        return "liked"
    return "cache" if _find(cache_dir(), ref) else "net"


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    shutil.move(path, target)
    return target


def pin(ref: str) -> bool:
    """Лайк: cache → liked. False — файла ещё нет, его надо скачать и повторить."""
    if _find(liked_dir(), ref):
        return True
    path = _find(cache_dir(), ref)
    if path is None:
        return False
    _move(path, liked_dir())
    total = _size(liked_dir())
    if total > settings.music_storage_max_mb * MB * 0.9:
        logger.warning("Лайки занимают %.0f МБ — почти весь лимит хранилища", total / MB)
    prune()
    return True


def unpin(ref: str) -> None:
    """Снятие лайка: файл остаётся, но теперь это обычный кэш."""
    if path := _find(liked_dir(), ref):
        _move(path, cache_dir()).touch()
        prune()


def drop(ref: str) -> None:
    """Дизлайк: из кэша долой. Лайкнутое не трогаем — сначала снимут лайк."""
    if path := _find(cache_dir(), ref):
        path.unlink(missing_ok=True)


def prune(keep: Path | None = None) -> None:
    """Кэш — не больше, чем «лимит минус лайки»; старые (по mtime) первыми."""
    limit = settings.music_storage_max_mb * MB - _size(liked_dir())
    files = sorted(_files(cache_dir()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for path in files:
        if total <= limit:
            break
        if path != keep:
            total -= path.stat().st_size
            path.unlink(missing_ok=True)


def usage() -> Usage:
    return Usage(
        liked_mb=round(_size(liked_dir()) / MB, 1),
        cache_mb=round(_size(cache_dir()) / MB, 1),
        limit_mb=settings.music_storage_max_mb,
    )
