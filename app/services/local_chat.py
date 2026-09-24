"""Прямой чат с локальной моделью (GPU asus) — мимо Hermes и облака, но с теми
же возможностями: музыка — быстрым путём router, поиск — orchestrator._run_local.

    Telegram: /local <текст> ─► плагин Hermes (scripts/hermes-local/) ─┐
                                                                    ├─► POST /local/chat ─► run_agent(local=True)
    любой локальный клиент на asus ────────────────────────────────┘

История своя и короткая: контекст у модели 8k токенов, а длинная переписка
ей только мешает. «/local новый» — начать заново.
"""

from __future__ import annotations

import time

from app.agent import router
from app.agent.orchestrator import run_agent
from app.music import devices

# Дольше этого молчали — прошлый разговор уже не про то, начинаем с чистого.
IDLE_RESET_S = 30 * 60

_sessions: dict[str, tuple[list[dict], float]] = {}


def reset(session_id: str) -> None:
    _sessions.pop(session_id, None)


async def ask(session_id: str, text: str) -> str:
    # «включи / пауза / громче» — быстрым путём, как голосом (играет на последнем
    # устройстве, где был Джарвис).
    device = devices.current_device()
    if device is None and router.is_player_command(text):
        return "Не знаю, где включить: открой Джарвиса на телефоне или в браузере."
    fast = await router.try_fast(text, device or devices.ASUS)
    if fast is not None:
        return fast.text
    history, last = _sessions.get(session_id, ([], 0.0))
    if time.monotonic() - last > IDLE_RESET_S:
        history = []
    reply, _tools = await run_agent(history, text, local=True)
    _sessions[session_id] = (history, time.monotonic())
    return reply or "Локальная модель промолчала — переформулируй."
