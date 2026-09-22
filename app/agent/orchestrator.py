"""Агентный цикл: LLM решает, отвечать текстом или звать инструмент.

history — список сообщений сессии (без system-промпта, он подставляется
каждый раз свежим). Инструментальные сообщения внутри одного вызова живут
только в локальном messages и в history не попадают — иначе история быстро
раздувалась бы результатами web_fetch.
"""

from __future__ import annotations

import json

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOLS, call_tool
from app.config import settings
from app.services.llm import get_llm

MAX_TOOL_ITERATIONS = 4


async def run_agent(history: list[dict], user_text: str) -> tuple[str, list[str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": user_text}]
    llm = get_llm()
    used_tools: list[str] = []

    for _ in range(MAX_TOOL_ITERATIONS):
        message = await llm.chat(messages, tools=TOOLS)
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            reply = message.get("content") or ""
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": reply})
            del history[: max(0, len(history) - settings.session_history_limit)]
            return reply, used_tools

        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            used_tools.append(name)
            result = await call_tool(name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })

    return "Не получилось разобраться за разумное число шагов, переформулируй запрос.", used_tools
