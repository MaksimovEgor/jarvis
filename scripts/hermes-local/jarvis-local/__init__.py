"""/local <текст> в любом чате Hermes (Telegram, CLI) — ответ локальной модели
Джарвиса. LLM Hermes не вызывается: текст уходит в ядро Джарвиса
(POST http://127.0.0.1:8000/local/chat), оно спрашивает Ollama на GPU asus.

«/local новый» — забыть историю локального чата.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

_URL = "http://127.0.0.1:8000/local/chat"
# Первая реплика ждёт загрузку модели в GPU (~10 с) и до трёх попыток.
_TIMEOUT = 120


def _post(text: str) -> str:
    body = json.dumps({"session_id": "telegram", "text": text}).encode()
    request = urllib.request.Request(_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"Джарвис недоступен: {exc}"


async def _handle(raw_args: str) -> str:
    return await asyncio.to_thread(_post, raw_args)


def register(ctx) -> None:
    ctx.register_command("local", handler=_handle,
                         description="Спросить локальную модель Джарвиса (GPU asus), без облака.")
