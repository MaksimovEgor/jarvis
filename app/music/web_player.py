"""API плеера в браузере (телефон, Mac): ядро ↔ <audio> на странице.

    GET  /player/events?device=…  — SSE: снимок состояния сразу и при каждом
                                     изменении, объявления таймеров, перемотка
    POST /player/report            — браузер: позиция, пауза, конец/ошибка трека
    POST /player/control           — кнопки статусбара и экрана блокировки
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.music import devices

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
    volume_supported: bool | None = None
    hls: bool | None = None


class PlayerControl(BaseModel):
    device: str
    action: Literal["pause", "resume", "next", "previous", "stop", "seek"]
    position: float | None = None


@router.get("/events")
async def events(device: str) -> EventSourceResponse:
    output = devices.web_output(_web_device(device))
    queue = output.subscribe()

    async def stream() -> AsyncIterator[dict[str, Any]]:
        try:
            while True:
                event = await queue.get()
                yield {"data": json.dumps(event, ensure_ascii=False)}
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
