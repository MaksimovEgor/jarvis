"""MCP-сервер Джарвиса для Hermes: плеер, таймеры/напоминания, «сказать вслух».

Живёт внутри jarvis-core (streamable HTTP на /mcp), а не отдельным stdio-
процессом: плееры устройств и «чья сейчас реплика» (devices.py) — в памяти
ядра, инструменты должны видеть их же. Подключение в Hermes:

    hermes mcp add jarvis --url http://127.0.0.1:8000/mcp

Описания инструментов — это то, по чему LLM решает, что звать, поэтому
они подробные и с примерами фраз.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Literal

from mcp.server.mcpserver import MCPServer

from app.music import devices
from app.music.library import library
from app.music.models import Diversity, Kind, Language, Mood, MoodEnergy, Rating, WaveSpec
from app.music.wave import MOOD_PRESET, spec_mood
from app.music.player import Player
from app.services import speaker, telegram, transcribe as transcriber
from app.timers import timers

logger = logging.getLogger(__name__)

NO_DEVICE = "Не знаю, где включить: открой Джарвиса на телефоне или в браузере."

mcp = MCPServer(
    name="jarvis",
    instructions=(
        "Плеер и таймеры Джарвиса. Звучит на том устройстве, с которого пришла реплика "
        "(телефон, Mac); из Telegram — на последнем, где был Джарвис. Музыка, книги, подкасты, радио — "
        "play_music, resume_listening, play_radio, music_control, seek, set_volume, now_playing. "
        "«Моя волна» и вкус: play_wave, play_liked, rate_track, music_taste_note. "
        "Таймеры и напоминания голосом — set_timer, remind, list_timers, cancel_timer. "
        "Сказать вслух — announce. Расшифровать/пересказать видео, подкаст, книгу, запись — transcribe. Никогда не говори, что действие выполнено, не вызвав "
        "инструмент. Результат перескажи коротко."
    ),
)


@mcp.tool()
async def play_music(
    query: str,
    kind: Kind = "music",
    source: Literal["youtube", "local"] = "youtube",
) -> str:
    """Включить музыку, аудиокнигу или подкаст.

    kind=music — артист, песня, альбом, жанр, настроение: ищет на YouTube и
    сразу строит очередь похожих (как «Моя волна»), одного вызова достаточно.
    query — запрос как для YouTube: «Queen», «Кино Группа крови», «lofi hip hop».
    kind=audiobook — аудиокнига («Мастер и Маргарита аудиокнига»),
    kind=podcast — подкаст, лекция, выпуск новостей. Длинное продолжается с
    места, где остановились в прошлый раз, на любом устройстве.
    source=local — только файлы из домашней библиотеки на сервере.
    """
    player = devices.current_player()
    if player is None:
        return NO_DEVICE
    if source == "local":
        return await player.play_local(query)
    return await player.play_query(query, kind)


@mcp.tool()
async def play_wave(
    mood: Mood = "auto",
    activity: str | None = None,
    mood_energy: MoodEnergy | None = None,
    character: Diversity | None = None,
    language: Language | None = None,
    station: str | None = None,
) -> str:
    """«Моя волна» — бесконечный поток под вкус хозяина и время суток (Яндекс
    Музыка + YouTube). Зови на «включи музыку», «мою волну», «что-нибудь» без
    артиста. Все настройки как в «Моей волне» Яндекса, можно сочетать:
    mood_energy: active (бодрое), fun (весёлое), calm (спокойное), sad (грустное);
    character: favorite (любимое), discover (незнакомое, «что-то новое»), popular;
    language: russian, not-russian (иностранное), without-words (без слов);
    activity — занятие: wake-up, run, workout, driving, road-trip, work-background,
    study-background, party, romantic-date, beloved, fall-asleep, sex;
    station — станция Яндекса: genre:rusrock, genre:jazz, epoch:nineties,
    mood:dark, mood:winter … («включи русский рок», «музыку 90-х»).
    mood — короткий вариант без Яндекса: auto, energetic, calm, focus, sleep, discover.
    Примеры: «грустное на русском» → mood_energy=sad, language=russian;
    «для бега» → activity=run; «незнакомое бодрое» → character=discover, mood_energy=active."""
    player = devices.current_player()
    if player is None:
        return NO_DEVICE
    base = MOOD_PRESET[mood]
    chosen = station or (f"activity:{activity}" if activity else None)
    spec = WaveSpec(
        station=chosen or base.station,
        mood_energy=mood_energy or base.mood_energy,
        diversity=character or base.diversity,
        language=language or base.language,
        label=" · ".join(x for x in (chosen, mood_energy, character, language) if x) or base.label,
    )
    return await player.play_wave(spec_mood(spec), spec=spec)


@mcp.tool()
async def play_liked(query: str | None = None) -> str:
    """Лайкнутые треки («Моя музыка») вперемешку: «включи мои лайки», «включи любимое».
    query — отфильтровать по исполнителю/названию («включи моё любимое из Кино»)."""
    player = devices.current_player()
    return await player.play_liked(query) if player else NO_DEVICE


@mcp.tool()
async def rate_track(value: Literal["like", "dislike", "none"], which: Literal["current", "previous"] = "current") -> str:
    """Лайк/дизлайк трека. like — «нравится», «сохрани» (трек навсегда в «Моей музыке»);
    dislike — «не нравится», «не включай больше» (сразу переключит, больше не прозвучит,
    исполнитель после двух дизлайков — реже); none — снять оценку.
    which=previous — «лайкни прошлую песню»."""
    player = devices.current_player()
    if player is None:
        return NO_DEVICE
    values: dict[str, Rating | None] = {"like": 1, "dislike": -1, "none": None}
    return await player.rate(values[value], which=which)


@mcp.tool()
async def show_lyrics() -> str:
    """Показать на экране текст играющей песни (подсветка строк, как в Яндекс
    Музыке): «покажи текст», «что он поёт», «какие там слова»."""
    device = devices.current_device()
    if device is None or not devices.is_web(device):
        return NO_DEVICE
    if devices.player_for(device).current is None:
        return "Сейчас ничего не играет."
    devices.web_output(device).show("lyrics")
    return "Показываю текст."


@mcp.tool()
async def music_taste_note(text: str) -> str:
    """Запомнить музыкальное предпочтение для «Моей волны»: «я не люблю рэп»,
    «утром хочу русский рок», «больше джаза по вечерам». text — суть коротко, от
    третьего лица: «не любит рэп». Учитывается в следующих подборках."""
    library.add_note(text)
    return "Запомнил, учту в подборках."


@mcp.tool()
async def resume_listening(query: str | None = None) -> str:
    """Продолжить недослушанную книгу/подкаст с места остановки:
    «продолжи книгу», «давай дальше Мастера и Маргариту» (query — часть
    названия). Без query — последнее недослушанное."""
    player = devices.current_player()
    return await player.resume_listening(query) if player else NO_DEVICE


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
    player = devices.current_player()
    return await player.play_radio(name, genre, country_code) if player else NO_DEVICE


Where = Literal["here", "everywhere"]


def _players(where: Where) -> list[Player]:
    if where == "everywhere":
        return devices.active_players()
    player = devices.current_player()
    return [player] if player else []


@mcp.tool()
async def music_control(
    action: Literal["pause", "resume", "stop", "next", "previous"],
    where: Where = "here",
) -> str:
    """Управление плеером: pause — пауза, resume — продолжить,
    stop — выключить музыку совсем, next — следующий трек/станция,
    previous — предыдущий трек.
    where: here — на устройстве, с которого говорят (по умолчанию);
    everywhere — на всех устройствах («выключи везде»)."""
    players = _players(where)
    if not players:
        return "Нигде ничего не играет."
    replies = []
    for player in players:
        handlers = {
            "pause": player.pause,
            "resume": player.resume,
            "stop": player.stop,
            "next": player.next,
            "previous": player.previous,
        }
        replies.append(await handlers[action]())
    return replies[0] if len(replies) == 1 else f"Готово на {len(replies)} устройствах."


@mcp.tool()
async def set_volume(level: int | None = None, delta: int | None = None) -> str:
    """Громкость музыки по шкале 0-100.

    level — абсолютное значение («громкость на 30» → level=30, «на половину» → 50).
    delta — относительное изменение: «тише» → -15, «громче» → +15,
    «чуть-чуть тише» → -5.
    """
    if level is None and delta is None:
        return "Укажи level или delta."
    player = devices.current_player()
    return await player.volume(level=level, delta=delta) if player else NO_DEVICE


@mcp.tool()
async def now_playing(where: Where = "here") -> str:
    """Что сейчас играет (трек или радиостанция, на паузе или нет) и что дальше.
    where: here — на этом устройстве, everywhere — на всех."""
    players = _players(where)
    if not players:
        return "Нигде ничего не играет."
    replies = []
    for player in players:
        status = await player.now_playing()
        upcoming = player.upcoming(3)
        prefix = f"Устройство …{player.device[-4:]}: " if len(players) > 1 else ""
        replies.append(prefix + status + (f" Дальше: {'; '.join(upcoming)}." if upcoming else ""))
    return " ".join(replies)


@mcp.tool()
async def seek(delta_seconds: int | None = None, position_seconds: int | None = None) -> str:
    """Перемотка в книге/подкасте/треке. «Назад на 30 секунд» → delta_seconds=-30,
    «вперёд на 5 минут» → 300, «на час двадцать» → position_seconds=4800."""
    if delta_seconds is None and position_seconds is None:
        return "Укажи delta_seconds или position_seconds."
    player = devices.current_player()
    return await player.seek(delta=delta_seconds, position=position_seconds) if player else NO_DEVICE


@mcp.tool()
async def set_timer(minutes: float = 0, seconds: int = 0, label: str | None = None) -> str:
    """Таймер, который прозвенит и скажет вслух на этом устройстве. «Поставь таймер на 5 минут» →
    minutes=5; «на полторы минуты» → minutes=1.5; «на 30 секунд» → seconds=30.
    label — только если пользователь сказал, для чего таймер: «пельмени»,
    «проверить духовку». «Таймер на 5 минут» — без label."""
    total = minutes * 60 + seconds
    if total <= 0:
        return "Укажи длительность таймера."
    # Модель иногда передаёт label="таймер" — это не название.
    if label and label.strip().lower().startswith(("таймер", "timer")):
        label = None
    text = f"Таймер «{label}»: время вышло." if label else "Время вышло, таймер сработал."
    alarm = timers.add(time.time() + total, text, devices.current_device() or devices.ASUS)
    return f"Таймер {alarm.id} поставлен, сработает в {alarm.due_local}."


@mcp.tool()
async def remind(at: str, text: str) -> str:
    """Напоминание голосом на этом устройстве в конкретное время. at — локальное время
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
    alarm = timers.add(due.timestamp(), f"Напоминаю: {text}", devices.current_device() or devices.ASUS)
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
async def send_telegram(text: str) -> str:
    """Прислать хозяину сообщение в Telegram прямо сейчас: «пришли/скинь мне в
    телеграм …». Только когда об этом просят. Не cronjob — cron доставляет
    через минуты и в служебной обёртке. text — ровно то, что просили, без
    лишних пояснений (номер, ссылка, список)."""
    await telegram.send(text)
    return "Отправил в Telegram."


