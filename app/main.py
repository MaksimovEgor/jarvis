from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile
from fastapi.staticfiles import StaticFiles

from app.agent.orchestrator import run_agent
from app.config import settings
from app.schemas import AudioChatResponse, TextChatRequest, TextChatResponse
from app.services import stt, tts
from app.services.hermes import run_hermes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.core")

app = FastAPI(title="Jarvis")

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
