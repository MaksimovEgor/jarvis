from __future__ import annotations

import base64
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.orchestrator import run_agent
from app.config import settings
from app.mcp_server import mcp
from app.music.player import player
from app.schemas import AudioChatResponse, TextChatRequest, TextChatResponse
from app.services import stt, tts
from app.services.hermes import run_hermes
from app.timers import timers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.core")

# MCP (streamable HTTP) встраивается прямо в ядро: плеер один на систему.
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

_LOCAL_ONLY = ("/mcp", "/music")


@app.middleware("http")
async def _local_only(request: Request, call_next):
    """Ядро доступно снаружи через туннель и Caddy (basic auth), но плеер и
    MCP — только для процессов на asus. Caddy всегда добавляет X-Forwarded-For,
    а прямые локальные запросы его не несут."""
    if request.url.path.startswith(_LOCAL_ONLY) and "x-forwarded-for" in request.headers:
        return JSONResponse({"detail": "local only"}, status_code=403)
    return await call_next(request)

# In-memory по session_id — переживает процесс, не перезапуски. Для пилота
# этого достаточно; персистентность истории не нужна раньше многопользовательского режима.
_sessions: dict[str, list[dict]] = {}


async def _answer(session_id: str, text: str) -> tuple[str, list[str]]:
    if settings.agent_backend == "hermes":
        return await run_hermes(session_id, text)
    history = _sessions.setdefault(session_id, [])
    return await run_agent(history, text)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat/text", response_model=TextChatResponse)
async def chat_text(req: TextChatRequest) -> TextChatResponse:
    reply, tool_calls = await _answer(req.session_id, req.text)
    audio_b64 = await _speak(reply) if req.speak else None
    return TextChatResponse(reply=reply, tool_calls=tool_calls, audio_base64=audio_b64)


@app.post("/chat/audio", response_model=AudioChatResponse)
async def chat_audio(file: UploadFile, session_id: str = "default") -> AudioChatResponse:
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "in.wav").suffix or ".wav") as tmp_in:
        tmp_in.write(await file.read())
        tmp_in.flush()
        transcript = await stt.transcribe(Path(tmp_in.name))
    logger.info("STT: %s", transcript)

    reply, tool_calls = await _answer(session_id, transcript)
    audio_b64 = await _speak(reply)
    return AudioChatResponse(transcript=transcript, reply=reply, tool_calls=tool_calls, audio_base64=audio_b64)


@app.post("/music/duck")
async def music_duck() -> dict:
    """Listener: wake word — музыку на паузу до конца ответа."""
    await player.duck()
    return {"status": "ok"}


@app.post("/music/unduck")
async def music_unduck() -> dict:
    await player.unduck()
    return {"status": "ok"}


@app.get("/music/state")
async def music_state() -> dict:
    return await player.state()


async def _speak(text: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_out:
        await tts.synthesize(text, Path(tmp_out.name))
        return base64.b64encode(Path(tmp_out.name).read_bytes()).decode()


# Веб-интерфейс (web/, собирается `npm run build`). Монтируется последним,
# чтобы не перекрывать API-роуты выше. Снаружи доступен только через
# `tailscale serve` (HTTPS нужен браузеру для доступа к микрофону).
_WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
