"""Свой агент потоком: фразы уходят по мере генерации (app/agent/orchestrator.py)."""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import AsyncMock

import pytest

from app import metrics
from app.agent import orchestrator
from app.agent.orchestrator import SentenceSplitter
from app.config import settings


def _split(*deltas: str) -> list[str]:
    out: list[str] = []
    splitter = SentenceSplitter(out.append)
    for d in deltas:
        splitter.feed(d)
    splitter.flush()
    return out


def test_splitter_sentences_as_ready() -> None:
    out: list[str] = []
    splitter = SentenceSplitter(out.append)
    splitter.feed("Небо голубое из-за рассеяния. Корот")
    assert out == ["Небо голубое из-за рассеяния."]
    splitter.feed("кие волны рассеиваются сильнее! Вот")
    assert out[-1] == "Короткие волны рассеиваются сильнее!"
    splitter.flush()
    assert out[-1] == "Вот"


def test_splitter_keeps_numbers_and_quotes() -> None:
    assert _split("Курс 3.5 рубля. Он сказал «да». ", "Всё") == ["Курс 3.5 рубля.", "Он сказал «да».", "Всё"]


class StreamLlm:
    """Шаги: сначала «Сейчас проверю.» + поиск, потом ответ."""

    def __init__(self) -> None:
        self.step = 0

    async def cloud_stream(self, _m: Any, _t: Any, on_text: Callable[[str], None]) -> dict:
        self.step += 1
        if self.step == 1:
            on_text("Сейчас ")
            on_text("проверю.")
            return {"role": "assistant", "content": "Сейчас проверю.",
                    "tool_calls": [{"id": "1", "function": {"name": "web_search", "arguments": "{}"}}]}
        for part in ("В 2022 году ", "победила Аргентина. ", "В финале — Франция."):
            on_text(part)
        return {"role": "assistant", "content": "В 2022 году победила Аргентина. В финале — Франция."}

    async def cloud(self, *_: Any) -> dict:
        raise AssertionError("с on_sentence ходим только потоком")


@pytest.fixture
def stream_llm(monkeypatch: pytest.MonkeyPatch) -> StreamLlm:
    fake = StreamLlm()
    monkeypatch.setattr(orchestrator, "get_llm", lambda: fake)
    monkeypatch.setattr(orchestrator, "call_tool", AsyncMock(return_value="Аргентина"))
    monkeypatch.setattr(settings, "llm_mode", "auto")
    return fake


async def test_sentences_in_order_across_steps(stream_llm: StreamLlm) -> None:
    said: list[str] = []
    await orchestrator.run_agent([], "кто выиграл ЧМ", on_sentence=said.append)
    assert said == ["Сейчас проверю.", "В 2022 году победила Аргентина.", "В финале — Франция."]


async def test_reply_is_final_step_text(stream_llm: StreamLlm) -> None:
    reply, tools = await orchestrator.run_agent([], "кто выиграл ЧМ", on_sentence=lambda _: None)
    assert reply == "В 2022 году победила Аргентина. В финале — Франция."
    assert tools == ["web_search"]


async def test_first_sentence_marks_llm_first(stream_llm: StreamLlm) -> None:
    sw = metrics.begin("t", "web:x")
    await orchestrator.run_agent([], "кто выиграл ЧМ", on_sentence=lambda _: None)
    assert "llm_first" in sw.stages


async def test_no_callback_uses_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    class Llm:
        async def cloud(self, *_: Any) -> dict:
            return {"content": "Без потока."}

        async def cloud_stream(self, *_: Any) -> dict:
            raise AssertionError("без on_sentence поток не нужен")

    monkeypatch.setattr(orchestrator, "get_llm", lambda: Llm())
    monkeypatch.setattr(settings, "llm_mode", "auto")
    assert await orchestrator.run_agent([], "привет") == ("Без потока.", [])
