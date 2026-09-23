"""История разговора для экрана: после перезагрузки страницы чат на месте.

Это только лента для глаз (что спросил — что ответил), а не контекст
модели: его держит Hermes. Одна сессия — один файл data/chat/<session>.jsonl;
туда же попадают голосовые команды с гарнитуры asus — разговор общий.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

STORE = Path("data/chat")
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _path(session_id: str) -> Path:
    return STORE / f"{_SAFE.sub('_', session_id)[:64] or 'default'}.jsonl"


def append(
    session_id: str, user: str, reply: str, tools: list[str], cancelled: bool = False, turn_id: str | None = None,
) -> None:
    """turn_id — по нему экран находит ответ хода, событие которого потерялось
    (ядро перезапустилось между ответом и доставкой)."""
    STORE.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), "user": user, "reply": reply, "tools": tools, "cancelled": cancelled, "turn_id": turn_id}
    with _path(session_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load(session_id: str, limit: int = 100) -> list[dict[str, Any]]:
    path = _path(session_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in lines if line.strip()]
