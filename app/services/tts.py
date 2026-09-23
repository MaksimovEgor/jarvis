"""TTS: основной движок — Vosk-TTS (нейросетевой русский голос, onnx,
локально на asus), запасные — Edge TTS (Microsoft через туннель) и Piper.

Vosk: модель (~750 МБ) грузится один раз при старте ядра (warmup), синтез —
в отдельном потоке (CPU-работа не должна вешать event loop), по одному
куску за раз: onnx и так занимает все ядра.

Edge отдаёт русские голоса не на все IP (с asus напрямую — пусто), поэтому
ходит через SOCKS-туннель на VPS (`scripts/systemd/jarvis-tunnel.service`,
`EDGE_TTS_PROXY`). Если туннель/Microsoft недоступны — фраза озвучивается
Piper'ом, чтобы ассистент не онемел.

Выход всегда WAV: listener играет его через aplay, а тот не умеет mp3.

Почему Edge «часто» уступал Piper (NoAudioReceived — Microsoft принял
запрос, но звука не вернул):
- стабильно — на эмодзи и тексте без букв → текст чистится (clean);
- случайно — тот же текст то проходит, то нет, длинный бывал и по 146с →
  длинное режется на куски по фразам, куски синтезируются параллельно, каждый
  с повторной попыткой; Piper — только если Edge не справился и со второго раза.

Piper — через CLI-бинарь (ставится вместе с pip-пакетом piper-tts, apt не
нужен), а не python-биндинг: python API piper-tts на PyPI периодически
меняется, а CLI-контракт стабилен.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import tempfile
import unicodedata
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable

import edge_tts
from aiohttp_socks import ProxyConnector

from app.config import settings

logger = logging.getLogger("jarvis.tts")

# Бинарь из того же venv, а не из PATH: под systemd venv в PATH нет.
_PIPER_BIN = str(Path(sys.executable).with_name("piper"))


_CHUNK_CHARS = 350
_EDGE_ATTEMPTS = 2
_EDGE_PARALLEL = 3
_EDGE_CHUNK_TIMEOUT = 20.0


def clean(text: str) -> str:
    """Без эмодзи/символов и markdown, в одну строку — на них Edge молчит."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("So", "Sk", "Cs", "Co", "Mn") or ch.isalnum())
    text = re.sub(r"[*_#`>|~]+", " ", text)
    return " ".join(text.split())


def _chunks(text: str) -> list[str]:
    """Куски по целым фразам, не длиннее _CHUNK_CHARS (одну длинную фразу — как есть)."""
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.!?…;:])\s+", text):
        if current and len(current) + len(sentence) + 1 > _CHUNK_CHARS:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    return parts + [current] if current else parts


def _silence(out_path: Path, seconds: float = 0.2) -> Path:
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * int(24000 * seconds))
    return out_path


async def synthesize(text: str, out_path: Path) -> Path:
    cleaned = clean(text)
    if not any(ch.isalnum() for ch in cleaned):
        # «...», одни эмодзи — произносить нечего (Piper на таком падает).
        return _silence(out_path)
    engines = {"vosk": (_synthesize_vosk, 1), "edge": (_edge_with_retry, _EDGE_PARALLEL)}
    if settings.tts_engine in engines:
        one, parallel = engines[settings.tts_engine]
        try:
            return await _chunked(cleaned, out_path, one, parallel)
        except Exception as exc:
            logger.warning("%s TTS не сработал (%s: %s) — озвучиваю Piper'ом",
                           settings.tts_engine, type(exc).__name__, exc)
    return await _synthesize_piper(cleaned, out_path)


async def _chunked(
    text: str, out_path: Path, one: Callable[[str, Path], Awaitable[Path]], parallel: int,
) -> Path:
    """Длинное — кусками по фразам (параллельно, если движок позволяет) и склейка."""
    chunks = _chunks(text)
    if len(chunks) == 1:
        return await one(chunks[0], out_path)
    limit = asyncio.Semaphore(parallel)
    with tempfile.TemporaryDirectory() as tmp:
        paths = [Path(tmp) / f"{i}.wav" for i in range(len(chunks))]

        async def part(chunk: str, path: Path) -> None:
            async with limit:
                await one(chunk, path)

        await asyncio.gather(*(part(c, p) for c, p in zip(chunks, paths)))
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in paths))
        await _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", str(out_path)])
    return out_path


_vosk: Any = None
_vosk_lock = asyncio.Lock()


def _load_vosk() -> Any:
    global _vosk
    if _vosk is None:
        from vosk_tts import Model, Synth  # тяжёлый импорт — только если нужен

        _vosk = Synth(Model(model_path=settings.vosk_tts_model_path))
    return _vosk


async def warmup() -> None:
    """Загрузить модель при старте, а не на первой фразе (~10 с)."""
    if settings.tts_engine == "vosk":
        try:
            async with _vosk_lock:
                await asyncio.to_thread(_load_vosk)
            logger.info("Vosk TTS загружен")
        except Exception:
            logger.exception("Vosk TTS не загрузился — будет Piper")


async def _synthesize_vosk(text: str, out_path: Path) -> Path:
    async with _vosk_lock:
        synth = await asyncio.to_thread(_load_vosk)
        await asyncio.to_thread(
            synth.synth, text, str(out_path),
            speaker_id=settings.vosk_tts_speaker, speech_rate=settings.vosk_tts_rate,
        )
    return out_path


async def _edge_with_retry(text: str, out_path: Path) -> Path:
    # Общий лимит на кусок: Microsoft бывает отдаёт звук по капле, и
    # receive_timeout не срабатывает (длинный ответ шёл 146с).
    for attempt in range(1, _EDGE_ATTEMPTS):
        try:
            return await asyncio.wait_for(_synthesize_edge(text, out_path), _EDGE_CHUNK_TIMEOUT)
        except Exception as exc:
            logger.info("Edge TTS: попытка %d не удалась (%s), повторяю", attempt, type(exc).__name__)
    return await asyncio.wait_for(_synthesize_edge(text, out_path), _EDGE_CHUNK_TIMEOUT)


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
