"""MCP-сервер Джарвиса для Hermes: плеер, таймеры/напоминания, «сказать вслух».

Живёт внутри jarvis-core (streamable HTTP на /mcp), а не отдельным stdio-
процессом: плеер должен быть один на систему — им же управляет listener
(пауза на время голосовой команды). Подключение в Hermes:

    hermes mcp add jarvis --url http://127.0.0.1:8000/mcp

Описания инструментов — это то, по чему LLM решает, что звать, поэтому
они подробные и с примерами фраз.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from mcp.server.mcpserver import MCPServer

from app.music.player import player
from app.services import speaker
from app.timers import timers

mcp = MCPServer(
    name="jarvis",
    instructions=(
        "Домашний сервер Джарвиса: всё звучит дома, в колонке/гарнитуре, а не на "
        "устройстве собеседника. Музыка и радио — play_music, play_radio, music_control, "
        "set_volume, now_playing. Таймеры и напоминания голосом — set_timer, remind, "
        "list_timers, cancel_timer. Сказать что-то вслух дома — announce. Никогда не "
        "говори, что действие выполнено, не вызвав инструмент. Результат перескажи коротко."
    ),
)


@mcp.tool()
async def play_music(query: str, source: Literal["youtube", "local"] = "youtube") -> str:
    """Включить музыку: артиста, песню, альбом, жанр или настроение.

    Ищет на YouTube и сразу строит очередь из похожих треков (как «Моя волна»),
    поэтому для «включи Queen» или «включи что-нибудь спокойное» достаточно
    одного вызова. query — поисковый запрос как для YouTube, например
    «Queen», «Кино Группа крови», «lofi hip hop», «русский рок 90-х».
    source=local — только файлы из домашней библиотеки на сервере.
    """
    if source == "local":
        return await player.play_local(query)
    return await player.play_youtube(query)


@mcp.tool()
async def play_radio(
    name: str | None = None,
    genre: str | None = None,
    country_code: str | None = None,
) -> str:
    """Включить интернет-радио (каталог radio-browser.info).

    name — часть названия станции: «Европа Плюс», «Record», «Маяк», «Радио Джаз».
    genre — тег жанра ПО-АНГЛИЙСКИ: jazz, rock, classical, pop, news, lounge, chillout.
    country_code — двухбуквенный код страны, например RU.
    Хотя бы один параметр обязателен. «Включи радио» без уточнений — genre=pop, country_code=RU.
    """
    if not (name or genre or country_code):
        return "Уточни станцию, жанр или страну."
    return await player.play_radio(name, genre, country_code)


@mcp.tool()
async def music_control(action: Literal["pause", "resume", "stop", "next", "previous"]) -> str:
    """Управление плеером: pause — пауза, resume — продолжить,
    stop — выключить музыку совсем, next — следующий трек/станция,
    previous — предыдущий трек."""
    handlers = {
        "pause": player.pause,
        "resume": player.resume,
        "stop": player.stop,
        "next": player.next,
        "previous": player.previous,
    }
    return await handlers[action]()


@mcp.tool()
async def set_volume(level: int | None = None, delta: int | None = None) -> str:
    """Громкость музыки по шкале 0-100.

    level — абсолютное значение («громкость на 30» → level=30, «на половину» → 50).
    delta — относительное изменение: «тише» → -15, «громче» → +15,
    «чуть-чуть тише» → -5.
    """
    if level is None and delta is None:
        return "Укажи level или delta."
    return await player.volume(level=level, delta=delta)


@mcp.tool()
async def now_playing() -> str:
    """Что сейчас играет (трек или радиостанция, на паузе или нет) и что дальше в очереди."""
    status = await player.now_playing()
    upcoming = player.upcoming(3)
    return status + (f" Дальше: {'; '.join(upcoming)}." if upcoming else "")


@mcp.tool()
async def set_timer(minutes: float = 0, seconds: int = 0, label: str | None = None) -> str:
    """Таймер, который прозвенит и скажет вслух дома. «Поставь таймер на 5 минут» →
    minutes=5; «на полторы минуты» → minutes=1.5; «на 30 секунд» → seconds=30.
    label — о чём таймер, если назван: «пельмени», «проверить духовку»."""
    total = minutes * 60 + seconds
    if total <= 0:
        return "Укажи длительность таймера."
    # Модель иногда передаёт label="таймер" — это не название.
    if label and label.strip().lower() in ("таймер", "timer"):
        label = None
    text = f"Таймер «{label}»: время вышло." if label else "Время вышло, таймер сработал."
    alarm = timers.add(time.time() + total, text)
    return f"Таймер {alarm.id} поставлен, сработает в {alarm.due_local}."


@mcp.tool()
async def remind(at: str, text: str) -> str:
    """Напоминание голосом дома в конкретное время. at — локальное время
    сервера в ISO: «2026-09-24T09:00». text — что сказать, как обращение:
    «Пора выходить на встречу». Для «через N минут» удобнее set_timer с label."""
    try:
        due = datetime.fromisoformat(at)
    except ValueError:
        return "Не понял время: нужен формат ГГГГ-ММ-ДДTЧЧ:ММ."
    if due.tzinfo is not None:
        due = due.astimezone().replace(tzinfo=None)
    now = datetime.now()
    if due <= now:
        return f"Это время уже прошло, сейчас {now:%d.%m %H:%M}."
    alarm = timers.add(due.timestamp(), f"Напоминаю: {text}")
    return f"Напоминание {alarm.id} на {alarm.due_local}."


@mcp.tool()
async def list_timers() -> str:
    """Какие таймеры и напоминания сейчас стоят (id, время, текст)."""
    alarms = timers.list()
    if not alarms:
        return "Таймеров и напоминаний нет."
    return " ".join(f"[{a.id}] {a.due_local} — {a.text}" for a in alarms)


@mcp.tool()
async def cancel_timer(alarm_id: str | None = None, cancel_all: bool = False) -> str:
    """Отменить таймер/напоминание по id (из list_timers). Если стоит ровно
    один — id можно не указывать. cancel_all=true — отменить все."""
    alarms = timers.list()
    if cancel_all:
        for a in alarms:
            timers.cancel(a.id)
        return f"Отменено: {len(alarms)}."
    if alarm_id is None:
        if len(alarms) != 1:
            return "Уточни, какой: " + (await list_timers())
        alarm_id = alarms[0].id
    alarm = timers.cancel(alarm_id)
    return f"Отменил: {alarm.text}" if alarm else f"Таймера {alarm_id} нет."


@mcp.tool()
async def announce(text: str) -> str:
    """Сказать фразу вслух дома прямо сейчас (с сигналом, музыка на это время
    встаёт на паузу). Для запланированных задач cron: «напомни голосом …»."""
    await speaker.announce(text)
    return "Сказал."
