"""STT: faster-whisper локально на asus (в отличие от point/backend, где
это self-host сервис на арендованном GPU-боксе за OpenAI-совместимым HTTP —
здесь модель грузится прямо в процесс, задержка на сеть не нужна).
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path

from faster_whisper import WhisperModel

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _model() -> WhisperModel:
    logger.info(
        "Загружаю faster-whisper %s (%s/%s)",
        settings.stt_model_size, settings.stt_device, settings.stt_compute_type,
    )
    return WhisperModel(
        settings.stt_model_size,
        device=settings.stt_device,
        compute_type=settings.stt_compute_type,
    )


def _transcribe_sync(path: Path) -> str:
    segments, _info = _model().transcribe(
        str(path),
        language=settings.stt_language or None,
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


async def transcribe(path: Path) -> str:
    return await asyncio.to_thread(_transcribe_sync, path)
