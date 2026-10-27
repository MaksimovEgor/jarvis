"""Мгновенное переключение на экране: ядро заранее сообщает соседей текущего
трека — предыдущий (prev) и готовый файл следующего (next)."""

import asyncio

import pytest

from app.music import sources, storage
from app.music.outputs import WebOutput
from app.music.player import Player
from tests.conftest import FakeOutput, yt


async def test_prev_meta_points_to_previous_track(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "where", lambda ref: "cache" if ref == "a" else "net")
    player = Player("web:test-device", FakeOutput())
    output = player.output
    await player._start_queue([yt("a", "A"), yt("b", "B"), yt("c", "C")])
    assert output.meta["prev"] is None
    await player.next()
    assert output.meta["prev"]["ref"] == "a"
    assert output.meta["prev"]["src"] == "media/file/a"
    await player.next()
    # «b» не скачан — экран покажет его сразу, а звук возьмёт у ядра.
    assert output.meta["prev"]["ref"] == "b" and output.meta["prev"]["src"] is None


async def test_web_prefetch_announces_ready_next(monkeypatch: pytest.MonkeyPatch) -> None:
    async def download(track, timeout: float = 0):
        return None

    monkeypatch.setattr(sources, "download", download)
    output = WebOutput("web:prefetch-test")
    output._state = {**output._state, "seq": 1, "src": "media/file/a", "title": "a"}
    output.prefetch(yt("b", "B"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert output.snapshot()["next"] == {"ref": "b", "src": "media/file/b"}


async def test_web_prefetch_failure_keeps_next_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def download(track, timeout: float = 0):
        raise RuntimeError("нет сети")

    monkeypatch.setattr(sources, "download", download)
    output = WebOutput("web:prefetch-fail")
    output._state = {**output._state, "seq": 1, "src": "media/file/a", "title": "a"}
    output.prefetch(yt("b", "B"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert "next" not in output.snapshot()
