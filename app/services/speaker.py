"""Ядро говорит само, без запроса пользователя: таймеры, напоминания,
объявления от Hermes cron.

Звучит только на устройстве пользователя (телефон, Mac): сигнал+фраза
склеиваются в один wav, браузеру уходит событие announce со ссылкой, музыку
он приглушает сам. asus — сервер, у него звука нет: браузер не на связи
(iOS усыпляет вкладку в фоне) — пуш, а без подписки на пуши — Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import tempfile
import uuid
import wave
from pathlib import Path

from app.music import devices, media
from app.services import telegram, tts, webpush

logger = logging.getLogger("jarvis.speaker")

_CHIME_RATE = 24000


def _chime_path() -> Path:
    """Два коротких тона «динь-дон», генерируются один раз."""
    path = Path(tempfile.gettempdir()) / "jarvis-chime.wav"
    if path.exists():
        return path
    frames = bytearray()
    for freq, seconds in ((880, 0.18), (0, 0.06), (660, 0.3)):
        for i in range(int(_CHIME_RATE * seconds)):
            fade = min(1.0, (int(_CHIME_RATE * seconds) - i) / 2000)  # без щелчка в конце
            sample = 0.3 * fade * math.sin(2 * math.pi * freq * i / _CHIME_RATE) if freq else 0.0
            frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_CHIME_RATE)
        wf.writeframes(bytes(frames))
    return path


async def announce(text: str, device: str = devices.ASUS, chime: bool = True, repeat: int = 1) -> str:
    """Как дошло — фраза для ответа инструмента.

    asus — только сервер, не звучит: экран открыт → звук там; свёрнут → пуш;
    подписки на пуши нет или устройство неизвестно → сообщение в Telegram,
    чтобы таймер/напоминание не потерялись."""
    web = devices.is_web(device)
    pushed = 0
    if web and not devices.web_output(device).watching:
        pushed = await webpush.notify(device, "Джарвис", text)
    if web and devices.web_output(device).connected:
        await _announce_web(text, device, chime, repeat)
        return "Сказал."
    if pushed:
        return "Экран свёрнут — прислал уведомление."
    try:
        await telegram.send(text)
        return "Устройство не на связи — написал в Telegram."
    except Exception:
        logger.exception("Не дошло ни пушем, ни в Telegram: %s", text)
        return "Не получилось доставить: устройство не на связи, Telegram недоступен."


async def _announce_web(text: str, device: str, chime: bool, repeat: int) -> None:
    out = Path(tempfile.gettempdir()) / f"jarvis-announce-{uuid.uuid4().hex}.wav"
    with tempfile.NamedTemporaryFile(suffix=".wav") as speech:
        await tts.synthesize(text, Path(speech.name))
        parts = ([str(_chime_path())] if chime else []) + [speech.name]
        parts = parts * repeat
        inputs = [arg for part in parts for arg in ("-i", part)]
        mix = "".join(f"[{i}:a]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", mix,
            "-ar", str(_CHIME_RATE), "-ac", "1", str(out),
        )
        await proc.wait()
    devices.web_output(device).announce(f"media/ref/{media.register(str(out))}")
    # Браузер заберёт файл за секунды; через 10 минут он уже не нужен.
    asyncio.get_running_loop().call_later(600, lambda: out.unlink(missing_ok=True))
