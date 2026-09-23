"""Ходы разговора: несколько просьб могут выполняться одновременно.

Каждая реплика — отдельная задача. Новая реплика не ждёт старые: она сразу
уходит в работу, а параллельно диспетчер (app/agent/conflicts.py) решает,
какие из выполняющихся она отменяет. Отмена — это cancel задачи: поток к
Hermes закрывается, и Hermes прерывает агента (см. services/hermes.py).

Если клиент ушёл (iPhone свернул вкладку и оборвал запрос), задача
продолжается: «напомни через 30 минут» не должно потеряться.

Прогресс (какой инструмент сейчас работает) и отмена уходят на экран
устройства событием {"type": "turn"} по SSE плеера — видно, над чем
Джарвис думает, и можно отменить конкретную просьбу.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.agent import conflicts
from app.music import devices
from app.services.speech import Spoken

logger = logging.getLogger("jarvis.turns")

# (ответ, инструменты, озвучка | None)
Answer = tuple[str, list[str], Spoken | None]
Progress = Callable[[str], None]


@dataclass
class Turn:
    id: str
    session_id: str
    device: str
    text: str
    task: asyncio.Task[Answer] = field(repr=False)


class Turns:
    def __init__(self) -> None:
        self._active: dict[str, Turn] = {}

    async def run(
        self, turn_id: str, session_id: str, device: str, text: str,
        work: Callable[[Progress], Awaitable[Answer]],
    ) -> Answer | None:
        """Ответ хода или None, если его отменили (новой репликой или ✕)."""
        running = [t for t in self._active.values() if t.session_id == session_id]
        task = asyncio.create_task(work(lambda tool: self._notify(device, turn_id, tool=tool)))
        turn = Turn(id=turn_id, session_id=session_id, device=device, text=text, task=task)
        self._active[turn_id] = turn
        task.add_done_callback(lambda _: self._active.pop(turn_id, None))
        if running:
            asyncio.create_task(self._resolve(turn, running))

        # wait, а не await task: если оборвался сам HTTP-запрос клиента,
        # задача продолжает выполняться.
        await asyncio.wait({task})
        if task.cancelled():
            self._notify(device, turn_id, cancelled=True)
            return None
        return task.result()

    @staticmethod
    def _notify(device: str, turn_id: str, tool: str | None = None, cancelled: bool = False) -> None:
        if device == devices.ASUS:
            return
        event: dict[str, object] = {"type": "turn", "id": turn_id}
        if tool:
            event["tool"] = tool
        if cancelled:
            event["cancelled"] = True
        devices.web_output(device).notify(event)

    async def _resolve(self, new: Turn, running: list[Turn]) -> None:
        try:
            indices = await conflicts.to_cancel(new.text, [t.text for t in running])
        except Exception:
            logger.exception("Диспетчер конфликтов упал")
            return
        for i in indices:
            old = running[i]
            if not old.task.done():
                logger.info("«%s» отменяет «%s»", new.text, old.text)
                old.task.cancel()

    def cancel(self, session_id: str, turn_id: str | None = None) -> int:
        """Отменить одну просьбу (✕ в чате) или все просьбы сессии."""
        victims = [
            t for t in self._active.values()
            if t.session_id == session_id and not t.task.done() and turn_id in (None, t.id)
        ]
        for t in victims:
            t.task.cancel()
        return len(victims)


turns = Turns()
