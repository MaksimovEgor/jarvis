"""Плеер одного устройства: очередь треков поверх выхода (mpv или браузер).

Плееров столько, сколько устройств (app/music/devices.py), но логика одна:

    play_youtube("Queen") ─► search → [трек]  ─► играет сразу
                                   └► mix (фоном) ─► +~20 похожих в очередь
    выход сообщил «трек кончился» ─► следующий в очереди (скачан заранее)

Длинное (книги, подкасты) начинается с сохранённой позиции минус 10с и
сохраняет позицию на паузе, стопе, переключении и раз в 15с
(app/music/positions.py).

Приглушение: listener зовёт duck() на wake word и unduck() после ответа.
Если во время команды включили новый трек или сказали «продолжи», он не
начинает играть поверх ответа, а ждёт unduck(). Веб приглушает себя сам,
на клиенте.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.music import positions, radio, youtube
from app.music.models import Kind, Track
from app.music.outputs import Output

logger = logging.getLogger("jarvis.player")

LOCAL_EXTS = (".mp3", ".flac", ".ogg", ".m4a", ".wav")
# Страховка, если listener упал между duck и unduck: музыка не должна
# остаться на паузе навсегда. С запасом над таймаутом ответа ядра.
DUCK_TIMEOUT = 200.0
# Сколько битых треков подряд пропускать, прежде чем сдаться.
MAX_SKIPS = 3
AUTOSAVE_SECONDS = 15


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Player:
    def __init__(self, device: str, output: Output) -> None:
        self.device = device
        self.output = output
        output.on_end = self._on_end
        self._queue: list[Track] = []
        self._pos = -1
        # Меняется при каждой новой очереди — фоновые задачи старой очереди
        # (догрузка микса) по нему понимают, что опоздали.
        self._generation = 0
        self._lock = asyncio.Lock()
        # Счётчик, а не флаг: голосовая команда и объявление таймера могут
        # пересечься, музыка вернётся после последнего unduck.
        self._duck_depth = 0
        self._resume_on_unduck = False
        self._duck_guard: asyncio.Task[None] | None = None
        self._autosave: asyncio.Task[None] | None = None

    @property
    def _duck_active(self) -> bool:
        return self._duck_depth > 0

    @property
    def current(self) -> Track | None:
        return self._queue[self._pos] if 0 <= self._pos < len(self._queue) else None

    # --- позиции длинного ---------------------------------------------------

    async def _save_position(self, finished: bool = False) -> None:
        track = self.current
        if track is not None and track.is_long:
            positions.save(track, await self.output.position(), finished=finished)

    async def _autosave_loop(self) -> None:
        while True:
            await asyncio.sleep(AUTOSAVE_SECONDS)
            try:
                if self.current is not None and self.current.is_long and not await self.output.is_paused():
                    await self._save_position()
            except Exception:
                logger.exception("Не получилось сохранить позицию")

    # --- загрузка ---------------------------------------------------------

    async def _play_index(self, index: int) -> Track:
        await self._save_position()
        if self._autosave is None:
            self._autosave = asyncio.create_task(self._autosave_loop())
        for i in range(index, min(index + MAX_SKIPS, len(self._queue))):
            track = self._queue[i]
            try:
                await self.output.load(track, positions.start_for(track), paused=self._duck_active)
            except Exception as exc:
                logger.warning("Пропускаю «%s»: %s", track.title, exc)
                continue
            self._pos = i
            self._resume_on_unduck = self._duck_active
            if i + 1 < len(self._queue):
                self.output.prefetch(self._queue[i + 1])
            logger.info("%s играет: %s", self.device, track.title)
            return track
        raise RuntimeError("не получилось загрузить ни один трек")

    async def _start_queue(self, tracks: list[Track]) -> Track:
        await self._save_position()
        self._generation += 1
        self._queue = tracks
        self._pos = -1
        return await self._play_index(0)

    async def _on_end(self, reason: str) -> None:
        track = self.current
        async with self._lock:
            if track is None or self.current is not track:
                return  # пока ждали lock, очередь сменили
            if reason == "error":
                logger.warning("%s: не смог проиграть «%s»", self.device, track.title)
            elif track.is_long:
                positions.save(track, track.duration or 0, finished=True)
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
            if was_last and self._pos + 1 < len(self._queue):
                self.output.prefetch(self._queue[self._pos + 1])

    # --- включение --------------------------------------------------------

    async def play_youtube(self, query: str, kind: Kind = "music") -> str:
        async with self._lock:
            candidates = await youtube.search(query, kind)
            if not candidates:
                return f"На YouTube ничего не нашлось по запросу «{query}»."
            # Первый, что загрузится (_play_index пропускает недоступные);
            # остальные кандидаты в очереди не нужны — дальше пойдёт микс.
            track = await self._start_queue(candidates[:MAX_SKIPS])
            self._queue, self._pos = [track], 0
            generation = self._generation
        if kind == "music":
            asyncio.create_task(self._extend_with_mix(track, generation))
            return f"Включаю: {track.title}. Дальше похожие треки."
        start = positions.start_for(track)
        return f"Включаю: {track.title}" + (f", продолжаю с {_clock(start)}." if start else ".")

    async def resume_listening(self, query: str | None) -> str:
        items = positions.unfinished(query)
        if not items:
            return "Недослушанного нет." if not query else f"Недослушанного по запросу «{query}» нет."
        async with self._lock:
            track = await self._start_queue([items[0].track()])
        return f"Продолжаю «{track.title}» с {_clock(positions.start_for(track))}."

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
        await self.output.set_paused(True)
        await self._save_position()
        return "Пауза."

    async def resume(self) -> str:
        if self.current is None:
            return "Нечего продолжать — очередь пуста."
        if self._duck_active:
            self._resume_on_unduck = True
        else:
            await self.output.set_paused(False)
        return "Продолжаю."

    async def stop(self) -> str:
        async with self._lock:
            await self._save_position()
            self._generation += 1
            self._queue, self._pos = [], -1
            self._resume_on_unduck = False
            await self.output.stop()
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

    async def seek(self, delta: float | None = None, position: float | None = None) -> str:
        track = self.current
        if track is None or track.source == "radio":
            return "Перематывать нечего."
        current = await self.output.position() or 0.0
        target = max(0.0, position if position is not None else current + (delta or 0))
        if track.duration:
            target = min(target, track.duration - 1)
        await self.output.seek(target)
        return f"Перемотал на {_clock(target)}."

    async def volume(self, level: int | None = None, delta: int | None = None) -> str:
        current = await self.output.volume()
        base = current if current is not None else settings.music_volume
        target = max(0, min(100, level if level is not None else base + (delta or 0)))
        if not await self.output.set_volume(target):
            return "На этом устройстве громкость меняется кнопками телефона."
        return f"Громкость {target} из 100."

    async def now_playing(self) -> str:
        track = self.current
        if track is None:
            return "Сейчас ничего не играет."
        paused = await self.output.is_paused() and not self._resume_on_unduck
        state = "На паузе" if paused else "Играет"
        if track.source == "radio":
            song = await self.output.stream_title()
            return f"{state} радио {track.title}" + (f", сейчас в эфире: {song}." if song else ".")
        if track.is_long:
            pos = await self.output.position() or 0
            total = f" из {_clock(track.duration)}" if track.duration else ""
            return f"{state}: {track.title}, {_clock(pos)}{total}."
        left = len(self._queue) - self._pos - 1
        return f"{state}: {track.title}. В очереди ещё {left}."

    def upcoming(self, limit: int = 5) -> list[str]:
        return [t.title for t in self._queue[self._pos + 1:self._pos + 1 + limit]]

    # --- приглушение на время голосовой команды ---------------------------

    async def duck(self) -> None:
        self._duck_depth += 1
        if self._duck_depth == 1:
            playing = self.current is not None and not await self.output.is_paused()
            self._resume_on_unduck = playing
            if playing:
                await self.output.set_paused(True)
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
            await self.output.set_paused(False)
        self._resume_on_unduck = False

    async def _auto_unduck(self) -> None:
        await asyncio.sleep(DUCK_TIMEOUT)
        logger.warning("unduck не пришёл за %.0fс — возвращаю музыку сам", DUCK_TIMEOUT)
        self._duck_guard = None
        await self.unduck(force=True)

    async def state(self) -> dict[str, Any]:
        track = self.current
        return {
            "device": self.device,
            "track": track.title if track else None,
            "source": track.source if track else None,
            "paused": await self.output.is_paused() if track else None,
            "position": await self.output.position() if track else None,
            "ducked": self._duck_active,
            "volume": await self.output.volume(),
            "upcoming": self.upcoming(),
        }
