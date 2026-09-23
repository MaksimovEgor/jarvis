"""Клиент Hermes Agent (основной профиль на этом же сервере) через его API server.

Jarvis здесь — только «уши и рот»: память о хозяине, скиллы, Telegram, cron и
прочее живут в Hermes, голос получает того же агента, что и чат в Telegram.

Используется /v1/responses с именованным `conversation`: историю (вместе с
вызовами инструментов) хранит сам Hermes, Jarvis шлёт только новую реплику.
`instructions` — голосовой стиль ответа; SOUL основного профиля не трогаем,
чтобы не испортить ответы в Telegram.

Разговор начинается заново после паузы (как у Алисы): одна бессрочная
conversation разрослась до 2500 сообщений и ~370k токенов на каждую реплику —
медленно, дорого, и модель отвечала по старой истории («уже играет»)
вместо вызова инструментов. Долгая память о хозяине живёт в памяти Hermes,
а не в истории разговора, поэтому при ротации не теряется.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.agent.prompts import VOICE_INSTRUCTIONS
from app.config import settings


# session_id → (conversation, время последней реплики). Живёт в процессе:
# после рестарта ядра разговор тоже начинается заново — это нормально.
_conversations: dict[str, tuple[str, float]] = {}
# conversation → call_id уже показанных вызовов: Hermes в output отдаёт
# вызовы инструментов за весь разговор, а не только за текущий ход.
_seen_calls: dict[str, set[str]] = {}


def _conversation_id(session_id: str) -> str:
    now = time.time()
    conversation, last = _conversations.get(session_id, ("", 0.0))
    if not conversation or now - last > settings.hermes_conversation_idle_minutes * 60:
        conversation = f"jarvis-voice-{session_id}-{int(now)}"
    _conversations[session_id] = (conversation, now)
    return conversation


async def run_hermes(session_id: str, user_text: str) -> tuple[str, list[str]]:
    conversation = _conversation_id(session_id)
    payload = {
        "model": "hermes-agent",
        "input": user_text,
        "instructions": VOICE_INSTRUCTIONS,
        "conversation": conversation,
        "store": True,
    }
    headers = {"Authorization": f"Bearer {settings.hermes_api_key}"}

    async with httpx.AsyncClient(timeout=settings.hermes_timeout) as client:
        resp = await client.post(f"{settings.hermes_url.rstrip('/')}/v1/responses", json=payload, headers=headers)
        resp.raise_for_status()

    reply, calls = _parse_output(resp.json())
    seen = _seen_calls.setdefault(conversation, set())
    tools = [name for call_id, name in calls if call_id not in seen]
    seen.update(call_id for call_id, _ in calls)
    return reply, tools


def _tool_name(item: dict[str, Any]) -> str:
    """Отложенные инструменты (MCP и пр. за tool_search) Hermes зовёт через
    обёртку `tool_call`, настоящее имя — в её аргументах."""
    name = item.get("name", "?")
    if name == "tool_call":
        try:
            return json.loads(item.get("arguments") or "{}").get("name", name)
        except json.JSONDecodeError:
            return name
    return name


def _parse_output(data: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    # Вызовы инструментов в output уже выполнены на стороне Hermes — берём
    # только (call_id, имя) для лога/ответа API.
    texts: list[str] = []
    calls: list[tuple[str, str]] = []
    for item in data.get("output") or []:
        if item.get("type") == "function_call":
            calls.append((item.get("call_id") or item.get("id", ""), _tool_name(item)))
        elif item.get("type") == "message":
            texts.extend(part.get("text", "") for part in item.get("content") or [] if part.get("type") == "output_text")

    reply = "\n".join(t for t in texts if t).strip()
    if not reply:
        raise RuntimeError(f"Hermes вернул ответ без текста (status={data.get('status')})")
    return reply, calls
