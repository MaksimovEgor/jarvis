from unittest.mock import AsyncMock

import pytest

from app.agent import router
from app.music.player import Player

DEVICE = "web:router-test-01"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    mocks = {
        name: AsyncMock(return_value="ок")
        for name in ("play_wave", "play_liked", "play_query", "rate")
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
    ("включи музыку для бега", "energetic"),
    ("поставь что-нибудь грустное на русском", "calm"),
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


async def test_wave_spec_passed_to_player(calls: dict[str, AsyncMock]) -> None:
    await router.try_fast("поставь что-нибудь грустное на русском", DEVICE)
    spec = calls["play_wave"].await_args.kwargs["spec"]
    assert (spec.mood_energy, spec.language) == ("sad", "russian")


@pytest.mark.parametrize("text", ["покажи текст", "покажи слова песни", "что он поет", "какие там слова"])
async def test_show_lyrics_phrases(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    from app.music import devices
    from tests.conftest import yt

    shown = []
    player = devices.player_for(DEVICE)
    monkeypatch.setattr(Player, "current", property(lambda self: yt("a")))
    monkeypatch.setattr(type(player.output), "show", lambda self, what: shown.append(what))
    reply = await router.try_fast(text, DEVICE)
    assert reply is not None and reply.tool == "show_lyrics" and shown == ["lyrics"]
