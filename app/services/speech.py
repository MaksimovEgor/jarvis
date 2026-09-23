"""Потоковая озвучка ответа: длинный ответ начинает звучать через секунду,
а не после синтеза целиком (1500 символов на CPU asus — ~45 с).

    ответ ─► куски по фразам: первый короткий (быстро синтезируется),
             каждый следующий не больше чем вдвое длиннее предыдущего
    кусок 0 ─► синтезируется сразу, уходит в ответе /chat/*
    кусок n ─► клиент забирает GET /tts/chunk/<id>/<n>; вместе с ним ядро
               начинает синтез n+1, чтобы тот был готов, пока звучит n

Синтез ленивый, с опережением на один кусок: если пользователь перебил и
клиент перестал забирать куски, лишнего не синтезируется (Vosk один и
занимает все ядра — зря занятый синтез задержал бы следующий ответ).
"""

from __future__ import annotations

import asyncio
import base64
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.services import tts

FIRST_CHARS = 100
MAX_CHARS = 400
# Синтез идёт ~0,4 от длительности звука: кусок вдвое длиннее предыдущего
# успевает синтезироваться, пока предыдущий звучит, — без пауз.
GROWTH = 2
JOB_TTL = 600.0


@dataclass
class Spoken:
    """Озвучка ответа: первый кусок сразу (или тоже по id), остальные — по id."""
    audio_base64: str | None
    job_id: str | None = None
    count: int = 1


@dataclass
class _Job:
    chunks: list[str]
    parts: dict[int, asyncio.Task[bytes]] = field(default_factory=dict)
    created: float = field(default_factory=time.time)


_jobs: dict[str, _Job] = {}


def _split(text: str) -> list[str]:
    """По фразам: первый кусок короткий, каждый следующий не больше чем в
    GROWTH раз длиннее предыдущего (и не больше MAX_CHARS)."""
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?…;:])\s+", text):
        limit = FIRST_CHARS if not chunks else min(MAX_CHARS, GROWTH * len(chunks[-1]))
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    return chunks + [current] if current else chunks


async def _synth(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        await tts.synthesize(text, Path(f.name))
        return Path(f.name).read_bytes()


def _ensure(job: _Job, n: int) -> None:
    if 0 <= n < len(job.chunks) and n not in job.parts:
        job.parts[n] = asyncio.create_task(_synth(job.chunks[n]))


def _prune() -> None:
    now = time.time()
    for job_id in [j for j, job in _jobs.items() if now - job.created > JOB_TTL]:
        for task in _jobs.pop(job_id).parts.values():
            task.cancel()


async def speak(text: str, inline: bool = True) -> Spoken:
    """inline=False — ответ уходит событием SSE (веб): тогда и первый кусок
    клиент забирает по id, а не в base64 внутри события — события хранятся
    для повтора переподключившемуся браузеру и должны быть лёгкими."""
    _prune()
    chunks = _split(tts.clean(text))
    if inline and len(chunks) <= 1:
        return Spoken(base64.b64encode(await _synth(text)).decode())
    job_id = uuid.uuid4().hex
    job = _jobs[job_id] = _Job(chunks or [text])
    first = await chunk(job_id, 0)
    return Spoken(base64.b64encode(first).decode() if inline else None, job_id, len(job.chunks))


async def chunk(job_id: str, n: int) -> bytes:
    job = _jobs.get(job_id)
    if job is None or not 0 <= n < len(job.chunks):
        raise KeyError(n)
    _ensure(job, n)
    _ensure(job, n + 1)  # опережение: следующий готовится, пока звучит этот
    return await job.parts[n]
