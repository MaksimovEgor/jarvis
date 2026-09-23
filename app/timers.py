"""Таймеры и напоминания, которые звучат дома голосом.

Живут в jarvis-core, а не в cron Hermes: доставка cron-задачи, поставленной
из голосового канала (api_server), зависает — у API-платформы нет push, и
голосом её не услышать. Здесь же в момент срабатывания ядро само ставит
музыку на паузу, играет сигнал и произносит текст (app/services/speaker.py).

Список пишется в data/timers.json — переживает рестарт ядра. Пропущенные
за время простоя (не старше часа) объявляются сразу после старта.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.music import devices
from app.services import speaker

logger = logging.getLogger("jarvis.timers")

STORE = Path("data/timers.json")
MISSED_GRACE = 3600  # старше — уже неактуально, молча выбрасываем


@dataclass
class Alarm:
    id: str
    due: float  # unix time
    text: str
    # Где звенеть — там, где поставили (телефон/asus).
    device: str = devices.ASUS

    @property
    def due_local(self) -> str:
        return datetime.fromtimestamp(self.due).strftime("%d.%m %H:%M:%S")


class Timers:
    def __init__(self) -> None:
        self._alarms: dict[str, Alarm] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self) -> None:
        if STORE.exists():
            for raw in json.loads(STORE.read_text()):
                alarm = Alarm(**raw)
                if alarm.due > time.time() - MISSED_GRACE:
                    self._schedule(alarm)
        self._save()

    def _save(self) -> None:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps([asdict(a) for a in self._alarms.values()], ensure_ascii=False, indent=1))

    def _schedule(self, alarm: Alarm) -> None:
        self._alarms[alarm.id] = alarm
        self._tasks[alarm.id] = asyncio.create_task(self._fire(alarm))

    async def _fire(self, alarm: Alarm) -> None:
        delay = alarm.due - time.time()
        text = alarm.text
        if delay > 0:
            await asyncio.sleep(delay)
        elif delay < -60:
            text = f"Пропущенное напоминание: {text}"
        logger.info("Сработал %s: %s", alarm.id, text)
        try:
            await speaker.announce(text, device=alarm.device, repeat=2)
        except Exception:
            logger.exception("Не получилось объявить %s", alarm.id)
        finally:
            self._alarms.pop(alarm.id, None)
            self._tasks.pop(alarm.id, None)
            self._save()

    def add(self, due: float, text: str, device: str = devices.ASUS) -> Alarm:
        alarm = Alarm(id=uuid.uuid4().hex[:6], due=due, text=text, device=device)
        self._schedule(alarm)
        self._save()
        return alarm

    def cancel(self, alarm_id: str) -> Alarm | None:
        alarm = self._alarms.pop(alarm_id, None)
        task = self._tasks.pop(alarm_id, None)
        if task:
            task.cancel()
        self._save()
        return alarm

    def list(self) -> list[Alarm]:
        return sorted(self._alarms.values(), key=lambda a: a.due)


timers = Timers()
