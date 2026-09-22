from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.agent.tools import music, web_fetch, web_search

ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]

TOOLS: list[dict[str, Any]] = [
    web_search.SCHEMA,
    web_fetch.SCHEMA,
    music.SCHEMA,
]

_HANDLERS: dict[str, ToolHandler] = {
    web_search.SCHEMA["function"]["name"]: web_search.run,
    web_fetch.SCHEMA["function"]["name"]: web_fetch.run,
    music.SCHEMA["function"]["name"]: music.run,
}


async def call_tool(name: str, arguments: dict[str, Any]) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Неизвестный инструмент: {name}"
    try:
        return await handler(arguments)
    except Exception as exc:  # инструмент не должен ронять весь диалог
        return f"Ошибка инструмента {name}: {exc}"
