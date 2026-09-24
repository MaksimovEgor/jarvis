"""API плеера в браузере (телефон, Mac): ядро ↔ <audio> на странице.

    GET  /player/events?device=…  — SSE: снимок состояния сразу и при каждом
                                     изменении, объявления таймеров, перемотка
    POST /player/report            — браузер: позиция, пауза, конец/ошибка трека
    POST /player/control           — кнопки статусбара и экрана блокировки
    GET  /player/queue, POST /player/queue/{move,remove,play} — экран «Очередь»
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.music import devices
from app.turns import turns

router = APIRouter(prefix="/player")


def _web_device(device: str) -> str:
    if not devices.DEVICE_RE.match(device) or device == devices.ASUS:
        raise HTTPException(400, "нужен device вида web:<id>")
    return device


class PlayerReport(BaseModel):
    device: str
    seq: int
    position: float | None = None
    paused: bool | None = None
    ended: bool = False
    error: str | None = None
    # Браузер ждёт данные дольше 10 с, хотя должен играть (<audio> waiting).
    stalled: bool = False
    # Экран придержал музыку (слушает «Джарвис», играет объявление) — не зависание.
    held: bool | None = None
    # iOS не дал включить звук без касания — ждём тап, это не зависание.
    blocked: bool | None = None
    volume_supported: bool | None = None
    hls: bool | None = None
    # Вкладка на экране или свёрнута — решает, слать ли пуш.
    visible: bool | None = None


class PlayerControl(BaseModel):
    device: str
    action: Literal["pause", "resume", "next", "previous", "stop", "seek"]
    position: float | None = None


class QueueAction(BaseModel):
    device: str
    index: int = 0
    to: int = 0


@router.get("/queue")
async def queue(device: str) -> dict:
    return devices.player_for(_web_device(device)).queue_view()


@router.post("/queue/move")
async def queue_move(req: QueueAction) -> dict:
    player = devices.player_for(_web_device(req.device))
    await player.queue_move(req.index, req.to)
    return player.queue_view()


@router.post("/queue/remove")
async def queue_remove(req: QueueAction) -> dict:
    player = devices.player_for(_web_device(req.device))
    await player.queue_remove(req.index)
    return player.queue_view()


@router.post("/queue/play")
async def queue_play(req: QueueAction) -> dict:
    player = devices.player_for(_web_device(req.device))
    text = await player.play_at(req.index)
    return {**player.queue_view(), "text": text}


@router.get("/events")
async def events(
    device: str, since: str | None = None, last_event_id: str | None = Header(None),
) -> EventSourceResponse:
    """since — id последнего полученного события: браузер сам шлёт его
    заголовком Last-Event-ID при автопереподключении, а при новом EventSource
    (вкладка вернулась из фона) — параметром."""
    device = _web_device(device)
    devices.seen(device)  # открыл страницу — теперь звук по умолчанию сюда
    output = devices.web_output(device)
    queue = output.subscribe(since or last_event_id, turns.active(device))

    async def stream() -> AsyncIterator[dict[str, Any]]:
        try:
            while True:
                event = dict(await queue.get())
                event_id = event.pop("_id", None)
                message = {"data": json.dumps(event, ensure_ascii=False)}
                yield {**message, "id": event_id} if event_id else message
        finally:
            output.unsubscribe(queue)

    # ping держит соединение живым через Caddy и туннель.
    return EventSourceResponse(stream(), ping=15)


@router.post("/report")
async def report(req: PlayerReport) -> dict:
    data = req.model_dump(exclude_unset=True)
    await devices.web_output(_web_device(req.device)).report(data)
    return {"status": "ok"}


@router.post("/control")
async def control(req: PlayerControl) -> dict:
    player = devices.player_for(_web_device(req.device))
    if req.action == "seek":
        text = await player.seek(position=req.position or 0)
    else:
        handlers = {
            "pause": player.pause, "resume": player.resume, "next": player.next,
            "previous": player.previous, "stop": player.stop,
        }
        text = await handlers[req.action]()
    return {"status": "ok", "text": text}
