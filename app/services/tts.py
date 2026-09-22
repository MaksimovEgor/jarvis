"""TTS: основной движок — Edge TTS (нейроголоса Microsoft, бесплатно, без
ключа), запасной — Piper локально.

Edge отдаёт русские голоса не на все IP (с asus напрямую — пусто), поэтому
ходит через SOCKS-туннель на VPS (`scripts/systemd/jarvis-tunnel.service`,
`EDGE_TTS_PROXY`). Если туннель/Microsoft недоступны — фраза озвучивается
Piper'ом, чтобы ассистент не онемел.

Выход всегда WAV: listener играет его через aplay, а тот не умеет mp3.

Piper — через CLI-бинарь (ставится вместе с pip-пакетом piper-tts, apt не
нужен), а не python-биндинг: python API piper-tts на PyPI периодически
меняется, а CLI-контракт стабилен.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import edge_tts
from aiohttp_socks import ProxyConnector

from app.config import settings

logger = logging.getLogger("jarvis.tts")

# Бинарь из того же venv, а не из PATH: под systemd venv в PATH нет.
_PIPER_BIN = str(Path(sys.executable).with_name("piper"))


async def synthesize(text: str, out_path: Path) -> Path:
    if settings.tts_engine == "edge":
        try:
            return await _synthesize_edge(text, out_path)
        except Exception as exc:
            logger.warning("Edge TTS недоступен (%s: %s) — озвучиваю Piper'ом", type(exc).__name__, exc)
    return await _synthesize_piper(text, out_path)


async def _synthesize_edge(text: str, out_path: Path) -> Path:
    connector = ProxyConnector.from_url(settings.edge_tts_proxy) if settings.edge_tts_proxy else None
    communicate = edge_tts.Communicate(
        text, settings.edge_tts_voice,
        rate=settings.edge_tts_rate, pitch=settings.edge_tts_pitch,
        connector=connector, connect_timeout=5, receive_timeout=20,
    )
    with tempfile.NamedTemporaryFile(suffix=".mp3") as mp3:
        await communicate.save(mp3.name)
        await _run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3.name, "-ar", "24000", "-ac", "1", str(out_path)])
    return out_path


async def _synthesize_piper(text: str, out_path: Path) -> Path:
    voice = Path(settings.tts_voice_path)
    if not voice.exists():
        raise RuntimeError(f"Голос Piper не найден: {voice}. См. README (скачать голос).")
    await _run([_PIPER_BIN, "--model", str(voice), "--output_file", str(out_path)], stdin=text.encode("utf-8"))
    return out_path


async def _run(cmd: list[str], stdin: bytes | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate(stdin)
    if proc.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} упал: {stderr.decode(errors='replace')}")
