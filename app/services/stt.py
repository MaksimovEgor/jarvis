"""STT: faster-whisper локально на asus (в отличие от point/backend, где
это self-host сервис на арендованном GPU-боксе за OpenAI-совместимым HTTP —
здесь модель грузится прямо в процесс, задержка на сеть не нужна).

GPU (GTX 1050, 2 ГБ): medium/int8 распознаёт фразу за ~1,6с против ~2,5с у
small на CPU и точнее. Библиотеки CUDA 12/cuDNN — из pip (onnxruntime-gpu
1.24.1, cuDNN 9.5: CUDA 13 и новые cuDNN Pascal уже не поддерживают);
ctranslate2 находит их, после того как onnxruntime загрузил их в процесс
(preload_dlls). Не поднялось на GPU — запасной small на CPU, чтобы голос
не отвалился совсем.
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
    if settings.stt_device == "cuda":
        try:
            import onnxruntime

            onnxruntime.preload_dlls()
            return WhisperModel(settings.stt_model_size, device="cuda", compute_type=settings.stt_compute_type)
        except Exception:
            logger.exception("Whisper на GPU не поднялся — запасной small на CPU")
            return WhisperModel("small", device="cpu", compute_type="int8")
    return WhisperModel(
        settings.stt_model_size,
        device=settings.stt_device,
        compute_type=settings.stt_compute_type,
    )


async def warmup() -> None:
    """Загрузить модель при старте, а не на первой команде."""
    try:
        await asyncio.to_thread(_model)
        logger.info("Whisper загружен")
    except Exception:
        logger.exception("Whisper не загрузился")


def _transcribe_sync(path: Path) -> str:
    segments, _info = _model().transcribe(
        str(path),
        language=settings.stt_language or None,
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


async def transcribe(path: Path) -> str:
    return await asyncio.to_thread(_transcribe_sync, path)
