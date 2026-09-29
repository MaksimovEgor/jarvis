"""Растущая озвучка и короткий первый кусок (app/services/speech.py)."""

from __future__ import annotations

import asyncio

import pytest

from app.services import speech


@pytest.fixture(autouse=True)
def fake_synth(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Синтез — мгновенно, wav = сам текст; что синтезировали — в списке."""
    done: list[str] = []

    async def synth(text: str) -> bytes:
        done.append(text)
        return text.encode()

    monkeypatch.setattr(speech, "_synth", synth)
    return done


LONG_FIRST = (
    "Солнечный свет, проходя через атмосферу, рассеивается на молекулах воздуха, "
    "и короткие синие волны рассеиваются сильнее."
)


def test_first_cut_by_comma() -> None:
    first, rest = speech._first_cut(LONG_FIRST)
    assert first == "Солнечный свет, проходя через атмосферу,"
    assert rest.startswith("рассеивается")


@pytest.mark.parametrize("sentence", [
    "Коротко и ясно.",
    "Очень длинное предложение без единой запятой которое просто идёт и идёт до самого конца.",
    "Итак, длинное предложение без запятых дальше которое идёт и идёт и идёт до конца.",
])
def test_first_cut_leaves_when_nowhere_to_cut(sentence: str) -> None:
    assert speech._first_cut(sentence) == [sentence]


def test_split_uses_first_cut() -> None:
    chunks = speech._split(f"{LONG_FIRST} Поэтому небо голубое.")
    assert chunks[0] == "Солнечный свет, проходя через атмосферу,"
    assert " ".join(chunks) == f"{LONG_FIRST} Поэтому небо голубое."


async def test_first_sentence_is_chunk_zero_immediately(fake_synth: list[str]) -> None:
    stream = speech.SpeechStream()
    stream.feed("Сейчас проверю.")
    await asyncio.sleep(0)
    assert fake_synth == ["Сейчас проверю."]


async def test_chunk_waits_for_feed() -> None:
    stream = speech.SpeechStream()
    stream.feed("Первая.")
    waiting = asyncio.create_task(speech.chunk(stream.job_id, 1))
    await asyncio.sleep(0.01)
    assert not waiting.done()
    stream.feed("Вторая фраза.")
    assert await asyncio.wait_for(waiting, 1) == "Вторая фраза.".encode()


async def test_tail_accumulates_until_requested() -> None:
    stream = speech.SpeechStream()
    for s in ("Первая фраза тут.", "Два.", "Три."):  # второй кусок — до 2 × 17 символов
        stream.feed(s)
    assert (await speech.chunk(stream.job_id, 0)) == "Первая фраза тут.".encode()
    # Пока клиент играл кусок 0, пришли «Два.» и «Три.» — одним куском.
    assert (await speech.chunk(stream.job_id, 1)) == "Два. Три.".encode()


async def test_chunks_grow_limit() -> None:
    stream = speech.SpeechStream()
    stream.feed("Первая короткая.")  # 16 символов → второй кусок до 32
    stream.feed("Вторая фраза подлиннее.")
    stream.feed("Третья фраза тоже есть.")
    spoken = stream.close()
    assert spoken.count == 3


async def test_chunk_after_close_returns_none() -> None:
    stream = speech.SpeechStream()
    stream.feed("Одна фраза.")
    spoken = stream.close()
    assert spoken.count == 1
    assert await speech.chunk(stream.job_id, 1) is None


async def test_close_wakes_waiting_client() -> None:
    stream = speech.SpeechStream()
    stream.feed("Одна фраза.")
    await speech.chunk(stream.job_id, 0)
    waiting = asyncio.create_task(speech.chunk(stream.job_id, 1))
    await asyncio.sleep(0.01)
    stream.close()
    assert await asyncio.wait_for(waiting, 1) is None


async def test_voice_cap() -> None:
    stream = speech.SpeechStream()
    for i in range(40):
        stream.feed(f"Фраза номер {i} про что-то важное.")
    spoken = stream.close()
    job = speech._jobs[stream.job_id]
    assert sum(len(c) for c in job.chunks) < speech.SPOKEN_MAX_CHARS + 60
    assert job.chunks[-1] == "Остальное — на экране."
    assert spoken.count == len(job.chunks)


async def test_plain_speak_unchanged() -> None:
    spoken = await speech.speak("Первая фраза. Вторая фраза.", inline=False)
    assert spoken.job_id and spoken.count >= 1 and spoken.audio_base64 is None
    assert await speech.chunk(spoken.job_id, spoken.count) is None
