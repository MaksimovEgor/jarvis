"""Секундомер хода и журнал задержек (app/metrics.py)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app import metrics


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "turns.jsonl"
    monkeypatch.setattr(metrics, "STORE", path)
    return path


async def test_mark_first_wins() -> None:
    sw = metrics.begin("t1", "web:x")
    metrics.mark("stt")
    first = sw.stages["stt"]
    await asyncio.sleep(0.02)
    metrics.mark("stt")
    assert sw.stages["stt"] == first


def test_mark_without_stopwatch_is_noop(store: Path) -> None:
    async def outside() -> None:
        metrics.mark("stt")
        metrics.set_kind("agent")

    # Свой контекст без begin() — как вызов из MCP или Telegram.
    asyncio.run(outside())
    assert not store.exists()


async def test_stopwatch_inherited_by_child_task() -> None:
    sw = metrics.begin("t2", "web:x")

    async def child() -> None:
        metrics.set_kind("fast")
        metrics.mark("route")

    await asyncio.create_task(child())
    assert sw.kind == "fast"
    assert "route" in sw.stages


def test_set_kind_last_wins() -> None:
    async def turn() -> metrics.Stopwatch:
        sw = metrics.begin("t4", "web:x")
        # Облако своего агента упало — ответил Hermes: ход считается как hermes.
        metrics.set_kind("agent")
        metrics.set_kind("hermes")
        return sw

    assert asyncio.run(turn()).kind == "hermes"


def test_record_server_and_client_lines(store: Path) -> None:
    sw = metrics.Stopwatch("t3", "web:x", kind="agent", stages={"stt": 0.4})
    metrics.record_server(sw)
    metrics.record_client("t3", "web:x", {"first_sound": 1.9})
    lines = [json.loads(line) for line in store.read_text().splitlines()]
    assert lines[0]["stages"] == {"stt": 0.4} and lines[0]["kind"] == "agent"
    assert lines[1]["client"] == {"first_sound": 1.9}


def _line(store: Path, **entry: object) -> None:
    with store.open("a") as f:
        f.write(json.dumps({"ts": time.time(), **entry}) + "\n")


def test_summary_merges_client_and_server(store: Path) -> None:
    for i, (stt, sound) in enumerate([(0.2, 1.0), (0.4, 2.0), (0.6, 3.0)]):
        _line(store, turn_id=f"a{i}", kind="agent", stages={"stt": stt})
        _line(store, turn_id=f"a{i}", client={"first_sound": sound})
    _line(store, turn_id="f0", kind="fast", stages={"route": 0.01})
    kinds = metrics.summary()["kinds"]
    assert kinds["agent"]["first_sound"] == {"median": 2.0, "p90": 3.0, "n": 3}
    assert kinds["agent"]["stt"]["median"] == 0.4
    assert kinds["fast"]["route"]["n"] == 1


def test_summary_ignores_older_than_days(store: Path) -> None:
    _line(store, turn_id="old", kind="agent", stages={"stt": 9.0}, ts=time.time() - 8 * 86400)
    _line(store, turn_id="new", kind="agent", stages={"stt": 1.0})
    assert metrics.summary(7)["kinds"]["agent"]["stt"]["n"] == 1


def test_prune_drops_old_lines(store: Path) -> None:
    _line(store, turn_id="old", kind="agent", stages={}, ts=time.time() - 31 * 86400)
    _line(store, turn_id="new", kind="agent", stages={})
    metrics.prune(30)
    assert [json.loads(line)["turn_id"] for line in store.read_text().splitlines()] == ["new"]
