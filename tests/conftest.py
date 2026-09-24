"""Каждый тест — со своей пустой библиотекой и хранилищем во временной папке."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from app.config import settings
from app.music.library import library
from app.music.models import Track
from app.music.outputs import Output


@pytest.fixture(autouse=True)
def music_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(settings, "music_cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "music_liked_dir", str(tmp_path / "liked"))
    monkeypatch.setattr(settings, "music_storage_max_mb", 10)
    monkeypatch.setattr(settings, "dj_hermes_api_key", "")
    library.close()
    monkeypatch.setattr(library, "path", tmp_path / "library.db")
    yield tmp_path
    library.close()


def yt(ref: str, artist: str | None = None, title: str | None = None, duration: float | None = 200) -> Track:
    return Track(title=title or f"song {ref}", source="youtube", ref=ref, duration=duration, artist=artist)


class FakeOutput(Output):
    """Выход в памяти: что загрузили, позиция задаётся тестом."""

    def __init__(self) -> None:
        super().__init__()
        self.loaded: list[Track | None] = []
        self.pos = 0.0
        self.paused = False
        self.meta: dict[str, Any] = {}

    async def load(self, track: Track, start: float, paused: bool) -> None:
        self.loaded.append(track)
        self.pos, self.paused = start, paused

    async def set_paused(self, paused: bool) -> None:
        self.paused = paused

    async def is_paused(self) -> bool:
        return self.paused

    async def stop(self) -> None:
        self.loaded.append(None)

    async def seek(self, position: float) -> None:
        self.pos = position

    async def position(self) -> float | None:
        return self.pos

    async def set_volume(self, level: int) -> bool:
        return True

    async def volume(self) -> int | None:
        return None

    def set_meta(self, meta: dict[str, Any]) -> None:
        self.meta.update(meta)
