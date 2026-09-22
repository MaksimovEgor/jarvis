"""Клиент Hermes Agent (основной профиль на этом же сервере) через его API server.

Jarvis здесь — только «уши и рот»: память о хозяине, скиллы, Telegram, cron и
прочее живут в Hermes, голос получает того же агента, что и чат в Telegram.

Используется /v1/responses с именованным `conversation`: историю (вместе с
вызовами инструментов) хранит сам Hermes, Jarvis шлёт только новую реплику.
`instructions` — голосовой стиль ответа; SOUL основного профиля не трогаем,
чтобы не испортить ответы в Telegram.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.agent.prompts import VOICE_INSTRUCTIONS
from app.config import settings


async def run_hermes(session_id: str, user_text: str) -> tuple[str, list[str]]:
    payload = {
        "model": "hermes-agent",
        "input": user_text,
        "instructions": VOICE_INSTRUCTIONS,
        "conversation": f"jarvis-voice-{session_id}",
        "store": True,
    }
    headers = {"Authorization": f"Bearer {settings.hermes_api_key}"}

    async with httpx.AsyncClient(timeout=settings.hermes_timeout) as client:
        resp = await client.post(f"{settings.hermes_url.rstrip('/')}/v1/responses", json=payload, headers=headers)
        resp.raise_for_status()

    return _parse_output(resp.json())


def _parse_output(data: dict[str, Any]) -> tuple[str, list[str]]:
    # Вызовы инструментов в output уже выполнены на стороне Hermes — берём
    # только их имена для лога/ответа API.
    texts: list[str] = []
    tools: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") == "function_call":
            tools.append(item.get("name", "?"))
        elif item.get("type") == "message":
            texts.extend(part.get("text", "") for part in item.get("content") or [] if part.get("type") == "output_text")

    reply = "\n".join(t for t in texts if t).strip()
    if not reply:
        raise RuntimeError(f"Hermes вернул ответ без текста (status={data.get('status')})")
    return reply, tools
