"""Потоковый ответ облачной LLM (SSE) — app/services/llm.py."""

from __future__ import annotations

import json
from functools import partial
from typing import Any

import httpx
import pytest

from app.services import llm


def _sse(*deltas: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps({'choices': [{'delta': d}]}, ensure_ascii=False)}" for d in deltas]
    return ("\n\n".join([*lines, "data: [DONE]"]) + "\n\n").encode()


@pytest.fixture
def respond(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Ответ «сервера» LLM: status + тело; последний запрос — в sent."""
    state: dict[str, Any] = {"status": 200, "body": b"", "sent": None}

    def handler(request: httpx.Request) -> httpx.Response:
        state["sent"] = json.loads(request.content)
        return httpx.Response(state["status"], content=state["body"])

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", partial(real, transport=httpx.MockTransport(handler)))
    return state


def _client() -> llm.LLMClient:
    return llm.LLMClient(api_key="k", model="m", base_url="http://llm.test/v1")


async def test_stream_text_deltas(respond: dict[str, Any]) -> None:
    respond["body"] = _sse({"role": "assistant"}, {"content": "Небо "}, {"content": "голубое."})
    seen: list[str] = []
    message = await _client().stream([{"role": "user", "content": "?"}], None, seen.append)
    assert seen == ["Небо ", "голубое."]
    assert message == {"role": "assistant", "content": "Небо голубое."}
    assert respond["sent"]["stream"] is True


async def test_stream_tool_calls_assembled(respond: dict[str, Any]) -> None:
    respond["body"] = _sse(
        {"content": "Сейчас проверю."},
        {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": '{"que'}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": 'ry": "ЧМ"}'}}]},
        {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "weather", "arguments": "{}"}}]},
    )
    message = await _client().stream([], [{"type": "function"}], lambda _: None)
    assert message["content"] == "Сейчас проверю."
    calls = message["tool_calls"]
    assert [c["id"] for c in calls] == ["c1", "c2"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"query": "ЧМ"}
    assert calls[1]["function"]["name"] == "weather"


async def test_stream_only_tools_has_null_content(respond: dict[str, Any]) -> None:
    respond["body"] = _sse({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "weather", "arguments": "{}"}}]})
    message = await _client().stream([], [{"type": "function"}], lambda _: None)
    assert message["content"] is None


async def test_stream_http_error_raises(respond: dict[str, Any]) -> None:
    respond["status"], respond["body"] = 402, b'{"error": "Insufficient Balance"}'
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await _client().stream([], None, lambda _: None)
    assert "Insufficient" in exc.value.response.text


async def test_stream_empty_raises(respond: dict[str, Any]) -> None:
    respond["body"] = _sse({"role": "assistant"})
    with pytest.raises(RuntimeError):
        await _client().stream([], None, lambda _: None)


async def test_stream_error_chunk_raises(respond: dict[str, Any]) -> None:
    # Ошибка провайдера посреди потока — не обрывок-«ответ», а RuntimeError для отката.
    error = json.dumps({"error": {"message": "upstream overloaded"}, "choices": [{"delta": {}, "finish_reason": "error"}]})
    respond["body"] = _sse({"content": "Небо "}).replace(b"data: [DONE]", f"data: {error}\n\ndata: [DONE]".encode())
    with pytest.raises(RuntimeError, match="overloaded"):
        await _client().stream([], None, lambda _: None)


async def test_stream_broken_chunk_raises(respond: dict[str, Any]) -> None:
    respond["body"] = b"data: {oops\n\n"
    with pytest.raises(RuntimeError):
        await _client().stream([], None, lambda _: None)
