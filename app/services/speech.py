"""Потоковая озвучка ответа: длинный ответ начинает звучать через секунду,
а не после синтеза целиком (1500 символов на CPU asus — ~45 с).

    ответ ─► куски по фразам: первый короткий (быстро синтезируется),
             каждый следующий не больше чем вдвое длиннее предыдущего
    кусок 0 ─► синтезируется сразу, уходит в ответе /chat/*
    кусок n ─► клиент забирает GET /tts/chunk/<id>/<n>; вместе с ним ядро
               начинает синтез n+1, чтобы тот был готов, пока звучит n

Растущая озвучка (SpeechStream) — ответ ещё пишется, а голос уже звучит:

    LLM ─ фраза ─► feed ─► кусок 0 сразу в синтез; дальше фразы копятся
                           в «хвосте», пока кусок не наберёт размер
    клиент ─ GET кусок n ─► есть — отдаём; нет, но хвост непуст — хвост
                           становится куском n (клиент готов играть дальше);
                           нет ничего — ждём фразу; озвучка закрыта — 204

Первое предложение длиннее FIRST_CHARS режется по запятой/тире: Vosk
тратит ~25 мс на символ, и 120 символов первой фразы — это 3 с тишины.

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
from typing import Callable

from app import metrics
from app.services import tts

# Первый кусок короткий: 60 символов синтезируются ~1,2 с — голос начинается
# почти сразу (100 символов было ~2,4 с).
FIRST_CHARS = 60
MAX_CHARS = 400
# Вслух — не больше ~30 секунд: длинный ответ целиком остаётся на экране.
SPOKEN_MAX_CHARS = 450
_MORE_ON_SCREEN = "Остальное — на экране."
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
    # Растущая озвучка: куски ещё добавляются (SpeechStream).
    open: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    # Хвост растущей озвучки — сделать куском прямо сейчас (клиент ждёт).
    seal: Callable[[], None] | None = None


_jobs: dict[str, _Job] = {}


# Где можно резать первое предложение: запятая, точка с запятой, двоеточие, тире.
_CLAUSE = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–]\s)")
# Короче — обрывок («Итак,»), такой кусок звучит странно.
MIN_FIRST_CHARS = 15


def _first_cut(sentence: str) -> list[str]:
    """Длинное первое предложение → [начало до запятой у FIRST_CHARS, остаток].
    Режем по последней границе не дальше FIRST_CHARS; если такой нет — по
    первой после; нет запятых вовсе — не режем."""
    if len(sentence) <= FIRST_CHARS:
        return [sentence]
    cuts = [m.start() for m in _CLAUSE.finditer(sentence) if m.start() >= MIN_FIRST_CHARS]
    before = [c for c in cuts if c <= FIRST_CHARS]
    cut = before[-1] if before else next(iter(cuts), None)
    if cut is None or len(sentence) - cut < MIN_FIRST_CHARS:
        return [sentence]
    return [sentence[:cut].strip(), sentence[cut:].strip()]


def _split(text: str) -> list[str]:
    """По фразам: первый кусок короткий, каждый следующий не больше чем в
    GROWTH раз длиннее предыдущего (и не больше MAX_CHARS)."""
    chunks: list[str] = []
    current = ""
    sentences = re.split(r"(?<=[.!?…;:])\s+", text)
    for sentence in [*_first_cut(sentences[0]), *sentences[1:]]:
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


async def _synth_first(text: str) -> bytes:
    wav = await _synth(text)
    metrics.mark("tts_first")  # задача наследует секундомер хода, который начал озвучку
    return wav


def _ensure(job: _Job, n: int) -> None:
    if 0 <= n < len(job.chunks) and n not in job.parts:
        job.parts[n] = asyncio.create_task(_synth_first(job.chunks[n]) if n == 0 else _synth(job.chunks[n]))


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
    text = for_voice(tts.clean(text))
    chunks = _split(text)
    if inline and len(chunks) <= 1:
        return Spoken(base64.b64encode(await _synth(text)).decode())
    job_id = uuid.uuid4().hex
    job = _jobs[job_id] = _Job(chunks or [text])
    if not inline:
        # Веб заберёт кусок 0 по id — текст ответа уходит на экран сразу,
        # не дожидаясь синтеза (раньше ждал его ~1-2 с).
        _ensure(job, 0)
        return Spoken(None, job_id, len(job.chunks))
    _ensure(job, 0)
    _ensure(job, 1)
    first = await job.parts[0]
    return Spoken(base64.b64encode(first).decode(), job_id, len(job.chunks))


async def first_ready(spoken: Spoken | None, timeout: float = 30.0) -> None:
    """Дождаться синтеза первого куска (для замера), не отменяя его и не падая."""
    job = _jobs.get(spoken.job_id) if spoken and spoken.job_id else None
    task = job.parts.get(0) if job else None
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except Exception:
        pass


def for_voice(text: str) -> str:
    """Столько целых фраз, сколько влезает в SPOKEN_MAX_CHARS, — иначе модель,
    выдавшая простыню, читала бы её минутами."""
    if len(text) <= SPOKEN_MAX_CHARS:
        return text
    spoken = ""
    for sentence in re.split(r"(?<=[.!?…])\s+", text):
        if spoken and len(spoken) + len(sentence) + 1 > SPOKEN_MAX_CHARS:
            break
        spoken = f"{spoken} {sentence}".strip()
    if len(spoken) > SPOKEN_MAX_CHARS:  # одна огромная фраза без точек
        spoken = spoken[:SPOKEN_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return f"{spoken} {_MORE_ON_SCREEN}"


class SpeechStream:
    """Растущая озвучка: фразы приходят по одной (LLM ещё пишет ответ)."""

    def __init__(self) -> None:
        _prune()
        self.job_id = uuid.uuid4().hex
        self._job = _jobs[self.job_id] = _Job([], open=True)
        self._job.seal = self._seal
        self._tail = ""
        self._chars = 0
        self._cut = False

    @property
    def opened(self) -> bool:
        return bool(self._job.chunks or self._tail)

    def feed(self, sentence: str) -> None:
        sentence = tts.clean(sentence)
        if not sentence or self._cut:
            return
        # Вслух — не больше SPOKEN_MAX_CHARS, как у обычной озвучки (for_voice).
        if self._chars and self._chars + len(sentence) > SPOKEN_MAX_CHARS:
            self._cut = True
            return
        self._chars += len(sentence) + 1
        if not self.opened:
            first, *rest = _first_cut(sentence)
            self._add(first)
            self._tail = " ".join(rest)
            return
        limit = min(MAX_CHARS, GROWTH * len(self._job.chunks[-1]))
        if self._tail and len(self._tail) + len(sentence) + 1 > limit:
            self._seal()
        self._tail = f"{self._tail} {sentence}".strip()
        self._job.changed.set()  # клиент, ждущий следующий кусок, заберёт хвост сразу

    def close(self) -> Spoken:
        self._seal()
        if self._cut:
            self._add(_MORE_ON_SCREEN)
        self._job.open = False
        self._job.changed.set()
        return Spoken(None, self.job_id, len(self._job.chunks))

    def _seal(self) -> None:
        if self._tail:
            self._add(self._tail)
            self._tail = ""

    def _add(self, text: str) -> None:
        self._job.chunks.append(text)
        if len(self._job.chunks) == 1:
            _ensure(self._job, 0)  # первый кусок — в синтез сразу
        self._job.changed.set()


# Сколько клиент ждёт следующую фразу растущей озвучки — как потолок хода.
STREAM_WAIT = 60.0


async def chunk(job_id: str, n: int) -> bytes | None:
    """Кусок n. None — озвучка закончилась раньше (растущая закрыта)."""
    job = _jobs.get(job_id)
    if job is None or n < 0:
        raise KeyError(n)
    deadline = time.monotonic() + STREAM_WAIT
    while n >= len(job.chunks):
        if not job.open:
            return None
        if n == len(job.chunks) and job.seal is not None:
            job.seal()  # клиент готов играть дальше — отдаём, что накопилось
            if n < len(job.chunks):
                break
        # Между проверкой и clear() нет await — set() не потеряется.
        job.changed.clear()
        try:
            await asyncio.wait_for(job.changed.wait(), max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            return None
    _ensure(job, n)
    _ensure(job, n + 1)  # опережение: следующий готовится, пока звучит этот
    return await job.parts[n]
