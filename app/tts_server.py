"""Vosk-TTS отдельным постоянным процессом (jarvis-tts, 127.0.0.1:8001).

Модель (~750 МБ) грузится ~60 с, и в процессе ядра загрузка держала GIL:
каждый перезапуск ядра (выкатка) — минута, когда ядро не отвечало вовсе.
Теперь модель живёт здесь, а ядро перезапускается за секунды:

    jarvis-core ──POST /synth {text, speaker, rate}──► этот процесс ──► wav
                  не отвечает (грузится, упал) → ядро озвучивает Piper'ом

Сервис нарочно минимальный: текст к синтезу готовит ядро (speakable, clean,
нарезка по фразам), здесь только синтез — меняется редко, перезапускается
редко (deploy.sh — только если изменился этот файл или юнит).

Порт открывается после загрузки модели: до этого ядро получает отказ в
соединении сразу, а не ждёт минуту.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import Response

from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.tts_server")

_synth: Any = None
# Синтез по одному: onnx и так занимает все ядра.
_lock = asyncio.Lock()


def _load() -> Any:
    import vosk_tts.model  # тяжёлый импорт
    from vosk_tts import Synth

    # Только CPU. vosk_tts сам берёт CUDA, если onnxruntime её видит (в .venv
    # стоит onnxruntime-gpu ради Whisper), а на GTX 1050 синтез вдвое
    # медленнее (0,68 от длительности звука против 0,3 на 8 ядрах) и занял бы
    # 1,5 ГБ из 2 — видеокарта нужна Whisper в ядре.
    runtime = vosk_tts.model.onnxruntime
    available = runtime.get_available_providers
    runtime.get_available_providers = lambda: ["CPUExecutionProvider"]
    try:
        return Synth(vosk_tts.model.Model(model_path=settings.vosk_tts_model_path))
    finally:
        runtime.get_available_providers = available


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _synth
    _synth = await asyncio.to_thread(_load)
    logger.info("Vosk TTS загружен")
    yield


app = FastAPI(title="Jarvis TTS", lifespan=_lifespan)


class SynthRequest(BaseModel):
    text: str
    speaker: int = 0
    rate: float = 1.0


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/synth")
async def synth(req: SynthRequest) -> Response:
    with tempfile.NamedTemporaryFile(suffix=".wav") as out:
        async with _lock:
            await asyncio.to_thread(_synth.synth, req.text, out.name, speaker_id=req.speaker, speech_rate=req.rate)
        return Response(Path(out.name).read_bytes(), media_type="audio/wav")
