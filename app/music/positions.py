"""Где остановились в длинном (книги, подкасты, выпуски новостей).

Общая для всех устройств: начал книгу дома — продолжил с телефона.
Файл data/positions.json, ключ — Track.key.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.music.models import Kind, Source, Track

STORE = Path("data/positions.json")
REWIND = 10.0  # продолжаем чуть раньше места остановки — вспомнить контекст
FINISHED_TAIL = 30.0  # ближе к концу — считаем дослушанным
MAX_ENTRIES = 200


@dataclass
class Position:
    title: str
    source: Source
    ref: str
    kind: Kind
    duration: float | None
    position: float
    updated: float
    finished: bool = False

    def track(self) -> Track:
        return Track(title=self.title, source=self.source, ref=self.ref, duration=self.duration, kind=self.kind)


def _load() -> dict[str, Position]:
    if not STORE.exists():
        return {}
    return {k: Position(**v) for k, v in json.loads(STORE.read_text()).items()}


def _write(data: dict[str, Position]) -> None:
    newest = sorted(data.items(), key=lambda kv: kv[1].updated, reverse=True)[:MAX_ENTRIES]
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({k: asdict(v) for k, v in newest}, ensure_ascii=False, indent=1))


def save(track: Track, position: float | None, duration: float | None = None, finished: bool = False) -> None:
    if not track.is_long or position is None:
        return
    duration = duration or track.duration
    if duration and position >= duration - FINISHED_TAIL:
        finished = True
    data = _load()
    data[track.key] = Position(
        title=track.title, source=track.source, ref=track.ref, kind=track.kind,
        duration=duration, position=0.0 if finished else position,
        updated=time.time(), finished=finished,
    )
    _write(data)


def start_for(track: Track) -> float:
    """С какой секунды начинать: длинное — с места остановки минус REWIND."""
    if not track.is_long:
        return 0.0
    saved = _load().get(track.key)
    if saved is None or saved.finished:
        return 0.0
    return max(0.0, saved.position - REWIND)


def unfinished(query: str | None = None) -> list[Position]:
    """Недослушанное, самое свежее первым; query — часть названия."""
    needle = (query or "").lower()
    items = [p for p in _load().values() if not p.finished and p.position > 0 and needle in p.title.lower()]
    return sorted(items, key=lambda p: p.updated, reverse=True)
