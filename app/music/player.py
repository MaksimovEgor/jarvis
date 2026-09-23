"""Плеер Джарвиса: очередь треков поверх одного процесса mpv.

Единственный владелец mpv в системе — этот объект внутри jarvis-core. Им
пользуются MCP-инструменты (Hermes), встроенный агент и listener (пауза на
время голосовой команды), поэтому состояние очереди и приглушения одно.

    play_youtube("Queen") ─► search → [трек]  ─► играет сразу
                                   └► mix (фоном) ─► +~20 похожих в очередь
    mpv end-file(eof) ─► следующий трек очереди (скачан заранее, _prefetch)

Приглушение: listener зовёт duck() на wake word и unduck() после ответа.
Если во время команды включили новый трек или сказали «продолжи», он не
начинает играть поверх ответа, а ждёт unduck().
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.music import radio, youtube
from app.music.models import Track
from app.music.mpv import MPV

logger = logging.getLogger("jarvis.player")

LOCAL_EXTS = (".mp3", ".flac", ".ogg", ".m4a", ".wav")
# Страховка, если listener упал между duck и unduck: музыка не должна
# остаться на паузе навсегда. С запасом над таймаутом ответа ядра.
DUCK_TIMEOUT = 200.0
# Сколько битых треков подряд пропускать, прежде чем сдаться.
MAX_SKIPS = 3


class Player:
    def __init__(self) -> None:
        self._mpv = MPV(settings.mpv_socket, on_event=self._on_event)
        self._queue: list[Track] = []
        self._pos = -1
        self._entry_id: int | None = None
        # Меняется при каждой новой очереди — фоновые задачи старой очереди
        # (догрузка микса) по нему понимают, что опоздали.
        self._generation = 0
        self._lock = asyncio.Lock()
        # Счётчик, а не флаг: голосовая команда и объявление таймера могут
        # пересечься, музыка вернётся после последнего unduck.
        self._duck_depth = 0
        self._resume_on_unduck = False
        self._duck_guard: asyncio.Task[None] | None = None

    @property
    def _duck_active(self) -> bool:
        return self._duck_depth > 0

    @property
    def current(self) -> Track | None:
        return self._queue[self._pos] if 0 <= self._pos < len(self._queue) else None

    # --- загрузка ---------------------------------------------------------

    async def _play_index(self, index: int) -> Track:
        for i in range(index, min(index + MAX_SKIPS, len(self._queue))):
            track = self._queue[i]
            try:
                target = str(await youtube.download(track)) if track.source == "youtube" else track.ref
            except Exception as exc:
                logger.warning("Пропускаю «%s»: %s", track.title, exc)
                continue
            self._pos = i
            # pause у mpv переживает loadfile: во время голосовой команды
            # новый трек загружается на паузе и стартует после ответа.
            await self._mpv.set("pause", self._duck_active)
            self._resume_on_unduck = self._duck_active
            data = await self._mpv.command("loadfile", target, "replace")
            self._entry_id = data.get("playlist_entry_id") if isinstance(data, dict) else None
            self._prefetch(i + 1)
            logger.info("Играет: %s", track.title)
            return track
        raise RuntimeError("не получилось загрузить ни один трек")

    def _prefetch(self, index: int) -> None:
        if index < len(self._queue) and self._queue[index].source == "youtube":
            task = asyncio.create_task(youtube.download(self._queue[index]))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # ошибку увидим при проигрывании

    async def _start_queue(self, tracks: list[Track]) -> Track:
        self._generation += 1
        self._queue = tracks
        self._pos = -1
        return await self._play_index(0)

    async def _on_event(self, msg: dict[str, Any]) -> None:
        # reason=stop — это наш же loadfile replace/stop, не конец трека.
        if msg.get("event") != "end-file" or msg.get("reason") not in ("eof", "error"):
            return
        async with self._lock:
            if msg.get("playlist_entry_id") != self._entry_id:
                return
            if msg.get("reason") == "error":
                logger.warning("mpv не смог проиграть «%s»", self.current.title if self.current else "?")
            self._entry_id = None
            if self._pos + 1 < len(self._queue):
                try:
                    await self._play_index(self._pos + 1)
                    return
                except Exception:
                    logger.exception("Не получилось переключить трек")
            self._queue, self._pos = [], -1

    async def _extend_with_mix(self, seed: Track, generation: int) -> None:
        try:
            tracks = await youtube.mix(seed)
        except Exception:
            logger.exception("Не получилось получить микс для «%s»", seed.title)
            return
        async with self._lock:
            if generation != self._generation:
                return
            was_last = self._pos == len(self._queue) - 1
            self._queue.extend(tracks)
            if was_last:
                self._prefetch(self._pos + 1)

    # --- включение --------------------------------------------------------

    async def play_youtube(self, query: str) -> str:
        async with self._lock:
            seed = await youtube.search(query)
            if seed is None:
                return f"На YouTube ничего не нашлось по запросу «{query}»."
            track = await self._start_queue([seed])
            generation = self._generation
        asyncio.create_task(self._extend_with_mix(seed, generation))
        return f"Включаю: {track.title}. Дальше похожие треки."

    async def play_radio(self, name: str | None, genre: str | None, country_code: str | None) -> str:
        stations = await radio.search(name=name, genre=genre, country_code=country_code)
        if not stations:
            return "Не нашёл такую радиостанцию."
        async with self._lock:
            # Остальные найденные станции — в очередь: «следующая» переключит
            # на похожую, а битый поток сам пропустится.
            track = await self._start_queue(stations)
        return f"Включаю радио {track.title}."

    async def play_local(self, query: str) -> str:
        library = Path(settings.music_library_dir)
        cache = Path(settings.music_cache_dir).resolve()
        needle = query.lower()
        files = sorted(
            p for p in library.rglob("*")
            if p.suffix.lower() in LOCAL_EXTS and needle in str(p.relative_to(library)).lower()
            and cache not in p.resolve().parents
        ) if library.exists() else []
        if not files:
            return f"В локальной библиотеке ничего не нашлось по запросу «{query}»."
        async with self._lock:
            track = await self._start_queue([Track(title=p.stem, source="local", ref=str(p)) for p in files])
        return f"Включаю {track.title}, всего {len(files)} в очереди."

    # --- управление -------------------------------------------------------

    async def pause(self) -> str:
        if self.current is None:
            return "Сейчас ничего не играет."
        self._resume_on_unduck = False
        await self._mpv.set("pause", True)
        return "Пауза."

    async def resume(self) -> str:
        if self.current is None:
            return "Нечего продолжать — очередь пуста."
        if self._duck_active:
            self._resume_on_unduck = True
        else:
            await self._mpv.set("pause", False)
        return "Продолжаю."

    async def stop(self) -> str:
        async with self._lock:
            self._generation += 1
            self._queue, self._pos, self._entry_id = [], -1, None
            self._resume_on_unduck = False
            await self._mpv.command("stop")
        return "Остановил."

    async def next(self) -> str:
        async with self._lock:
            if self._pos + 1 >= len(self._queue):
                return "Дальше в очереди ничего нет."
            track = await self._play_index(self._pos + 1)
        return f"Следующий: {track.title}."

    async def previous(self) -> str:
        async with self._lock:
            if self._pos <= 0:
                return "Это первый трек в очереди."
            track = await self._play_index(self._pos - 1)
        return f"Предыдущий: {track.title}."

    async def volume(self, level: int | None = None, delta: int | None = None) -> str:
        current = int(await self._mpv.get("volume", settings.music_volume))
        target = level if level is not None else current + (delta or 0)
        target = max(0, min(100, target))
        await self._mpv.set("volume", target)
        return f"Громкость {target} из 100."

    async def now_playing(self) -> str:
        track = self.current
        if track is None:
            return "Сейчас ничего не играет."
        paused = await self._mpv.get("pause", False) and not self._resume_on_unduck
        state = "На паузе" if paused else "Играет"
        if track.source == "radio":
            metadata = await self._mpv.get("metadata", {}) or {}
            song = metadata.get("icy-title")
            return f"{state} радио {track.title}" + (f", сейчас в эфире: {song}." if song else ".")
        left = len(self._queue) - self._pos - 1
        return f"{state}: {track.title}. В очереди ещё {left}."

    def upcoming(self, limit: int = 5) -> list[str]:
        return [t.title for t in self._queue[self._pos + 1:self._pos + 1 + limit]]

    # --- приглушение на время голосовой команды ---------------------------

    async def duck(self) -> None:
        self._duck_depth += 1
        if self._duck_depth == 1:
            playing = self.current is not None and not await self._mpv.get("pause", True)
            self._resume_on_unduck = playing
            if playing:
                await self._mpv.set("pause", True)
        if self._duck_guard:
            self._duck_guard.cancel()
        self._duck_guard = asyncio.create_task(self._auto_unduck())

    async def unduck(self, force: bool = False) -> None:
        if not self._duck_active:
            return
        self._duck_depth = 0 if force else self._duck_depth - 1
        if self._duck_active:
            return
        if self._duck_guard:
            self._duck_guard.cancel()
            self._duck_guard = None
        if self._resume_on_unduck and self.current is not None:
            await self._mpv.set("pause", False)
        self._resume_on_unduck = False

    async def _auto_unduck(self) -> None:
        await asyncio.sleep(DUCK_TIMEOUT)
        logger.warning("unduck не пришёл за %.0fс — возвращаю музыку сам", DUCK_TIMEOUT)
        self._duck_guard = None
        await self.unduck(force=True)

    async def state(self) -> dict[str, Any]:
        track = self.current
        return {
            "track": track.title if track else None,
            "source": track.source if track else None,
            "paused": bool(await self._mpv.get("pause", False)) if track else None,
            "ducked": self._duck_active,
            "volume": await self._mpv.get("volume"),
            "upcoming": self.upcoming(),
        }


player = Player()
