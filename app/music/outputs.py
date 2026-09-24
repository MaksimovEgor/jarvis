"""Куда звучит плеер: mpv на asus или <audio> в браузере телефона/Mac.

Player (очередь, позиции, приглушение) один и тот же, различается только
выход:

    MpvOutput  — IPC к mpv; короткие треки качаются в кэш, длинное и радио
                 играются потоком (длинное — через /media/yt ядра).
    WebOutput  — ядро хранит «желаемое» состояние (src, start, paused) и шлёт
                 его браузеру по SSE; браузер сам играет и отчитывается
                 (/player/report) позицией и концом трека. Состояние шлётся
                 целиком, а не командами: после переподключения (iOS рвёт SSE
                 в фоне) браузер просто сверяется с последним снимком.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Awaitable, Callable

from app.config import settings
from app.music import media, sources
from app.music.models import Track
from app.music.mpv import MPV

logger = logging.getLogger("jarvis.outputs")

EndHandler = Callable[[str], Awaitable[None]]  # reason: "eof" | "error" | "stall"

# id событий хода — «<запуск ядра>:<номер>»: после рестарта ядра номера
# начинаются заново, и старый Last-Event-ID браузера не должен их скрыть.
BOOT = uuid.uuid4().hex[:8]
# Столько хранятся события хода для повтора переподключившемуся браузеру.
REPLAY_SECONDS = 600


class Output(ABC):
    def __init__(self) -> None:
        self.on_end: EndHandler | None = None

    async def _ended(self, reason: str) -> None:
        if self.on_end is not None:
            await self.on_end(reason)

    @abstractmethod
    async def load(self, track: Track, start: float, paused: bool) -> None: ...

    @abstractmethod
    async def set_paused(self, paused: bool) -> None: ...

    @abstractmethod
    async def is_paused(self) -> bool: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def seek(self, position: float) -> None: ...

    @abstractmethod
    async def position(self) -> float | None: ...

    @abstractmethod
    async def set_volume(self, level: int) -> bool:
        """False — устройство не даёт менять громкость (iPhone)."""

    @abstractmethod
    async def volume(self) -> int | None: ...

    async def stream_title(self) -> str | None:
        """Что сейчас в эфире радио (ICY), если выход это знает."""
        return None

    def prefetch(self, track: Track) -> None:
        """Подготовить следующий трек заранее, если выходу это помогает."""

    def set_meta(self, meta: dict[str, Any]) -> None:
        """Что показать о текущем треке рядом с плеером (оценка и т.п.).
        Экрана у mpv нет — по умолчанию ничего."""


class MpvOutput(Output):
    def __init__(self) -> None:
        super().__init__()
        self._mpv = MPV(settings.mpv_socket, on_event=self._on_event)
        self._entry_id: int | None = None

    async def _on_event(self, msg: dict[str, Any]) -> None:
        # reason=stop — это наш же loadfile replace/stop, не конец трека.
        if msg.get("event") != "end-file" or msg.get("reason") not in ("eof", "error"):
            return
        if msg.get("playlist_entry_id") != self._entry_id:
            return
        self._entry_id = None
        await self._ended(msg["reason"])

    async def _target(self, track: Track) -> str:
        if track.source == "youtube" and track.is_long:
            # Часы звука не качаем целиком — поток через прокси ядра.
            return f"{settings.core_url}/media/yt/{track.ref}"
        if sources.is_song(track):
            return str(await sources.download(track))
        return track.ref

    async def load(self, track: Track, start: float, paused: bool) -> None:
        target = await self._target(track)
        # start и pause у mpv — глобальные опции, переживают loadfile: задаём
        # перед каждой загрузкой. На паузе — во время голосовой команды новый
        # трек ждёт конца ответа.
        await self._mpv.set("start", f"{start:.1f}" if start > 0 else "none")
        await self._mpv.set("pause", paused)
        data = await self._mpv.command("loadfile", target, "replace")
        self._entry_id = data.get("playlist_entry_id") if isinstance(data, dict) else None

    async def set_paused(self, paused: bool) -> None:
        await self._mpv.set("pause", paused)

    async def is_paused(self) -> bool:
        return bool(await self._mpv.get("pause", True))

    async def stop(self) -> None:
        self._entry_id = None
        await self._mpv.command("stop")

    async def seek(self, position: float) -> None:
        await self._mpv.command("seek", position, "absolute")

    async def position(self) -> float | None:
        return await self._mpv.get("time-pos")

    async def set_volume(self, level: int) -> bool:
        await self._mpv.set("volume", level)
        return True

    async def volume(self) -> int | None:
        value = await self._mpv.get("volume")
        return int(value) if value is not None else None

    async def stream_title(self) -> str | None:
        metadata = await self._mpv.get("metadata", {}) or {}
        return metadata.get("icy-title")

    def prefetch(self, track: Track) -> None:
        if sources.is_song(track):
            task = asyncio.create_task(sources.download(track))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # ошибку увидим при проигрывании


class WebOutput(Output):
    # Браузер без отчёта дольше этого — считаем, что вкладка закрыта/уснула.
    ALIVE_SECONDS = 45
    # Сторож: должно играть, экран открыт, а позиция не растёт столько —
    # трек завис, дальше. Браузер шлёт позицию раз в 10 с, поэтому не меньше 25.
    STALL_SECONDS = 25.0
    STALL_CHECK_SECONDS = 5.0

    def __init__(self, device: str) -> None:
        super().__init__()
        self.device = device
        self._state: dict[str, Any] = {"seq": 0, "src": None, "title": None, "start": 0.0, "paused": True}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._reported_position: float | None = None
        self._reported_at = 0.0
        self._last_seen = 0.0
        self._volume_supported: bool | None = None
        self._volume: int | None = None
        # Safari играет длинное только через HLS (см. media.py).
        self._hls = False
        # События ходов с номерами — iOS рвёт SSE, пропущенное досылаем.
        self._log: deque[tuple[int, float, dict[str, Any]]] = deque(maxlen=200)
        self._event_id = 0
        # Вкладка на экране (visibilitychange) — иначе готовое шлём пушем.
        self._visible = True
        # Когда позиция последний раз росла — для сторожа зависаний.
        self._progress_at = time.time()
        self._watchdog: asyncio.Task[None] | None = None
        self._held = False
        self._blocked = False
        # Трек реально пошёл (позиция хоть раз выросла) — только такой может «зависнуть».
        self._started = False

    # --- связь с браузером ------------------------------------------------

    @property
    def watching(self) -> bool:
        """Экран открыт и получает события — ответ увидят без пуша."""
        return bool(self._subscribers) and self._visible

    @property
    def connected(self) -> bool:
        return bool(self._subscribers) or time.time() - self._last_seen < self.ALIVE_SECONDS

    def snapshot(self) -> dict[str, Any]:
        return {"type": "state", **self._state}

    def _push(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            queue.put_nowait(event)

    def subscribe(self, since: str | None, active: list[dict[str, Any]]) -> asyncio.Queue[dict[str, Any]]:
        """Снимок плеера, пропущенные после since события ходов и список идущих
        ходов (active) — по нему экран поймёт, какие просьбы потерялись.
        Событие с ключом "_id" уходит в SSE с этим id."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        queue.put_nowait(self.snapshot())
        for event in self._missed(since):
            queue.put_nowait(event)
        queue.put_nowait({"type": "turns", "active": active, "_id": f"{BOOT}:{self._event_id}"})
        self._subscribers.add(queue)
        # Переподключается только открытая вкладка (useMusic, visibilitychange).
        self._visible = True
        self._last_seen = time.time()
        return queue

    def _missed(self, since: str | None) -> list[dict[str, Any]]:
        # Без since — страница только открылась: готовое она берёт из истории.
        if not since:
            return []
        boot, _, n = since.partition(":")
        after = int(n) if boot == BOOT and n.isdigit() else 0
        now = time.time()
        return [
            {**event, "age": round(now - at), "_id": f"{BOOT}:{i}"}
            for i, at, event in self._log
            if i > after and now - at < REPLAY_SECONDS
        ]

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def announce(self, url: str) -> None:
        self._push({"type": "announce", "url": url})

    def notify(self, event: dict[str, Any]) -> None:
        """События ходов экрану (app/turns.py) — с номером и в журнал для повтора."""
        self._event_id += 1
        self._log.append((self._event_id, time.time(), event))
        self._push({**event, "_id": f"{BOOT}:{self._event_id}"})

    async def report(self, data: dict[str, Any]) -> None:
        """Отчёт браузера: позиция, пауза, конец/ошибка трека."""
        self._last_seen = time.time()
        if "volume_supported" in data:
            self._volume_supported = bool(data["volume_supported"])
        if "hls" in data:
            self._hls = bool(data["hls"])
        if data.get("visible") is not None:
            self._visible = bool(data["visible"])
        if data.get("blocked") is not None:
            self._blocked = bool(data["blocked"])
            self._progress_at = time.time()
        if data.get("held") is not None:
            self._held = bool(data["held"])
            self._progress_at = time.time()
        if data.get("seq") != self._state["seq"]:
            return  # отчёт о прошлом треке
        if data.get("position") is not None:
            position = float(data["position"])
            if self._reported_position is None or position > self._reported_position + 0.5:
                self._progress_at = time.time()
                self._started = True
            self._reported_position = position
            self._reported_at = time.time()
        if "paused" in data:
            # Пауза с экрана блокировки/кнопкой колонки — просто принимаем.
            self._state["paused"] = bool(data["paused"])
            self._progress_at = time.time()
        if data.get("ended"):
            await self._ended("eof")
        elif data.get("stalled"):
            logger.warning("%s: браузер говорит, что %s завис", self.device, self._state["src"])
            await self._ended("stall")
        elif data.get("error"):
            logger.warning("%s: браузер не смог проиграть %s: %s", self.device, self._state["src"], data["error"])
            await self._ended("error")

    # --- Output ------------------------------------------------------------

    async def _src(self, track: Track) -> str:
        if track.source == "youtube" and track.is_long:
            return f"media/hls/{track.ref}/index.m3u8" if self._hls else f"media/yt/{track.ref}"
        if sources.is_song(track):
            # Песня — сначала на диск (~1-3 с): исправленный yt-dlp файл или
            # MP3/FLAC Яндекса iPhone играет, а сырой поток YouTube (DASH) — нет.
            await sources.download(track)
            return f"media/file/{track.ref}"
        if track.source == "radio" and track.ref.startswith("https://"):
            return track.ref
        # http-радио страница по https не откроет (mixed content), локальные
        # файлы браузеру не видны — отдаём через ядро.
        return f"media/ref/{media.register(track.ref)}"

    async def load(self, track: Track, start: float, paused: bool) -> None:
        self._state = {
            "seq": self._state["seq"] + 1, "src": await self._src(track), "title": track.title,
            "start": start, "paused": paused, "live": track.source == "radio",
            # Оценить можно только трек с YouTube — у радио и файлов нет id.
            "ref": track.ref if sources.is_song(track) else None, "rating": None,
        }
        self._reported_position, self._reported_at = start, time.time()
        self._progress_at = time.time()
        self._started = False
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch_stall())
        self._push(self.snapshot())

    def _busy(self) -> bool:
        """Идёт ход (голосовая команда) — музыка на клиенте придержана, это не зависание."""
        from app.turns import turns  # turns → devices → outputs: импорт здесь, иначе цикл

        return bool(turns.active(self.device))

    async def _watch_stall(self) -> None:
        while True:
            await asyncio.sleep(self.STALL_CHECK_SECONDS)
            state = self._state
            if state["src"] is None or state["paused"] or state.get("live") or not self.watching:
                self._progress_at = time.time()
                continue
            # Не стартовал вовсе — блокировка iOS или загрузка: это решает браузер
            # (report stalled/blocked), сервер переключать не должен.
            if not self._started or self._held or self._blocked or self._busy():
                self._progress_at = time.time()
                continue
            if time.time() - self._progress_at > self.STALL_SECONDS:
                logger.warning("%s: позиция не растёт %.0f с — трек завис", self.device, self.STALL_SECONDS)
                self._progress_at = time.time()
                try:
                    await self._ended("stall")
                except Exception:
                    logger.exception("Сторож не смог переключить трек")

    def set_meta(self, meta: dict[str, Any]) -> None:
        if self._state["src"] is None or all(self._state.get(k) == v for k, v in meta.items()):
            return
        self._state.update(meta)
        self._push(self.snapshot())

    async def set_paused(self, paused: bool) -> None:
        if self._state["src"] is None:
            return
        if self._state["paused"] != paused:
            # Позиция на момент паузы, иначе position() продолжит «тикать».
            self._reported_position = await self.position()
            self._reported_at = time.time()
            self._progress_at = time.time()
        self._state["paused"] = paused
        self._push(self.snapshot())

    async def is_paused(self) -> bool:
        return bool(self._state["paused"])

    async def stop(self) -> None:
        self._state = {"seq": self._state["seq"] + 1, "src": None, "title": None, "start": 0.0, "paused": True}
        self._push(self.snapshot())

    async def seek(self, position: float) -> None:
        self._reported_position, self._reported_at = position, time.time()
        self._progress_at = time.time()
        self._push({"type": "seek", "seq": self._state["seq"], "position": position})

    async def position(self) -> float | None:
        if self._reported_position is None:
            return None
        if self._state["paused"]:
            return self._reported_position
        return self._reported_position + (time.time() - self._reported_at)

    async def set_volume(self, level: int) -> bool:
        if self._volume_supported is False:
            return False
        self._volume = level
        self._push({"type": "volume", "level": level})
        return True

    async def volume(self) -> int | None:
        return self._volume

    def prefetch(self, track: Track) -> None:
        if sources.is_song(track):
            task = asyncio.create_task(sources.download(track))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
