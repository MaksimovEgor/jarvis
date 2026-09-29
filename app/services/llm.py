"""Клиент LLM-оркестратора: OpenAI-совместимый /chat/completions с tool calling.

Паттерн клиента — как QwenBackend в point/backend (httpx.AsyncClient,
Bearer-заголовок только когда есть ключ), но без response_format=json_object:
агент отвечает то обычным текстом, то только tool_calls, а json_object
такой ответ бы не дал. Провайдер (DeepSeek/OpenAI/свой GPU-бокс) — это
только base_url/api_key/model, протокол один и тот же.

get_llm() — цепочка: облако (LLM_*) → локальная модель на GPU (Ollama,
jarvis-llm.service). Облако не ответило (кончились деньги, 5xx, нет сети) —
запрос уходит локальной. Свой агент при этом зовёт cloud() и переключается
сам (orchestrator.py: у локальной инструмент выбирает код, не модель).
settings.llm_mode == "local" — сразу локальная (переключается голосом/API).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Qwen3 с отключёнными «размышлениями» всё равно иногда оставляет пустые теги.
_THINK = re.compile(r"<think>.*?</think>|</?think>", re.DOTALL)


class LLMClient:
    def __init__(
        self, api_key: str, model: str, base_url: str,
        timeout: float = 60.0, extra: dict[str, Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout
        self._extra = extra or {}

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.4,
            **self._extra,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._endpoint, json=self._payload(messages, tools), headers=self._headers())
            resp.raise_for_status()

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM вернул пустой ответ (choices пуст)")
        message = choices[0]["message"]
        if isinstance(message.get("content"), str):
            message["content"] = _THINK.sub("", message["content"]).strip()
        return message

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
    ) -> dict[str, Any]:
        """То же, что chat(), но потоком (SSE): текст отдаётся в on_text по мере
        генерации — озвучка начинается с первой фразы, а не после всего ответа.
        Возвращает собранное сообщение того же вида, что chat().

        tool_calls приходят кусками: имя и id — в первом, аргументы — строкой
        по частям; склеиваются по delta.index.
        """
        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        payload = {**self._payload(messages, tools), "stream": True}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", self._endpoint, json=payload, headers=self._headers()) as resp:
                if resp.is_error:
                    await resp.aread()  # иначе у ошибки не будет текста (402 «нет денег»)
                    resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError as exc:
                        raise RuntimeError(f"LLM прислала битый кусок потока: {data[:200]}") from exc
                    choices = chunk.get("choices") or []
                    # Ошибку посреди потока (OpenRouter и подобные) провайдер шлёт
                    # куском, а не статусом: без этого обрывок сошёл бы за ответ.
                    if chunk.get("error") or any(c.get("finish_reason") == "error" for c in choices):
                        raise RuntimeError(f"LLM оборвала поток ошибкой: {chunk.get('error') or data[:200]}")
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if text := delta.get("content"):
                        content.append(text)
                        on_text(text)
                    for part in delta.get("tool_calls") or []:
                        call = calls.setdefault(part.get("index", 0), {
                            "id": "", "type": "function", "function": {"name": "", "arguments": ""},
                        })
                        call["id"] = part.get("id") or call["id"]
                        fn = part.get("function") or {}
                        call["function"]["name"] += fn.get("name") or ""
                        call["function"]["arguments"] += fn.get("arguments") or ""
        text = _THINK.sub("", "".join(content)).strip()
        # Как у chat(): при одних инструментах content — null, не пустая строка.
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        elif not text:
            raise RuntimeError("LLM вернул пустой ответ (поток без текста и инструментов)")
        return message


_LOCAL_ATTEMPTS = 3


class ChainLLM:
    """Тот же chat(), что у LLMClient, но по цепочке: облако → локальная."""

    def __init__(self, cloud: LLMClient, local: LLMClient) -> None:
        self._cloud = cloud
        self._local = local

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if settings.llm_mode != "local":
            try:
                return await self._cloud.chat(messages, tools)
            except (httpx.HTTPError, RuntimeError) as exc:
                logger.warning("Облачная LLM не ответила (%s) — отвечает локальная", _reason(exc))
        return await self.local(messages, tools)

    async def cloud(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Только облако, ошибка — наверх: свой агент при отказе сам уходит на
        локальный путь, где инструмент выбирает код (orchestrator.py)."""
        return await self._cloud.chat(messages, tools)

    async def cloud_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
    ) -> dict[str, Any]:
        """Облако потоком; ошибка — наверх, как у cloud()."""
        return await self._cloud.stream(messages, tools, on_text)

    async def local(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Сразу локальная модель. Маленькая модель через раз отдаёт пустоту без
        вызова инструмента — повтор (~0,5 с на GPU)."""
        for _ in range(_LOCAL_ATTEMPTS - 1):
            message = await self._local.chat(messages, tools)
            if message.get("content") or message.get("tool_calls"):
                return message
        return await self._local.chat(messages, tools)


def _reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.response.status_code} {exc.response.text[:120]}"
    return f"{type(exc).__name__}: {exc}"


_client: ChainLLM | None = None


def get_llm() -> ChainLLM:
    global _client
    if _client is None:
        _client = ChainLLM(
            LLMClient(api_key=settings.llm_api_key, model=settings.llm_model, base_url=settings.llm_base_url),
            # Модель грузится в GPU по первому запросу (~10 с), отсюда таймаут.
            # max_tokens — маленькая модель без потолка пишет абзацы там, где
            # голосу нужна пара фраз (~19 токенов/с на GTX 1050).
            LLMClient(
                api_key="", model=settings.local_llm_model, base_url=settings.local_llm_url,
                timeout=120.0, extra={"max_tokens": 300},
            ),
        )
    return _client
