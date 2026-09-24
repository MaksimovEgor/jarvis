"""Расшифровка длинного аудио отдельным процессом: запускается на задачу и
завершается, модель в памяти не живёт.

    python -m app.transcribe_worker <вход> <выход.txt> [cuda|cpu]

Зачем отдельный процесс: CUDA в процессе держит ~1 ГБ RAM, а на asus память —
узкое место (ядро с GigaAM на CPU — 0,46 ГБ). Здесь CUDA живёт минуты задачи.
GigaAM на GTX 1050 — ~35× быстрее реального времени (час — ~2 мин), на CPU —
~8×. Звук читается потоком из ffmpeg: трёхчасовой подкаст целиком в память
(~700 МБ float32) не грузится.

Вывод — строки «[мм:сс] текст» на каждый кусок до ~22 с: по меткам можно
ответить «на какой минуте про это говорили». Пишется по мере работы.
Код выхода 0 — готово; иначе — текст ошибки в stderr.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from app.config import settings
from app.services.audio_split import MAX_CHUNK_S, SAMPLE_RATE, quiet_cut

_READ = SAMPLE_RATE * 2 * 5  # 5 с s16le за чтение


def _pcm(path: Path) -> Iterator[np.ndarray]:
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
         "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    while block := proc.stdout.read(_READ):
        yield np.frombuffer(block[: len(block) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg не прочитал {path.name} (код {proc.returncode})")


def _chunks(path: Path) -> Iterator[tuple[float, np.ndarray]]:
    """(начало в секундах, кусок) — разрез в самом тихом месте."""
    buf = np.zeros(0, dtype=np.float32)
    start = 0
    for block in _pcm(path):
        buf = np.concatenate([buf, block])
        while len(buf) > MAX_CHUNK_S * SAMPLE_RATE:
            cut = quiet_cut(buf)
            yield start / SAMPLE_RATE, buf[:cut]
            start += cut
            buf = buf[cut:]
    if len(buf) > SAMPLE_RATE // 2:
        yield start / SAMPLE_RATE, buf


def _model(device: str):  # noqa: ANN202 — тип модели onnx-asr внутренний
    import onnx_asr
    import onnxruntime

    if device == "cuda":
        onnxruntime.preload_dlls()
        return onnx_asr.load_model(
            settings.gigaam_model, settings.gigaam_model_path,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
    return onnx_asr.load_model(
        settings.gigaam_model, settings.gigaam_model_path, quantization="int8", providers=["CPUExecutionProvider"],
    )


def main(src: Path, out: Path, device: str) -> None:
    model = _model(device)
    with out.open("w", encoding="utf-8") as f:
        for start, chunk in _chunks(src):
            text = model.recognize(chunk, sample_rate=SAMPLE_RATE).strip()
            if text:
                minutes, seconds = divmod(int(start), 60)
                f.write(f"[{minutes:02d}:{seconds:02d}] {text}\n")
                f.flush()


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "cuda")
