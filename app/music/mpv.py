"""Асинхронный IPC-клиент к `mpv --idle`.

Одно постоянное соединение с сокетом: по нему идут и команды (ответы
сопоставляются по request_id), и события mpv — нам нужен `end-file`, чтобы
по окончании трека переключать очередь. mpv поднимается лениво при первой
команде и перезапускается, если умер (например, после рестарта jarvis-core
systemd убивает его вместе с cgroup, а файл сокета остаётся).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import settings

logger = logging.getLogger("jarvis.mpv")

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MPVError(RuntimeError):
    pass


class MPV:
    def __init__(self, socket_path: str, on_event: EventHandler | None = None) -> None:
        self._socket_path = socket_path
        self._on_event = on_event
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()

    async def _spawn(self) -> None:
        Path(self._socket_path).unlink(missing_ok=True)
        await asyncio.create_subprocess_exec(
            "mpv", "--idle=yes", "--no-video", "--no-terminal",
            "--ao=alsa", f"--audio-device=alsa/{settings.audio_output_device}",
            f"--volume={settings.music_volume}", "--volume-max=100",
            f"--input-ipc-server={self._socket_path}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(50):
            if Path(self._socket_path).exists():
                return
            await asyncio.sleep(0.1)
        raise MPVError("mpv не поднял IPC-сокет")

    async def _connect(self) -> None:
        async with self._lock:
            if self._writer is not None and not self._writer.is_closing():
                return
            try:
                reader, writer = await asyncio.open_unix_connection(self._socket_path)
            except (FileNotFoundError, ConnectionRefusedError):
                await self._spawn()
                reader, writer = await asyncio.open_unix_connection(self._socket_path)
            self._writer = writer
            self._reader_task = asyncio.create_task(self._read_loop(reader))

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                msg = json.loads(line)
                if "request_id" in msg and msg["request_id"] in self._pending:
                    fut = self._pending.pop(msg["request_id"])
                    if not fut.done():
                        fut.set_result(msg)
                elif "event" in msg and self._on_event is not None:
                    # Отдельной задачей: обработчик сам шлёт команды в mpv и
                    # ждёт ответа, который читает этот же цикл.
                    asyncio.create_task(self._on_event(msg))
        except Exception:
            logger.exception("Чтение IPC mpv упало")
        finally:
            self._writer = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MPVError("mpv закрыл соединение"))
            self._pending.clear()

    async def command(self, *args: Any) -> Any:
        await self._connect()
        assert self._writer is not None
        self._next_id += 1
        request_id = self._next_id
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        self._writer.write((json.dumps({"command": list(args), "request_id": request_id}) + "\n").encode())
        await self._writer.drain()
        try:
            msg = await asyncio.wait_for(fut, timeout=5)
        finally:
            self._pending.pop(request_id, None)
        if msg.get("error") != "success":
            raise MPVError(f"{args[0]}: {msg.get('error')}")
        return msg.get("data")

    async def get(self, name: str, default: Any = None) -> Any:
        try:
            return await self.command("get_property", name)
        except MPVError:
            # «property unavailable» — нормальное состояние, например
            # media-title в idle.
            return default

    async def set(self, name: str, value: Any) -> None:
        await self.command("set_property", name, value)
