"""Путь реплики: кто отвечает, без повторов действий, без внезапного фона."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.agent import orchestrator, router
from app.agent.orchestrator import CloudUnavailable
from app.config import settings
from app.services import hermes, speech


@pytest.mark.parametrize("text", [
    "напомни завтра в 9 позвонить маме",
    "что у меня в заметках про машину",
    "пришли это в телеграм",
    "запомни, что я не ем лук",
    "спроси у Гермеса",
    "каждое утро присылай погоду",
    "посмотри мои задачи на сегодня",
])
def test_needs_hermes(text: str) -> None:
    assert router.needs_hermes(text)


@pytest.mark.parametrize("text", [
    "какая погода в Москве",
    "сколько будет 17 умножить на 23",
    "включи последний выпуск Редакции",
    "кто написал Мастера и Маргариту",
])
def test_quick_questions_stay_with_builtin(text: str) -> None:
    assert not router.needs_hermes(text)


@pytest.fixture
def backends(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    monkeypatch.setattr(settings, "agent_backend", "hermes")
    monkeypatch.setattr(settings, "llm_mode", "auto")
    mocks = {"agent": AsyncMock(return_value=("агент", [])), "hermes": AsyncMock(return_value=("гермес", []))}
    monkeypatch.setattr(main, "run_agent", mocks["agent"])
    monkeypatch.setattr(main, "run_hermes", mocks["hermes"])
    return mocks


async def test_quick_question_goes_to_builtin(backends: dict[str, AsyncMock]) -> None:
    assert await main._think("s", "какая погода", lambda _: None, False) == ("агент", [])
    backends["hermes"].assert_not_awaited()


async def test_memory_question_goes_to_hermes(backends: dict[str, AsyncMock]) -> None:
    assert await main._think("s", "напомни в 9 про встречу", lambda _: None, False) == ("гермес", [])
    backends["agent"].assert_not_awaited()


async def test_builtin_without_cloud_hands_over_to_hermes(backends: dict[str, AsyncMock]) -> None:
    backends["agent"].side_effect = CloudUnavailable("402")
    assert await main._think("s", "какая погода", lambda _: None, False) == ("гермес", [])


async def test_slow_turn_is_cut_not_sent_to_background(
    backends: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow(*_: object, **__: object) -> tuple[str, list[str]]:
        await asyncio.sleep(5)
        return "поздно", []

    backends["agent"].side_effect = slow
    monkeypatch.setattr(settings, "foreground_limit_seconds", 0.05)
    monkeypatch.setattr(router, "try_fast", AsyncMock(return_value=None))
    reply, tools, spoken = await main._answer("s", "какая погода", "web:core-test-01", False, False, lambda _: None)
    assert reply == main._TOO_SLOW and spoken is None


def test_hermes_tool_without_text_is_done() -> None:
    data = {"output": [{"type": "function_call", "name": "play_music"}], "status": "completed"}
    assert hermes.parse_output(data) == (hermes.DONE_WITHOUT_TEXT, ["play_music"])


async def test_builtin_does_not_replay_after_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    class Llm:
        async def cloud(self, *_: object) -> dict:
            calls["n"] += 1
            if calls["n"] == 1:
                return {"tool_calls": [{"id": "1", "function": {"name": "music", "arguments": '{"action": "play"}'}}]}
            raise httpx.ConnectError("обрыв")

    monkeypatch.setattr(orchestrator, "get_llm", lambda: Llm())
    monkeypatch.setattr(orchestrator, "call_tool", AsyncMock(return_value="Включаю"))
    monkeypatch.setattr(settings, "llm_mode", "auto")
    reply, tools = await orchestrator.run_agent([], "включи что-нибудь", fallback_local=False)
    assert (reply, tools) == (orchestrator.DONE, ["music"])


def test_long_answer_is_cut_for_voice() -> None:
    text = " ".join(f"Фраза номер {i} про что-то важное." for i in range(40))
    spoken = speech.for_voice(text)
    assert len(spoken) < speech.SPOKEN_MAX_CHARS + 40
    assert spoken.endswith("Остальное — на экране.")
    assert speech.for_voice("Коротко.") == "Коротко."


async def test_last_step_answers_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    class Llm:
        async def cloud(self, _messages: object, tools: object) -> dict:
            seen.append(tools)
            if tools is None:
                return {"content": "Ответ по найденному."}
            return {"tool_calls": [{"id": "1", "function": {"name": "web_fetch", "arguments": '{"url": "x"}'}}]}

    monkeypatch.setattr(orchestrator, "get_llm", lambda: Llm())
    monkeypatch.setattr(orchestrator, "call_tool", AsyncMock(return_value="пусто"))
    monkeypatch.setattr(settings, "llm_mode", "auto")
    reply, _ = await orchestrator.run_agent([], "найди что-нибудь")
    assert reply == "Ответ по найденному."
    assert seen[-1] is None and len(seen) == orchestrator.MAX_TOOL_ITERATIONS


async def test_weather_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.tools import weather

    answers = [
        {"results": [{"name": "Москва", "latitude": 55.75, "longitude": 37.62}]},
        {
            "current": {"temperature_2m": 10.8, "apparent_temperature": 9.6, "weather_code": 3, "wind_speed_10m": 1.5},
            "daily": {
                "time": ["d1", "d2"], "temperature_2m_min": [8.2, 9.0], "temperature_2m_max": [14.6, 16.1],
                "precipitation_probability_max": [20, None], "weather_code": [61, 0],
            },
        },
    ]
    monkeypatch.setattr(weather, "_get", AsyncMock(side_effect=answers))
    monkeypatch.setattr(settings, "youtube_proxy", "")
    text = await weather.run({})
    assert text.splitlines() == [
        "Москва, сейчас: 11°, ощущается как 10°, пасмурно, ветер 2 м/с.",
        "сегодня: от 8° до 15°, небольшой дождь, вероятность осадков 20%.",
        "завтра: от 9° до 16°, ясно.",
    ]
