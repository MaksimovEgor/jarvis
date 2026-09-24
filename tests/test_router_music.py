from unittest.mock import AsyncMock

import pytest

from app.agent import router
from app.music.player import Player

DEVICE = "web:router-test-01"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    mocks = {
        name: AsyncMock(return_value="ок")
        for name in ("play_wave", "play_liked", "play_youtube", "rate")
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(Player, name, mock)
    return mocks


@pytest.mark.parametrize("text, mood", [
    ("Джарвис, включи музыку", "auto"),
    ("включи мою волну", "auto"),
    ("поставь что-нибудь", "auto"),
    ("поставь что-нибудь бодрое", "energetic"),
    ("включи спокойную музыку", "calm"),
    ("включи музыку для работы", "focus"),
    ("включи что-нибудь для сна", "sleep"),
    ("включи что-нибудь новенькое", "discover"),
])
async def test_wave_phrases(calls: dict[str, AsyncMock], text: str, mood: str) -> None:
    reply = await router.try_fast(text, DEVICE)
    assert reply is not None and reply.tool == "play_wave"
    assert calls["play_wave"].await_args.args[-1] == mood


@pytest.mark.parametrize("text", ["лайк", "Поставь лайк", "мне нравится", "лайкни эту", "сохрани эту песню"])
async def test_like_phrases(calls: dict[str, AsyncMock], text: str) -> None:
    reply = await router.try_fast(text, DEVICE)
    assert reply is not None and calls["rate"].await_args.args[-1] == 1


@pytest.mark.parametrize("text", ["дизлайк", "не нравится", "не включай больше это", "больше не ставь эту песню"])
async def test_dislike_phrases(calls: dict[str, AsyncMock], text: str) -> None:
    reply = await router.try_fast(text, DEVICE)
    assert reply is not None and calls["rate"].await_args.args[-1] == -1


@pytest.mark.parametrize("text", ["включи мою музыку", "включи лайкнутое", "включи мои лайки", "включи любимое"])
async def test_play_liked_phrase(calls: dict[str, AsyncMock], text: str) -> None:
    reply = await router.try_fast(text, DEVICE)
    assert reply is not None and reply.tool == "play_liked"


@pytest.mark.parametrize("text", ["включи Queen", "включи рок музыку", "включи что-нибудь из Кино"])
async def test_search_still_youtube(calls: dict[str, AsyncMock], text: str) -> None:
    reply = await router.try_fast(text, DEVICE)
    assert reply is not None and reply.tool == "play_music"
    calls["play_wave"].assert_not_awaited()