@mcp.tool()
async def announce(text: str) -> str:
    """Сказать фразу вслух прямо сейчас (с сигналом, музыка на это время
    встаёт на паузу). Экран свёрнут — пуш, подписки нет — Telegram."""
    return await speaker.announce(text, devices.current_device() or devices.ASUS)


# Hermes ждёт инструмент до 300 с; дольше — доделываем в фоне и пишем в Telegram.
TRANSCRIBE_WAIT_S = 240
TRANSCRIBE_MAX_CHARS = 60_000


@mcp.tool()
async def transcribe(source: str, kind: Kind = "podcast") -> str:
    """Расшифровать аудио/видео в текст — чтобы пересказать, ответить «о чём
    там», найти место («на какой минуте про X»). Фразы: «перескажи это видео
    <ссылка>», «о чём этот подкаст», «о чём был последний выпуск <подкаст>»,
    «перескажи главу», «что было в голосовом».
    source:
      - ссылка (YouTube, SoundCloud и почти любой сайт с видео/аудио);
      - "current" — то, что сейчас играет у хозяина;
      - путь к файлу на этом сервере (например, аудио из Telegram);
      - иначе — поисковый запрос, kind уточняет: podcast или audiobook.
    Возвращает текст с метками [мм:сс]. Час звука — 2–4 минуты; если дольше,
    ответит, что пришлёт в Telegram, — тогда позже вызвать снова с тем же
    source: готовое отдаётся мгновенно. Пересказывай сам, текст целиком не
    зачитывай."""
    task = transcriber.start(source, kind)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), TRANSCRIBE_WAIT_S)
    except TimeoutError:
        task.add_done_callback(_notify_transcribed)
        return ("Расшифровка ещё идёт (длинная запись). Скажи хозяину, что пришлю в Telegram, "
                "когда будет готово; потом вызови transcribe с тем же source.")
    except Exception as exc:  # источник не нашёлся/не скачался — Hermes скажет словами
        logger.warning("transcribe %r: %s", source, exc)
        return f"Не получилось: {exc}."
    if not result.text:
        return f"«{result.title}»: речи не нашлось (музыка или тишина)."
    text = result.text
    if len(text) > TRANSCRIBE_MAX_CHARS:
        text = text[:TRANSCRIBE_MAX_CHARS] + f"\n… (обрезано; весь текст — в файле {result.path})"
    return f"«{result.title}» — расшифровка:\n{text}"


def _notify_transcribed(task: asyncio.Task[transcriber.Transcript]) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        message = f"Расшифровка не удалась: {exc}"
    else:
        message = f"Расшифровка «{task.result().title}» готова — спроси меня о ней."
    notify = asyncio.ensure_future(telegram.send(message))
    notify.add_done_callback(lambda t: t.exception() and logger.warning("Telegram: %s", t.exception()))
