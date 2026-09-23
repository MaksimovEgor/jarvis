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

Веб не ждёт ответа в HTTP-запросе (detach): POST сразу возвращает «принято»,
а расшифровка, прогресс, ответ и ошибка приходят событиями по SSE. Долгий
POST через Caddy и ssh-туннель рвался (свёрнутая вкладка, моргнувший туннель),
и ответ, уже готовый на ядре, до экрана не доходил:

    POST /chat/* ─► 202 ─┐
                         ▼
    detach: STT → run → озвучка ─► emit {"type": "turn", id, transcript|tool|reply|error|cancelled}
                                        │ WebOutput хранит события (outputs.py)
    SSE переподключился ◄───────────────┘ пропущенные досылаются + список идущих ходов

Основной план и фон. Отвечать быстро важнее, чем держать долгий ресерч
на переднем плане:

    реплика ─► start ─► уложился в бюджет (FOREGROUND_BUDGET_SECONDS)? ── да ─► ответ
                            │ нет, или «…в фоне» (conflicts.wants_background)
                            ▼
                      demote: ход фоновый — не держит «думаю» и музыку,
                      новые реплики его не отменяют (кроме ✕ и явного «отмени …»)
                            │ готово
                            ▼
                      ответ событием (веб) или объявлением (asus, speaker.py)
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
    tool: str | None = None
    # Ушёл в фон: реплики-конфликты и «стоп» его не трогают.
    background: bool = False


@dataclass
class _Detached:
    """Ход веба целиком — от расшифровки до озвучки ответа."""
    device: str
    text: str
    task: asyncio.Task[None] = field(repr=False)


class Turns:
    def __init__(self) -> None:
        self._active: dict[str, Turn] = {}
        self._detached: dict[str, _Detached] = {}

    def detach(self, turn_id: str, device: str, text: str, job: Awaitable[None]) -> None:
        """Ход без ожидающего HTTP-запроса: результат уйдёт событиями (emit)."""
        task = asyncio.create_task(job)
        self._detached[turn_id] = _Detached(device=device, text=text, task=task)
        task.add_done_callback(lambda _: self._detached.pop(turn_id, None))

    def active(self, device: str) -> list[dict[str, object]]:
        """Идущие ходы устройства — экрану после переподключения SSE."""
        result: list[dict[str, object]] = []
        for turn_id, d in self._detached.items():
            if d.device != device:
                continue
            turn = self._active.get(turn_id)
            result.append({
                "id": turn_id, "text": d.text,
                "tool": turn.tool if turn else None,
                "background": bool(turn and turn.background),
            })
        return result

    def busy(self) -> int:
        """Ходы в работе, от расшифровки до озвучки ответа."""
        return len(self._active.keys() | self._detached.keys())

    def start(
        self, turn_id: str, session_id: str, device: str, text: str,
        work: Callable[[Progress], Awaitable[Answer]],
    ) -> Turn:
        """Запустить ход; новая реплика может отменить идущие на переднем плане."""
        running = [t for t in self._active.values() if t.session_id == session_id and not t.background]

        def progress(tool: str) -> None:
            if current := self._active.get(turn_id):
                current.tool = tool
            self.emit(device, turn_id, tool=tool)

        task = asyncio.create_task(work(progress))
        turn = Turn(id=turn_id, session_id=session_id, device=device, text=text, task=task)
        self._active[turn_id] = turn
        task.add_done_callback(lambda _: self._active.pop(turn_id, None))
        if running:
            asyncio.create_task(self._resolve(turn, running))
        return turn

    @staticmethod
    async def within(turn: Turn, seconds: float) -> bool:
        """Закончился ли ход за seconds (сам ход при этом не трогаем)."""
        done, _ = await asyncio.wait({turn.task}, timeout=seconds)
        return bool(done)

    @staticmethod
    def demote(turn: Turn) -> None:
        logger.info("«%s» уходит в фон", turn.text)
        turn.background = True

    async def outcome(self, turn: Turn) -> Answer | None:
        """Ответ хода или None, если его отменили (новой репликой или ✕)."""
        # wait, а не await task: если оборвался сам HTTP-запрос клиента,
        # задача продолжает выполняться.
        await asyncio.wait({turn.task})
        if turn.task.cancelled():
            self.emit(turn.device, turn.id, cancelled=True)
            return None
        return turn.task.result()

    def emit(self, device: str, turn_id: str, **fields: object) -> None:
        """Событие хода на экран устройства. У asus экрана нет."""
        if device == devices.ASUS:
            return
        if isinstance(fields.get("transcript"), str) and (d := self._detached.get(turn_id)):
            d.text = str(fields["transcript"])
        devices.web_output(device).notify({"type": "turn", "id": turn_id, **fields})

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
        """Отменить одну просьбу (✕ в чате, в том числе фоновую) или все
        просьбы сессии на переднем плане («стоп» не трогает фоновый ресерч)."""
        victims = [
            t for t in self._active.values()
            if t.session_id == session_id and not t.task.done()
            and (t.id == turn_id if turn_id else not t.background)
        ]
        for t in victims:
            t.task.cancel()
        return len(victims)


turns = Turns()
