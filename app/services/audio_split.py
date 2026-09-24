"""Нарезка звука по паузам: GigaAM рассчитан на фразы до ~25 с.

Отдельный лёгкий модуль (только numpy): его берут и ядро (stt.py), и
GPU-воркер (app/transcribe_worker.py), которому не нужен faster-whisper.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
MAX_CHUNK_S = 22
MIN_CHUNK_S = 15


def quiet_cut(audio: np.ndarray) -> int:
    """Где резать запись длиннее MAX_CHUNK_S: самое тихое место окна
    [MIN_CHUNK_S, MAX_CHUNK_S], чтобы не рубить слово пополам."""
    frame = SAMPLE_RATE // 10
    window = audio[MIN_CHUNK_S * SAMPLE_RATE : MAX_CHUNK_S * SAMPLE_RATE]
    energy = (window[: len(window) // frame * frame].reshape(-1, frame) ** 2).mean(axis=1)
    return MIN_CHUNK_S * SAMPLE_RATE + int(energy.argmin()) * frame + frame // 2


def split(audio: np.ndarray) -> list[np.ndarray]:
    out = []
    while len(audio) > MAX_CHUNK_S * SAMPLE_RATE:
        cut = quiet_cut(audio)
        out.append(audio[:cut])
        audio = audio[cut:]
    out.append(audio)
    return out
