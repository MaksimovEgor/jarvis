"""Ядро говорит само, без запроса пользователя: таймеры, напоминания,
объявления от Hermes cron.

asus: играет через тот же dmix, что mpv и listener, — поэтому звучит поверх
чего угодно; музыку на время объявления ставим на паузу тем же duck/unduck,
что и при голосовой команде.

Веб: сигнал+фраза склеиваются в один wav, браузеру уходит событие announce
со ссылкой, музыку он приглушает сам. Если браузер не на связи (iOS усыпляет
вкладку в фоне) — объявление звучит на asus, чтобы не потерялось.
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

from app.config import settings
from app.music import devices, media
from app.services import tts

logger = logging.getLogger("jarvis.speaker")

_CHIME_RATE = 24000
# Объявления не должны перебивать друг друга (два таймера на одну минуту).
_lock = asyncio.Lock()


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


async def _play(path: Path) -> None:
    # plug: — ресемплинг под фиксированный формат dmix.
    proc = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-D", f'plug:"{settings.audio_output_device}"', str(path),
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("aplay: %s", err.decode(errors="replace").strip())


async def announce(text: str, device: str = devices.ASUS, chime: bool = True, repeat: int = 1) -> str:
    """Возвращает устройство, на котором реально прозвучало."""
    if device != devices.ASUS and devices.web_output(device).connected:
        await _announce_web(text, device, chime, repeat)
        return device
    if device != devices.ASUS:
        logger.info("%s не на связи — объявляю на asus", device)
    await _announce_asus(text, chime, repeat)
    return devices.ASUS


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


async def _announce_asus(text: str, chime: bool, repeat: int) -> None:
    player = devices.player_for(devices.ASUS)
    async with _lock:
        with tempfile.NamedTemporaryFile(suffix=".wav") as speech:
            await tts.synthesize(text, Path(speech.name))
            await player.duck()
            try:
                for i in range(repeat):
                    if i:
                        await asyncio.sleep(1.5)
                    if chime:
                        await _play(_chime_path())
                    await _play(Path(speech.name))
            finally:
                await player.unduck()
