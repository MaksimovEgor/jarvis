from __future__ import annotations

import base64
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from os import PathLike
from pathlib import Path
from typing import AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app import history
from app.agent import conflicts, router
from app.agent.orchestrator import run_agent
from app.config import settings
from app.mcp_server import mcp
from app.music import devices, media, web_player
from app.schemas import AudioChatResponse, CancelRequest, ClientLog, TextChatRequest, TextChatResponse
from app.services import stt, tts
from app.services.hermes import run_hermes
from app.timers import timers
from app.turns import turns

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.core")

# MCP (streamable HTTP) встраивается прямо в ядро: плееры живут здесь же.
# Его маршрут /mcp добавляется в роутер FastAPI, а менеджер сессий
# запускается через lifespan — у смонтированного sub-app свой lifespan не вызывается.
_mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    timers.start()
    async with _mcp_app.router.lifespan_context(_mcp_app):
        yield


app = FastAPI(title="Jarvis", lifespan=_lifespan)
app.router.routes.extend(_mcp_app.routes)
app.include_router(media.router)
app.include_router(web_player.router)

_LOCAL_ONLY = ("/mcp", "/music")


class _LocalOnly:
    """Ядро доступно снаружи через туннель и Caddy (basic auth), но MCP и
    управление плеером asus — только для процессов на asus. Caddy всегда
    добавляет X-Forwarded-For, а прямые локальные запросы его не несут.
    Чистый ASGI, а не @app.middleware: тот буферизует и ломает долгие
    потоковые ответы (SSE, аудио)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["path"].startswith(_LOCAL_ONLY)
            and any(name == b"x-forwarded-for" for name, _ in scope["headers"])
        ):
            await JSONResponse({"detail": "local only"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(_LocalOnly)

# In-memory по session_id — переживает процесс, не перезапуски. Для пилота
# этого достаточно; персистентность истории не нужна раньше многопользовательского режима.
_sessions: dict[str, list[dict]] = {}


def _device(device: str) -> str:
    if not devices.DEVICE_RE.match(device):
        raise HTTPException(400, "неизвестное устройство")
    return device


async def _stop_everything(session_id: str, device: str, speak: bool) -> tuple[str, list[str], str | None]:
    """«Стоп» как у Алисы — мгновенно и без Hermes: отменить всё, что
    выполняется, и поставить музыку на паузу."""
    turns.cancel(session_id)
    await devices.player_for(device).pause()
    return "Хорошо.", [], await _speak("Хорошо.") if speak else None


async def _answer(
    session_id: str, text: str, device: str, speak: bool, progress: Callable[[str], None],
) -> tuple[str, list[str], str | None]:
    """Весь ход целиком — и ответ, и его озвучка: «стоп» должен отменять и
    синтез длинного ответа, иначе он прозвучит уже после «стопа».
    Простые команды («включи…», «пауза», «таймер на…») — быстрым путём без Hermes."""
    fast = await router.try_fast(text, device)
    if fast is not None:
        reply, tools = fast.text, [fast.tool]
    else:
        # На время хода MCP-инструменты играют и ставят таймеры на этом устройстве.
        with devices.turn(device):
            if settings.agent_backend == "hermes":
                reply, tools = await run_hermes(session_id, text, progress)
            else:
                reply, tools = await run_agent(_sessions.setdefault(session_id, []), text)
    return reply, tools, await _speak(reply) if speak else None


async def _run_turn(
    turn_id: str | None, session_id: str, device: str, text: str, speak: bool,
) -> tuple[str, list[str], str | None] | None:
    result = await turns.run(
        turn_id or uuid.uuid4().hex, session_id, device, text,
        lambda progress: _answer(session_id, text, device, speak, progress),
    )
    reply, tools = (result[0], result[1]) if result else ("", [])
    history.append(session_id, text, reply, tools, cancelled=result is None)
    return result


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat/text", response_model=TextChatResponse)
async def chat_text(req: TextChatRequest) -> TextChatResponse:
    device = _device(req.device)
    if conflicts.is_just_stop(req.text):
        reply, tool_calls, audio_b64 = await _stop_everything(req.session_id, device, req.speak)
        history.append(req.session_id, req.text, reply, tool_calls)
        return TextChatResponse(reply=reply, tool_calls=tool_calls, audio_base64=audio_b64)
    result = await _run_turn(req.turn_id, req.session_id, device, req.text, req.speak)
    if result is None:
        return TextChatResponse(reply="", cancelled=True)
    reply, tool_calls, audio_b64 = result
    return TextChatResponse(reply=reply, tool_calls=tool_calls, audio_base64=audio_b64)


@app.post("/chat/cancel")
async def chat_cancel(req: CancelRequest) -> dict:
    """Отменить одну просьбу (turn_id, ✕ в чате) или все просьбы сессии."""
    return {"cancelled": turns.cancel(req.session_id, req.turn_id)}


@app.get("/chat/history")
async def chat_history(session_id: str = "default", limit: int = 100) -> list[dict]:
    return history.load(session_id, min(limit, 500))


@app.post("/chat/audio", response_model=AudioChatResponse)
async def chat_audio(
    file: UploadFile, session_id: str = "default", device: str = devices.ASUS, turn_id: str | None = None
) -> AudioChatResponse:
    _device(device)
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "in.wav").suffix or ".wav") as tmp_in:
        tmp_in.write(await file.read())
        tmp_in.flush()
        transcript = await stt.transcribe(Path(tmp_in.name))
    logger.info("STT: %s", transcript)
    if not transcript.strip():
        # Тишина/шум — в Hermes не отправляем (пустой ввод даёт 500).
        return AudioChatResponse(transcript="", reply="Не расслышал.", tool_calls=[], audio_base64=await _speak("Не расслышал."))

    if conflicts.is_just_stop(transcript):
        reply, tool_calls, audio_b64 = await _stop_everything(session_id, device, True)
        history.append(session_id, transcript, reply, tool_calls)
        return AudioChatResponse(transcript=transcript, reply=reply, tool_calls=tool_calls, audio_base64=audio_b64)
    result = await _run_turn(turn_id, session_id, device, transcript, True)
    if result is None:
        return AudioChatResponse(transcript=transcript, reply="", cancelled=True)
    reply, tool_calls, audio_b64 = result
    return AudioChatResponse(transcript=transcript, reply=reply, tool_calls=tool_calls, audio_base64=audio_b64)


@app.post("/client/log")
async def client_log(entry: ClientLog) -> dict:
    """Диагностика с телефона (распознавание «Джарвис» в Safari) — в журнал ядра."""
    logger.info("client %s: %s", entry.device[:20], entry.message[:300])
    return {"status": "ok"}


@app.post("/music/duck")
async def music_duck() -> dict:
    """Listener: wake word — музыку на паузу до конца ответа."""
    await devices.player_for(devices.ASUS).duck()
    return {"status": "ok"}


@app.post("/music/unduck")
async def music_unduck() -> dict:
    await devices.player_for(devices.ASUS).unduck()
    return {"status": "ok"}


@app.get("/music/state")
async def music_state(device: str = devices.ASUS) -> dict:
    return await devices.player_for(_device(device)).state()


async def _speak(text: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_out:
        await tts.synthesize(text, Path(tmp_out.name))
        return base64.b64encode(Path(tmp_out.name).read_bytes()).decode()


# Веб-интерфейс (web/, собирается `npm run build`). Монтируется последним,
# чтобы не перекрывать API-роуты выше. Снаружи — через Caddy на VPS point
# (HTTPS нужен браузеру для доступа к микрофону).
_WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


class _WebStatic(StaticFiles):
    """index.html — всегда свежий: Safari на iPhone держал старую страницу
    из кэша, и она слала запросы без device. Ассеты с хешем в имени
    кэшируются как обычно."""

    def file_response(
        self, full_path: PathLike[str] | str, stat_result: os.stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


if _WEB_DIST.is_dir():
    app.mount("/", _WebStatic(directory=_WEB_DIST, html=True), name="web")
