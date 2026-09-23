"""Ядро говорит само, без запроса пользователя: таймеры, напоминания,
объявления от Hermes cron.

Играет через тот же dmix, что mpv и listener, — поэтому звучит поверх
чего угодно; музыку на время объявления ставим на паузу тем же duck/unduck,
что и при голосовой команде.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import tempfile
import wave
from pathlib import Path

from app.config import settings
from app.music.player import player
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


async def announce(text: str, chime: bool = True, repeat: int = 1) -> None:
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
