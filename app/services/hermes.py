"""Клиент Hermes Agent (основной профиль на этом же сервере) через его API server.

Jarvis здесь — только «уши и рот»: память о хозяине, скиллы, Telegram, cron и
прочее живут в Hermes, голос получает того же агента, что и чат в Telegram.

Используется /v1/responses с именованным `conversation`: историю (вместе с
вызовами инструментов) хранит сам Hermes, Jarvis шлёт только новую реплику.
`instructions` — голосовой стиль ответа; SOUL основного профиля не трогаем,
чтобы не испортить ответы в Telegram.

Запрос идёт потоком (stream=true): тогда отмена задачи в ядре (новая реплика
её заменила, app/turns.py) закрывает соединение, и Hermes прерывает агента.
Без потока брошенный запрос Hermes всё равно доделывал — и, например,
включал музыку через минуты после того, как его отменили.

Hermes выполняет ходы одного разговора строго по очереди: долгая просьба
(7 минут на «пришли VIN в Telegram») задерживала все следующие. Поэтому,
если в разговоре уже идёт ход, новая реплика уходит в параллельный разговор
— без свежего контекста, зато сразу. Долгая память Hermes там та же.

Hermes отвечает 503 (gateway_draining), пока перезагружается после
`kill -USR1`, а при рестарте не принимает соединения. Такие ошибки случаются
до начала потока — агент ещё ничего не сделал, и запрос безопасно повторить.
Оборвавшийся поток не повторяем: инструменты могли уже сработать.

Разговор начинается заново после паузы (как у Алисы): одна бессрочная
conversation разрослась до 2500 сообщений и ~370k токенов на каждую реплику —
медленно, дорого, и модель отвечала по старой истории («уже играет»)
вместо вызова инструментов. Долгая память о хозяине живёт в памяти Hermes,
а не в истории разговора, поэтому при ротации не теряется.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

import httpx

from app.agent.prompts import VOICE_INSTRUCTIONS
from app.config import settings

logger = logging.getLogger("jarvis.hermes")

# session_id → (conversation, время последней реплики). Живёт в процессе:
# после рестарта ядра разговор тоже начинается заново — это нормально.
_conversations: dict[str, tuple[str, float]] = {}
# conversation → сколько ходов в нём сейчас выполняется.
_running: dict[str, int] = {}

# Паузы перед повторами запроса, который Hermes не начал выполнять.
_RETRY_DELAYS = (2.0, 4.0, 8.0)
_RETRY_STATUSES = {502, 503, 504}

# Финальные события потока /v1/responses.
_FINAL_EVENTS = ("response.completed", "response.failed", "response.incomplete")


def _conversation_id(session_id: str) -> str:
    now = time.time()
    conversation, last = _conversations.get(session_id, ("", 0.0))
    if not conversation or now - last > settings.hermes_conversation_idle_minutes * 60:
        conversation = f"jarvis-voice-{session_id}-{int(now)}"
    _conversations[session_id] = (conversation, now)
    return conversation


async def run_hermes(
    session_id: str, user_text: str, on_tool: Callable[[str], None] | None = None,
) -> tuple[str, list[str]]:
    main = _conversation_id(session_id)
    conversation = main if not _running.get(main) else f"{main}-p{uuid.uuid4().hex[:6]}"
    _running[main] = _running.get(main, 0) + 1
    try:
        return await _with_retries(conversation, user_text, on_tool)
    finally:
        _running[main] -= 1


async def _with_retries(conversation: str, user_text: str, on_tool: Callable[[str], None] | None) -> tuple[str, list[str]]:
    for delay in (*_RETRY_DELAYS, None):
        try:
            return await _stream(conversation, user_text, on_tool)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPStatusError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if delay is None or (status is not None and status not in _RETRY_STATUSES):
                raise
            logger.warning("Hermes недоступен (%s) — повтор через %.0f с", status or type(exc).__name__, delay)
            await asyncio.sleep(delay)
    raise AssertionError("недостижимо")


async def _stream(conversation: str, user_text: str, on_tool: Callable[[str], None] | None) -> tuple[str, list[str]]:
    payload = {
        "model": "hermes-agent",
        "input": user_text,
        "instructions": VOICE_INSTRUCTIONS,
        "conversation": conversation,
        "store": True,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {settings.hermes_api_key}"}
    url = f"{settings.hermes_url.rstrip('/')}/v1/responses"

    # Без лимита на чтение: долгие задачи законны, останавливает их отмена хода.
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            event = ""
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    event = line[len("event: "):]
                elif not line.startswith("data: "):
                    continue
                elif event in _FINAL_EVENTS:
                    return _parse_output(json.loads(line[len("data: "):])["response"])
                elif event == "response.output_item.done" and on_tool is not None:
                    item = json.loads(line[len("data: "):]).get("item") or {}
                    if item.get("type") == "function_call":
                        on_tool(_tool_name(item))
    raise RuntimeError("Hermes закрыл поток без ответа")


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


def _parse_output(data: dict[str, Any]) -> tuple[str, list[str]]:
    # Вызовы инструментов в output уже выполнены на стороне Hermes — берём
    # только имена для лога/ответа API.
    texts: list[str] = []
    tools: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") == "function_call":
            tools.append(_tool_name(item))
        elif item.get("type") == "message":
            texts.extend(part.get("text", "") for part in item.get("content") or [] if part.get("type") == "output_text")

    reply = "\n".join(t for t in texts if t).strip()
    if not reply:
        raise RuntimeError(f"Hermes вернул ответ без текста (status={data.get('status')})")
    return reply, tools
