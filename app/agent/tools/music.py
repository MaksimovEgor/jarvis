"""Управление музыкой через mpv (idle-процесс + unix-сокет IPC).

Три источника: YouTube (через yt-dlp, без аккаунта), локальная библиотека
на сервере, интернет-радио (жёстко заданные потоки — этого достаточно для
пилота, расширить список тривиально).
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

from app.config import settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "music",
        "description": (
            "Управление музыкой: play (source=youtube|local|radio, query — "
            "поисковый запрос или название станции), pause, resume, stop, "
            "volume (level 0-100)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "resume", "stop", "volume"],
                },
                "source": {"type": "string", "enum": ["youtube", "local", "radio"]},
                "query": {"type": "string", "description": "Что искать/включить"},
                "level": {
                    "type": "integer",
                    "description": "Громкость 0-100, только для action=volume",
                },
            },
            "required": ["action"],
        },
    },
}

# Пусто по умолчанию: рабочие адреса потоков нужно проверить и вписать
# самому (`curl -I <url>` должен давать 200) — угадывать их нельзя, битая
# ссылка выглядит как "станция играет", хотя ничего не звучит.
RADIO_STATIONS: dict[str, str] = {
    # "название": "https://.../stream.mp3",
}


class MPVController:
    """IPC-клиент к `mpv --idle`: процесс поднимается лениво при первой команде."""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path

    async def _ensure_running(self) -> None:
        if Path(self._socket_path).exists():
            return
        await asyncio.create_subprocess_exec(
            "mpv", "--idle", "--no-video",
            "--ao=alsa", "--audio-device=alsa/plughw:0,0",  # PipeWire выключен, см. listener.py
            f"--input-ipc-server={self._socket_path}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(50):
            if Path(self._socket_path).exists():
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("mpv не поднял IPC-сокет")

    async def command(self, *args: object) -> dict:
        await self._ensure_running()
        payload = json.dumps({"command": list(args)}) + "\n"

        def _send() -> str:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(self._socket_path)
                sock.sendall(payload.encode())
                sock.settimeout(5)
                return sock.recv(65536).decode()

        raw = await asyncio.to_thread(_send)
        return json.loads(raw.splitlines()[0]) if raw else {}


_mpv = MPVController(settings.mpv_socket)


async def _search_youtube(query: str) -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", f"ytsearch1:{query}", "--print", "%(webpage_url)s", "--no-playlist",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    urls = out.decode().strip().splitlines()
    return urls[0] if urls else None


def _search_local(query: str) -> Path | None:
    library = Path(settings.music_library_dir)
    if not library.exists():
        return None
    query_lower = query.lower()
    for path in library.rglob("*"):
        if path.suffix.lower() in (".mp3", ".flac", ".ogg", ".m4a", ".wav") and query_lower in path.name.lower():
            return path
    return None


async def run(arguments: dict) -> str:
    action = arguments["action"]

    if action == "pause":
        await _mpv.command("set_property", "pause", True)
        return "Пауза."
    if action == "resume":
        await _mpv.command("set_property", "pause", False)
        return "Продолжаю."
    if action == "stop":
        await _mpv.command("stop")
        return "Остановил."
    if action == "volume":
        level = int(arguments.get("level", 50))
        await _mpv.command("set_property", "volume", level)
        return f"Громкость {level}."
    if action != "play":
        return f"Неизвестное действие: {action}"

    source = arguments.get("source", "youtube")
    query = arguments.get("query", "")

    if source == "radio":
        station = next((url for name, url in RADIO_STATIONS.items() if name in query.lower()), None)
        if not station:
            return f"Не знаю такую станцию. Есть: {', '.join(RADIO_STATIONS)}."
        await _mpv.command("loadfile", station, "replace")
        return f"Включаю радио: {query}."

    if source == "local":
        path = _search_local(query)
        if not path:
            return f"В локальной библиотеке ничего не найдено по «{query}»."
        await _mpv.command("loadfile", str(path), "replace")
        return f"Включаю {path.stem}."

    url = await _search_youtube(query)
    if not url:
        return f"На YouTube ничего не нашлось по «{query}»."
    await _mpv.command("loadfile", url, "replace")
    return f"Включаю с YouTube: {query}."
