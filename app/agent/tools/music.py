"""Музыка для встроенного агента — тонкая обёртка над общим плеером
(app/music/player.py). Hermes получает то же самое через MCP (app/mcp_server.py).
"""

from __future__ import annotations

from app.music import devices

SCHEMA = {
    "type": "function",
    "function": {
        "name": "music",
        "description": (
            "Музыка дома. play — включить (source=youtube: артист/песня/жанр, "
            "дальше сама играет очередь похожих; source=radio: query — название "
            "станции, genre — жанр по-английски; source=local — домашняя библиотека). "
            "pause, resume, stop, next, previous, volume (level 0-100 или delta ±), "
            "now_playing — что играет."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "resume", "stop", "next", "previous", "volume", "now_playing"],
                },
                "source": {"type": "string", "enum": ["youtube", "local", "radio"]},
                "query": {"type": "string", "description": "Что искать/включить"},
                "genre": {"type": "string", "description": "Жанр радио по-английски: jazz, rock…"},
                "level": {"type": "integer", "description": "Громкость 0-100"},
                "delta": {"type": "integer", "description": "Изменение громкости, например -15"},
            },
            "required": ["action"],
        },
    },
}


async def run(arguments: dict) -> str:
    action = arguments["action"]
    player = devices.current_player()
    if player is None:
        return "Не знаю, где включить: открой Джарвиса на телефоне или в браузере."
    if action == "play":
        source = arguments.get("source", "youtube")
        query = arguments.get("query", "")
        if source == "radio":
            return await player.play_radio(query or None, arguments.get("genre"), None)
        if source == "local":
            return await player.play_local(query)
        return await player.play_youtube(query)
    if action == "volume":
        return await player.volume(level=arguments.get("level"), delta=arguments.get("delta"))
    handlers = {
        "pause": player.pause,
        "resume": player.resume,
        "stop": player.stop,
        "next": player.next,
        "previous": player.previous,
        "now_playing": player.now_playing,
    }
    handler = handlers.get(action)
    return await handler() if handler else f"Неизвестное действие: {action}"
