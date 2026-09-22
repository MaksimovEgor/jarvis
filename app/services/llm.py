"""Клиент LLM-оркестратора: OpenAI-совместимый /chat/completions с tool calling.

Паттерн клиента — как QwenBackend в point/backend (httpx.AsyncClient,
Bearer-заголовок только когда есть ключ), но без response_format=json_object:
агент отвечает то обычным текстом, то только tool_calls, а json_object
такой ответ бы не дал. Провайдер (DeepSeek/OpenAI/свой GPU-бокс) — это
только base_url/api_key/model, протокол один и тот же.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._timeout = 60.0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.4,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._endpoint, json=payload, headers=headers)
            resp.raise_for_status()

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM вернул пустой ответ (choices пуст)")
        return choices[0]["message"]


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
        )
    return _client
