"""Мгновенное переключение на экране: ядро заранее сообщает соседей текущего
трека — предыдущий (prev) и готовый файл следующего (next)."""

import asyncio
from dataclasses import replace

import pytest

from app.music import player as player_module
from app.music import sources, storage, taste
from app.music.outputs import WebOutput
from app.music.player import Player
from tests.conftest import FakeOutput, yt


class _SlowWave:
    """Волна, которая подбирает не сразу: как в жизни — секунды сети."""

    label = "тестовая"
    mood = "auto"
    slot = "day"

    def __init__(self, refs: list[str], delay: float = 0.02) -> None:
        self.refs = refs
        self.delay = delay
        self.calls = 0
        self.kwargs: dict[str, object] = {}

    async def more(self, queued, n: int = 8, tag: bool = True):
        self.calls += 1
        self.kwargs = {"n": n, "tag": tag}
        await asyncio.sleep(self.delay)
        return [replace(yt(ref, ref.upper()), origin="wave") for ref in self.refs[:n]]

    async def on_start(self, track) -> None:
        return None

    async def on_end(self, track, outcome, played) -> None:
        return None

    def forget(self, ref: str) -> None:
        return None


async def _no_download(track, timeout: float = 0):
    return None


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


async def test_wave_next_waits_for_pick_and_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Старт волны: впереди пусто, волна ещё подбирает — «дальше» ждёт этот же
    подбор и переключает, а не отвечает «не успела»."""
    monkeypatch.setattr(sources, "download", _no_download)
    player = Player("web:test-device", FakeOutput())
    player.wave = _SlowWave(["b", "c", "d", "e"])
    player._queue = [replace(yt("a", "A"), origin="wave")]
    player._pos = 0

    reply = await player.next()

    assert player.current is not None and player.current.ref == "b"
    assert "song b" in reply
    assert player.wave.calls == 1


async def test_wave_keeps_one_pick_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "download", _no_download)
    player = Player("web:test-device", FakeOutput())
    player.wave = slow = _SlowWave(["b", "c", "d", "e"], delay=0.05)
    player._queue = [yt("a", "A")]
    player._pos = 0

    first = player._refill()
    second = player._refill()

    assert first is second and first is not None
    await first
    assert slow.calls == 1
    assert [t.ref for t in player._queue] == ["a", "b", "c", "d", "e"]


async def test_wave_pick_starts_before_first_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подбор следующих идёт, пока качается первый трек: к первому звуку
    «дальше» уже есть."""
    player = Player("web:test-device", FakeOutput())
    player.wave = _SlowWave(["b", "c", "d", "e"])
    seen: dict[str, bool] = {}

    async def download(track, timeout: float = 0):
        seen["refill"] = player._refill_task is not None
        return None

    monkeypatch.setattr(sources, "download", download)
    monkeypatch.setattr(storage, "where", lambda ref: "net")

    await player._start_queue([replace(yt("a", "A"), origin="wave")])

    assert seen["refill"] is True
    assert player._refill_task is not None
    await player._refill_task


async def test_wave_start_gets_next_before_first_sound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Старт волны: первая пачка подбирается сразу, пока качается открывающий
    трек, — к первому звуку «дальше» уже есть и без разметки (её ждёт LLM)."""
    picked: list[_SlowWave] = []

    class FakeWave(_SlowWave):
        def __init__(self, device, mood: str = "auto", rng=None, spec=None) -> None:
            super().__init__(["b", "c", "d", "e"], delay=0.01)
            picked.append(self)

        async def first(self):
            return replace(yt("a", "A"), origin="wave")

    monkeypatch.setattr(player_module, "Wave", FakeWave)
    monkeypatch.setattr(taste, "refresh_seeds_soon", lambda slot, mood: None)
    monkeypatch.setattr(sources, "download", _no_download)
    player = Player("web:test-device", FakeOutput())

    reply = await player.play_wave("auto")
    assert "a" in reply.lower() or "волну" in reply
    assert player._refill_task is not None
    await player._refill_task

    assert picked[0].kwargs == {"n": 3, "tag": False}
    assert [u["ref"] for u in player.output.meta["upcoming"]] == ["b", "c", "d"]
