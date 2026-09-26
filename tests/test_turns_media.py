import asyncio

import pytest

from app.config import settings
from app.turns import Turns

DEVICE = "web:turns-test-01"


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # Диспетчер конфликтов — только правила, без сетевого вызова LLM.
    monkeypatch.setattr(settings, "llm_api_key", "")


async def _slow(_progress: object) -> tuple[str, list[str], None]:
    await asyncio.sleep(10)
    return "", [], None


async def test_new_play_request_cancels_old_ones_even_in_background() -> None:
    turns = Turns()
    old = turns.start("1", "s", DEVICE, "включи Queen", _slow)
    turns.demote(old)
    tale = turns.start("2", "s", DEVICE, "сказку про автобус найди", _slow)
    question = turns.start("3", "s", DEVICE, "какая погода", _slow)
    latest = turns.start("4", "s", DEVICE, "поставь Кино", _slow)
    await asyncio.sleep(0)
    assert old.task.cancelled() and tale.task.cancelled()
    assert not question.task.done() and not latest.task.done()
    for t in (question, latest):
        t.task.cancel()


async def test_ui_tap_cancels_voice_play_requests() -> None:
    turns = Turns()
    voice = turns.start("1", "s", DEVICE, "включи Queen", _slow)
    other = turns.start("2", "s", "web:turns-test-02", "включи Кино", _slow)
    assert turns.cancel_media(DEVICE) == {"1"}
    await asyncio.sleep(0)
    assert voice.task.cancelled() and not other.task.done()
    other.task.cancel()
