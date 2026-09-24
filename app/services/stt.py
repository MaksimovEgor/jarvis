"""STT локально на asus, модель грузится прямо в процесс (задержка на сеть
не нужна).

Основной движок — GigaAM v3 (Сбер) через onnx-asr: на русском ошибается
заметно реже Whisper, особенно на дальнем/шумном звуке, и не галлюцинирует
на тишине («Продолжение следует…»). Вариант e2e — сразу с пунктуацией и
цифрами, как отдавал Whisper. Модель качается один раз в
data/models/gigaam-v3 (см. README) — не в кэш HF: onnxruntime 1.24.1 не
открывает модели по симлинкам кэша.

На asus — int8 на CPU (STT_DEVICE=cpu): 0,26 с на фразу и 0,33 ГБ RAM.
На GPU (GTX 1050) — 0,06 с, но процесс держит ~1 ГБ RAM (CUDA), а память на
asus — узкое место. STT_DEVICE=cuda: fp32 на CUDA (onnxruntime-gpu 1.24.1,
cuDNN 9.5 — новые Pascal не поддерживают), не поднялось — int8 на CPU.

STT_ENGINE=whisper — прежний faster-whisper (откат, если GigaAM подведёт).
Декодирование любых форматов (webm/opus из браузера) — всё равно через
faster_whisper.decode_audio (PyAV): onnx-asr сам читает только WAV.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import numpy as np
from faster_whisper import decode_audio

from app.config import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# GigaAM рассчитан на фразы до ~25 с; длиннее — режем по паузам.
_MAX_CHUNK_S = 22
_MIN_CHUNK_S = 15

Recognizer = Callable[[np.ndarray], str]


def _gigaam() -> Recognizer:
    import onnx_asr
    import onnxruntime

    path = settings.gigaam_model_path
    if settings.stt_device == "cuda":
        try:
            onnxruntime.preload_dlls()
            model = onnx_asr.load_model(
                settings.gigaam_model, path,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            logger.info("GigaAM %s на GPU", settings.gigaam_model)
            return lambda audio: model.recognize(audio, sample_rate=SAMPLE_RATE)
        except Exception:
            logger.exception("GigaAM на GPU не поднялся — int8 на CPU")
    model = onnx_asr.load_model(settings.gigaam_model, path, quantization="int8", providers=["CPUExecutionProvider"])
    logger.info("GigaAM %s на CPU (int8)", settings.gigaam_model)
    return lambda audio: model.recognize(audio, sample_rate=SAMPLE_RATE)


def _whisper() -> Recognizer:
    from faster_whisper import WhisperModel

    logger.info(
        "Загружаю faster-whisper %s (%s/%s)",
        settings.stt_model_size, settings.stt_device, settings.stt_compute_type,
    )
    if settings.stt_device == "cuda":
        try:
            import onnxruntime

            # ctranslate2 находит CUDA/cuDNN, после того как onnxruntime загрузил их в процесс.
            onnxruntime.preload_dlls()
            model = WhisperModel(settings.stt_model_size, device="cuda", compute_type=settings.stt_compute_type)
        except Exception:
            logger.exception("Whisper на GPU не поднялся — запасной small на CPU")
            model = WhisperModel("small", device="cpu", compute_type="int8")
    else:
        model = WhisperModel(settings.stt_model_size, device=settings.stt_device, compute_type=settings.stt_compute_type)

    def recognize(audio: np.ndarray) -> str:
        segments, _info = model.transcribe(audio, language=settings.stt_language or None, vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments)

    return recognize


@lru_cache(maxsize=1)
def _model() -> Recognizer:
    return _whisper() if settings.stt_engine == "whisper" else _gigaam()


async def warmup() -> None:
    """Загрузить модель при старте, а не на первой команде."""
    try:
        await asyncio.to_thread(_model)
        logger.info("STT загружен (%s)", settings.stt_engine)
    except Exception:
        logger.exception("STT не загрузился")


def _chunks(audio: np.ndarray) -> list[np.ndarray]:
    """Длинную запись — на куски до _MAX_CHUNK_S, разрез в самом тихом месте
    окна [_MIN_CHUNK_S, _MAX_CHUNK_S], чтобы не рубить слово пополам."""
    frame = SAMPLE_RATE // 10
    out = []
    while len(audio) > _MAX_CHUNK_S * SAMPLE_RATE:
        window = audio[_MIN_CHUNK_S * SAMPLE_RATE : _MAX_CHUNK_S * SAMPLE_RATE]
        energy = (window[: len(window) // frame * frame].reshape(-1, frame) ** 2).mean(axis=1)
        cut = _MIN_CHUNK_S * SAMPLE_RATE + int(energy.argmin()) * frame + frame // 2
        out.append(audio[:cut])
        audio = audio[cut:]
    out.append(audio)
    return out


def _transcribe_sync(path: Path) -> str:
    audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
    recognize = _model()
    if settings.stt_engine == "whisper":
        return recognize(audio).strip()
    return " ".join(recognize(chunk).strip() for chunk in _chunks(audio)).strip()


async def transcribe(path: Path) -> str:
    return await asyncio.to_thread(_transcribe_sync, path)
