"""Задержки хода: на что уходит время от конца фразы до первого звука ответа.

Секундомер хода живёт в ContextVar: его видят все задачи, запущенные внутри
хода (turns.start, синтез озвучки), поэтому этапы отмечаются вызовом
metrics.mark() из любого места без протаскивания параметров. Вне хода
(Telegram, MCP, тесты) mark() ничего не делает.

    ход веба ─► begin ─► mark("stt") ─► mark("route") ─► … ─► record_server ─┐
    экран: конец фразы → первый звук ─► POST /metrics/turn ─► record_client ─┤
                                                                             ▼
                                           data/metrics/turns.jsonl (строки сливаются по turn_id)
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("jarvis.metrics")

Kind = Literal["fast", "agent", "hermes", "local", "stop", "empty"]
Stage = Literal["stt", "route", "llm_first", "llm_total", "tts_first"]

STORE = Path("data/metrics/turns.jsonl")


@dataclass
class Stopwatch:
    turn_id: str
    device: str
    kind: Kind | None = None
    started: float = field(default_factory=time.monotonic)
    # Секунды от started.
    stages: dict[str, float] = field(default_factory=dict)

    def mark(self, stage: Stage) -> None:
        """Первая отметка этапа выигрывает: повторный синтез или второй шаг
        LLM не сдвигают уже замеренное."""
        self.stages.setdefault(stage, round(time.monotonic() - self.started, 2))

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "stages": dict(self.stages)}


_current: ContextVar[Stopwatch | None] = ContextVar("jarvis_stopwatch", default=None)


def begin(turn_id: str, device: str) -> Stopwatch:
    sw = Stopwatch(turn_id, device)
    _current.set(sw)
    return sw


def mark(stage: Stage) -> None:
    if (sw := _current.get()) is not None:
        sw.mark(stage)


def set_kind(kind: Kind) -> None:
    """Последний вызов выигрывает: откат на Hermes после упавшего облака
    переписывает вид хода — медленный ход не попадёт в «agent»."""
    if (sw := _current.get()) is not None:
        sw.kind = kind


def _append(entry: dict[str, Any]) -> None:
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with STORE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Замер — не повод ронять ход.
        logger.exception("Не записал метрику хода")


def record_server(sw: Stopwatch) -> None:
    _append({"ts": time.time(), "turn_id": sw.turn_id, "device": sw.device, **sw.as_dict()})


def record_client(turn_id: str, device: str, client: dict[str, Any]) -> None:
    _append({"ts": time.time(), "turn_id": turn_id, "device": device, "client": client})


def _load(days: int) -> list[dict[str, Any]]:
    if not STORE.exists():
        return []
    since = time.time() - days * 86400
    entries = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("ts", 0) >= since:
            entries.append(entry)
    return entries


def _merge(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Строки сервера и клиента одного хода — в одну запись."""
    turns: dict[str, dict[str, Any]] = {}
    for e in entries:
        turn = turns.setdefault(e["turn_id"], {"kind": None, "values": {}})
        if "stages" in e:
            turn["kind"] = e.get("kind")
            turn["values"].update(e["stages"])
        for key in ("first_sound", "silenced_after"):
            if (value := (e.get("client") or {}).get(key)) is not None:
                turn["values"][key] = value
    return list(turns.values())


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summary(days: int = 7) -> dict[str, Any]:
    """{kind: {этап: {median, p90, n}}} — медиана и p90 по видам хода."""
    by_kind: dict[str, dict[str, list[float]]] = {}
    for turn in _merge(_load(days)):
        # Клиентский замер без серверной строки (ядро упало до записи) — вид неизвестен.
        stages = by_kind.setdefault(turn["kind"] or "unknown", {})
        for key, value in turn["values"].items():
            stages.setdefault(key, []).append(float(value))
    return {
        "days": days,
        "kinds": {
            kind: {
                key: {"median": _percentile(v, 0.5), "p90": _percentile(v, 0.9), "n": len(v)}
                for key, v in stages.items()
            }
            for kind, stages in by_kind.items()
        },
    }


def prune(max_age_days: int = 30) -> None:
    """Старше max_age_days — выбросить (при старте ядра)."""
    if not STORE.exists():
        return
    since = time.time() - max_age_days * 86400
    lines = STORE.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if _ts(line) >= since]
    if len(kept) != len(lines):
        STORE.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


def _ts(line: str) -> float:
    try:
        return float(json.loads(line).get("ts", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0.0
