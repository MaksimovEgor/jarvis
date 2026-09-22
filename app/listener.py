"""Постоянно слушающий клиент: wake-word (openWakeWord) → запись команды по VAD
(встроенный в openWakeWord Silero VAD) → POST /chat/audio → проигрывание ответа
через aplay.

Микрофон читается напрямую через `arecord` (без PortAudio/sounddevice — это
лишний системный пакет и лишний sudo apt). Отдельный процесс от FastAPI-ядра
специально: при переезде на микрокомпьютер с колонкой переезжает только этот
файл, core остаётся как есть и вызывается по сети.

Сейчас использует штатную модель openWakeWord `hey_jarvis` (по-английски) —
рабочая заглушка, пока не обучена кастомная модель под русское «Джарвис»
(WAKE_MODEL_PATH тогда меняется на неё, остальной код не трогается).

Аудио — напрямую через ALSA (`plughw:0,0`), без PipeWire: встроенный микрофон
на этом ноутбуке не отдавал сигнал ни через один слой PipeWire (ACP отдаёт
только один input-роут и не переключается на гарнитуру даже при вставленном
джеке), а PipeWire выключен намеренно (`systemctl --user mask ...`). Сейчас
источник — микрофон проводной гарнитуры в 3.5mm джеке (ALSA `Capture Source`
= Headset Mic, выставляется здесь же при старте). Если вернётесь на встроенный
микрофон — поменять `_CAPTURE_SOURCE_ITEM` на 0 и разобраться, почему на пине
0x1b молчит EAPD (см. README/история чата).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import subprocess
import tempfile
import wave
from pathlib import Path

import httpx
import numpy as np
from openwakeword.model import Model
from openwakeword.vad import VAD

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.listener")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # 80мс — шаг, которого ждёт openWakeWord

_PKG_MODELS = Path(__file__).resolve().parents[1] / ".venv/lib/python3.12/site-packages/openwakeword/resources/models"
WAKE_MODEL_PATH = str(_PKG_MODELS / "hey_jarvis_v0.1.onnx")

WAKE_THRESHOLD = 0.5
WAKE_NEAR_MISS = 0.2
VAD_SPEECH_THRESHOLD = 0.5
SILENCE_FRAMES_TO_STOP = 15  # ~1.2с тишины после речи останавливает запись команды
MAX_COMMAND_FRAMES = 150  # ~12с — защита от зависшей записи
CORE_URL = "http://127.0.0.1:8000"

ALSA_DEVICE = "plughw:0,0"
_CAPTURE_SOURCE_ITEM = 1  # 0=Internal Mic, 1=Headset Mic, 2=Internal Mic 1 (см. `amixer -c0 cget numid=6`)
_CAPTURE_VOLUME = 35  # из 63 — микрофон гарнитуры клипует на максимуме, см. README


def _configure_alsa() -> None:
    """ALSA-состояние не переживает перезагрузку без `alsactl store` (нужен
    root) — проще выставлять при каждом старте, идемпотентно."""
    subprocess.run(["amixer", "-c0", "cset", "numid=6", str(_CAPTURE_SOURCE_ITEM)], stdout=subprocess.DEVNULL)
    subprocess.run(["amixer", "-c0", "cset", "numid=7", str(_CAPTURE_VOLUME)], stdout=subprocess.DEVNULL)


def _mic_frames():
    proc = subprocess.Popen(
        ["arecord", "-D", ALSA_DEVICE, "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    frame_bytes = FRAME_SAMPLES * 2
    try:
        while True:
            # BufferedReader.read(n) блокируется, пока не наберёт n байт или
            # не встретит EOF — короткий возврат здесь означает, что arecord
            # реально закончился (упал/отдали устройство), а не что "неудачно
            # прочиталось". Раньше это молча обрывало прослушку без единой
            # строчки в логе.
            data = proc.stdout.read(frame_bytes)
            if len(data) < frame_bytes:
                err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
                logger.error("arecord завершился (код %s): %s", proc.poll(), err or "(без сообщения)")
                break
            yield np.frombuffer(data, dtype=np.int16)
    finally:
        proc.terminate()


def _play(path: Path) -> None:
    subprocess.run(["aplay", "-D", ALSA_DEVICE, "-q", str(path)], check=False)


async def _handle_command(frames: list[np.ndarray]) -> None:
    pcm = np.concatenate(frames).tobytes()
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_in:
        with wave.open(tmp_in.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)

        # С запасом над HERMES_TIMEOUT ядра: агентный ход с инструментами бывает долгим.
        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(tmp_in.name, "rb") as f:
                resp = await client.post(
                    f"{CORE_URL}/chat/audio",
                    files={"file": ("command.wav", f, "audio/wav")},
                )
    resp.raise_for_status()
    data = resp.json()

    logger.info("Услышал: %s", data["transcript"])
    logger.info("Ответ: %s", data["reply"])

    if data.get("audio_base64"):
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_out:
            Path(tmp_out.name).write_bytes(base64.b64decode(data["audio_base64"]))
            _play(Path(tmp_out.name))


async def main() -> None:
    _configure_alsa()
    wake_model = Model(wakeword_model_paths=[WAKE_MODEL_PATH])
    wake_key = next(iter(wake_model.models.keys()))
    vad = VAD()
    logger.info("Слушаю (модель: %s)…", wake_key)

    recording: list[np.ndarray] | None = None
    heard_speech = False
    silence_run = 0

    for frame in _mic_frames():
        # predict() зовём на каждом кадре, даже во время записи команды: у
        # openWakeWord внутри кольцевой буфер фич, который обновляется только
        # через predict(). Если его не кормить, после команды модель видит
        # всё ещё старое «Hey Jarvis» и сразу срабатывает повторно.
        score = wake_model.predict(frame).get(wake_key, 0.0)
        if recording is None:
            if WAKE_NEAR_MISS < score <= WAKE_THRESHOLD:
                # Видно, что фраза «почти» распозналась — нужно для подбора
                # порога и громкости микрофона.
                logger.info("Почти wake word: score=%.2f", score)
            if score > WAKE_THRESHOLD:
                logger.info("Сработал wake word — записываю команду…")
                recording = []
                heard_speech = False
                silence_run = 0
            continue

        recording.append(frame)
        is_speech = vad.predict(frame) > VAD_SPEECH_THRESHOLD
        if is_speech:
            heard_speech = True
            silence_run = 0
        elif heard_speech:
            silence_run += 1

        timed_out = len(recording) >= MAX_COMMAND_FRAMES
        finished_speaking = heard_speech and silence_run >= SILENCE_FRAMES_TO_STOP

        if timed_out or finished_speaking:
            frames, recording = recording, None
            wake_model.reset()
            if not heard_speech:
                logger.info("Ложное срабатывание — команды не было, снова слушаю.")
                continue
            try:
                await _handle_command(frames)
            except Exception:
                logger.exception("Не получилось обработать команду")


if __name__ == "__main__":
    asyncio.run(main())
