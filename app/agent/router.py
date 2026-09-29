"""Быстрые команды — без Hermes, как у Алисы: «включи Queen», «пауза»,
«громче», «что играет», «таймер на 5 минут».

Через Hermes такая команда шла 10–60 секунд: он полноценный агент — ищет,
читает скиллы, а главное, ходы одного разговора выполняет по очереди, и
«включи песню» ждал, пока он доделает прошлую просьбу. Здесь — правила и
сразу плеер: доли секунды плюс поиск на YouTube.

Не подошло под правила — None, реплика уходит агенту. Быстрый путь ничего
не нашёл — так и отвечаем сразу («Нигде ничего не нашлось…»): агент искал
бы то же самое ещё минуту.
Правила нарочно узкие: лучше отдать агенту, чем включить не то.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

from app.agent.tools import weather
from app.config import settings
from app.music import devices, wave_spec, yandex
from app.music.models import Kind
from app.music.wave import spec_mood
from app.timers import Alarm, timers


logger = logging.getLogger("jarvis.router")


@dataclass
class FastReply:
    text: str
    tool: str


# Режим «мозга» (app/services/llm.py): локальная модель на GPU или облако.
_LLM_LOCAL = re.compile(r"^(?:переключись|перейди|включи)\s+(?:на\s+)?(?:локальн\w*|офлайн\w*|оффлайн\w*)(?:\s+(?:модель|мозг|режим|нейросеть))?$")
_LLM_CLOUD = re.compile(r"^(?:переключись|перейди|вернись)\s+(?:на\s+|в\s+)?(?:облак\w*|облачн\w*|основн\w*|обычн\w*)(?:\s+(?:модель|мозг|режим|нейросеть))?$")

_PLAY_VERB = r"(?:включи|поставь|сыграй|играй|запусти|вруби)"
_POLITE = r"(?:\s+(?:мне|пожалуйста|плиз))*"

_PAUSE = re.compile(r"^(?:поставь\s+)?(?:на\s+)?паузу$|^пауза$|^(?:останови|выключи)\s+(?:музыку|песню|трек)$")
_RESUME = re.compile(r"^(?:продолжи|продолжай|возобнови|сними\s+с\s+паузы|играй\s+дальше)(?:\s+(?:музыку|играть|воспроизведение))?$")
_NEXT = re.compile(r"^(?:следующ\w*(?:\s+(?:трек|песн\w*|станци\w*))?|дальше|переключи|пропусти|другую(?:\s+песню)?)$")
_PREV = re.compile(r"^(?:предыдущ\w*(?:\s+(?:трек|песн\w*|станци\w*))?|верни\s+(?:прошлую|предыдущую)(?:\s+песню)?)$")
_LOUDER = re.compile(r"^(?:сделай\s+)?(?:по)?громче$")
_QUIETER = re.compile(r"^(?:сделай\s+)?(?:по)?тише$")
_VOLUME = re.compile(r"^(?:сделай\s+|поставь\s+)?громкость\s+(?:на\s+)?(\d{1,3})(?:\s*процент\w*)?$")
_NOW = re.compile(r"^(?:что\s+(?:сейчас\s+|это\s+)*играет|что\s+за\s+(?:песня|трек)|как\s+называется\s+(?:эта\s+)?(?:песня|трек))$")
_LIKE = re.compile(r"^(?:(?:поставь\s+)?лайк|лайкни(?:\s+(?:это|эту|ее|его|трек|песню))?|мне\s+(?:это\s+)?нравится|нравится|класс(?:ная\s+песня)?|сохрани\s+(?:эту\s+)?(?:песню|трек))$")
_DISLIKE = re.compile(
    r"^(?:(?:поставь\s+)?дизлайк|мне\s+(?:это\s+)?не\s+нравится|не\s+нравится|"
    r"(?:больше\s+)?не\s+(?:включай|ставь)(?:\s+больше)?(?:\s+(?:это|эту\s+песню|этот\s+трек))?)$"
)
# «Включи мою музыку / лайки / любимое» — лайки перемешанные.
_LIKED = re.compile(
    rf"^{_PLAY_VERB}{_POLITE}\s+(?:мою\s+музыку|мои\s+лайки|лайки|лайкнут\w*(?:\s+(?:песни|треки))?|"
    r"(?:мо[июе]\s+)?любим\w*(?:\s+(?:музыку|песни|треки))?|избранн\w*)$"
)
_LYRICS = re.compile(
    r"^(?:покажи|открой|выведи)\s+(?:мне\s+)?(?:текст|слова)(?:\s+(?:песни|трека))?$|"
    r"^(?:что|о\s+чем)\s+(?:он|она|они)\s+(?:поет|поют)$|^какие\s+(?:там\s+)?слова$"
)
_RESUME_BOOK = re.compile(r"^(?:продолжи|продолжай|давай\s+дальше)\s+(?:слушать\s+)?(?:аудио)?книгу$")
# Справочное без LLM: время, дата, погода «сейчас», свои таймеры. Ловим смысл,
# а не одну формулировку: короткая реплика = вежливое начало + ключ + хвост.
# Лишние слова («какое время лучше для пробежки») — уже не справка, агенту.
_ASK = r"^(?:(?:скажи|подскажи|скажите|подскажите|а|ну|слушай|так)\s+)*"
_TAIL = r"(?:\s+(?:пожалуйста|подскажи|скажи))?$"
_TIME = re.compile(
    _ASK + r"(?:(?:сколько|который|какое)\s+(?:сейчас\s+)?(?:час|часов|время|времени)|время|сколько\s+на\s+часах)"
    r"(?:\s+сейчас)?" + _TAIL
)
_DATE = re.compile(
    _ASK + r"(?:сегодня\s+)?(?:(?:какое|какой|какая)\s+(?:сегодня\s+)?(?:число|день(?:\s+недели)?|дата)"
    r"|что\s+(?:сегодня\s+)?за\s+день)(?:\s+(?:сегодня|и\s+день\s+недели))?" + _TAIL
)
# Только дом и «сейчас/сегодня»: город, «завтра», «на выходные» — агенту;
# «нужен ли зонт», «холодно ли» — совет, а не цифры, тоже агенту.
_WEATHER_NOW = re.compile(
    _ASK + r"(?:(?:какая\s+)?(?:сейчас\s+|сегодня\s+)?погода|что\s+с\s+погодой|что\s+(?:там\s+)?на\s+улице|"
    r"(?:сколько\s+градусов|какая\s+(?:сейчас\s+)?температура))"
    r"(?:\s+(?:сейчас|сегодня|на\s+улице|за\s+окном))?" + _TAIL
)
_TIMERS = re.compile(
    _ASK + r"(?:(?:какие|мои|есть\s+ли)\s+(?:у\s+меня\s+)?(?:есть\s+|стоят\s+)?(?:таймеры|напоминания|будильники)"
    r"(?:\s+(?:стоят|есть|поставлены))?|что\s+(?:у\s+меня\s+)?по\s+(?:таймерам|напоминаниям|будильникам)|"
    r"(?:таймеры|напоминания)(?:\s+есть)?)" + _TAIL
)

_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_WEEKDAYS_ACC = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
_MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
               "сентября", "октября", "ноября", "декабря")
# «27» speakable прочёл бы «двадцать семь»; дате нужно порядковое.
_ORDINALS = (
    "первое", "второе", "третье", "четвёртое", "пятое", "шестое", "седьмое", "восьмое", "девятое", "десятое",
    "одиннадцатое", "двенадцатое", "тринадцатое", "четырнадцатое", "пятнадцатое", "шестнадцатое",
    "семнадцатое", "восемнадцатое", "девятнадцатое", "двадцатое",
)
_COUNTS = ("", "Один", "Два", "Три", "Четыре")
TIMER_TEXT = "Время вышло, таймер сработал."


def _ordinal(day: int) -> str:
    if day <= 20:
        return _ORDINALS[day - 1]
    if day == 30:
        return "тридцатое"
    return f"{'двадцать' if day < 30 else 'тридцать'} {_ORDINALS[day % 10 - 1]}"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n)
    if 10 <= n % 100 < 20:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def say_time(now: datetime) -> str:
    return f"Сейчас {now:%H:%M}."


def say_date(now: datetime) -> str:
    return f"Сегодня {_WEEKDAYS[now.weekday()]}, {_ordinal(now.day)} {_MONTHS_GEN[now.month - 1]}."


def _degrees(match: re.Match[str]) -> str:
    n = int(match.group(1))
    return f"{n} {_plural(n, ('градус', 'градуса', 'градусов'))}"


async def weather_now() -> str:
    """Первая строка прогноза — «сейчас»: «Москва, сейчас: 12°, ощущается как 10°, …»."""
    first = (await weather.run({})).splitlines()[0]
    return re.sub(r"(-?\d+)°", _degrees, first)


def _when(due: float, now: float) -> str:
    at, today = datetime.fromtimestamp(due), datetime.fromtimestamp(now)
    days = (at.date() - today.date()).days
    if days == 0:
        return f"в {at:%H:%M}"
    if days == 1:
        return f"завтра в {at:%H:%M}"
    if days < 7:
        return f"в {_WEEKDAYS_ACC[at.weekday()]} в {at:%H:%M}"
    return f"{at.day} {_MONTHS_GEN[at.month - 1]} в {at:%H:%M}"


def say_timers(alarms: list[Alarm], now: float) -> str:
    upcoming = sorted((a for a in alarms if a.due > now), key=lambda a: a.due)
    if not upcoming:
        return "Таймеров и напоминаний нет."
    n = len(upcoming)
    head = _COUNTS[n] if n < len(_COUNTS) else f"Всего {n}"
    items = "; ".join(
        f"{_when(a.due, now)} — {'таймер' if a.text == TIMER_TEXT else a.text.rstrip('.')}" for a in upcoming
    )
    return f"{head}: {items}."


_TIMER = re.compile(r"^(?:поставь\s+|заведи\s+|засеки\s+)?таймер\s+на\s+(.+)$")
_RADIO = re.compile(rf"^{_PLAY_VERB}{_POLITE}\s+радио(?:станцию)?(?:\s+(.+))?$")
_PLAY = re.compile(rf"^{_PLAY_VERB}{_POLITE}\s+(.+?)(?:\s+(?:на|с|из)\s+(?:ют[ую]бе?|ют[ую]ба|youtube))?$")

# «включи свет», «поставь будильник» — не музыка, пусть решает Hermes.
_NOT_MEDIA = re.compile(r"^(?:свет|лампу|телевизор|будильник|таймер|напоминани\w*|кондиционер|чайник|отопление)\b")
# Несколько просьб в одной фразе («включи Кино и сделай погромче, а потом
# таймер») — не название песни: целиком в Hermes, он разберёт по частям.
_COMPOUND = re.compile(
    r"\b(?:а\s+потом|потом|затем|и\s+(?:сделай|поставь|включи|выключи|напомни|скажи|найди|запусти))\b"
)
# «последний/новый выпуск» — нужен свежий ролик канала, это умеет Hermes.
_NEEDS_AGENT = re.compile(r"\b(?:последн\w*|нов\w*|свеж\w*|сегодняшн\w*|вчерашн\w*)\b")

# Сказка — сразу аудиосказка, без агента: он искал минуту и зачитывал
# варианты. «Включи сказку про автобус», «сказку про автобус найди»,
# «найди и включи аудиосказку колобок». «Расскажи сказку» — не сюда (агент).
_TALE = re.compile(
    r"^(?:(?:найди|поищи|включи|поставь|запусти|давай|нам|мне|пожалуйста|и|детск\w*|какую-нибудь|аудио)\s+)*"
    r"(?:аудио)?сказк\w*(?:\s+(?:(?:про|о|об)\s+)?(.+?))?(?:\s+(?:найди|включи|поставь|пожалуйста))?$"
)
DEFAULT_TALE = "аудиосказки для детей"

_BOOK = re.compile(r"\b(?:аудио)?книг[ауи]?\b")
_PODCAST = re.compile(r"\b(?:подкаст\w*|выпуск\w*|лекци\w*|интервью|новост\w*)\b")

_GENRES = {
    "джаз": "jazz", "рок": "rock", "поп": "pop", "классик": "classical", "классическ": "classical",
    "новост": "news", "шансон": "chanson", "электрон": "electronic", "лаунж": "lounge",
    "релакс": "chillout", "чилаут": "chillout", "блюз": "blues", "металл": "metal", "хип-хоп": "hip-hop",
}

_NUMBERS = {
    "одну": 1, "один": 1, "одна": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пятнадцать": 15,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "сорок пять": 45, "пятьдесят": 50,
}
_UNITS = {"сек": 1, "мин": 60, "час": 3600}


def _normalize(text: str) -> str:
    """«И включи, пожалуйста, радио Европа Плюс.» → «включи пожалуйста радио европа плюс»."""
    text = " ".join(re.sub(r"[^\w\s-]", " ", text.lower().replace("ё", "е")).split())
    text = re.sub(r"^(?:джарвис|jarvis)\s*", "", text)
    return re.sub(r"^(?:(?:и|а|ну|слушай|пожалуйста)\s+)+", "", text)


def _duration(spec: str) -> float | None:
    """«5 минут», «пять минут», «полчаса», «полторы минуты», «минуту», «1 час 30 минут»."""
    spec = spec.strip()
    if spec.startswith("полчаса"):
        return 1800
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:[.,]\d+)?|полтор\w*|[а-я]+(?:\s+[а-я]+)?)?\s*(сек|мин|час)\w*", spec):
        amount = amount.strip()
        if not amount:
            value = 1.0
        elif amount[0].isdigit():
            value = float(amount.replace(",", "."))
        elif amount.startswith("полтор"):
            value = 1.5
        elif amount in _NUMBERS:
            value = float(_NUMBERS[amount])
        elif amount.split()[-1] in _NUMBERS:
            value = float(_NUMBERS[amount.split()[-1]])
        else:
            return None
        total += value * _UNITS[unit]
    return total or None


async def _stations() -> dict[str, str]:
    """Каталог станций Яндекса (жанры, эпохи…) — только когда Яндекс подключён:
    без него «включи рок» остаётся поиском, как раньше."""
    if not yandex.is_on():
        return {}
    try:
        return await yandex.stations()
    except Exception:
        logger.warning("Нет каталога станций Яндекса", exc_info=True)
        return {}


def _clock(seconds: float) -> str:
    return time.strftime("%H:%M", time.localtime(time.time() + seconds))


# Что умеет только Hermes («второй мозг»): память и данные хозяина,
# напоминания, Telegram, долгие задачи. Остальное — быстрый встроенный агент
# без лишнего контекста.
_HERMES = re.compile(
    r"\b(?:гермес\w*|hermes|"
    r"напомн\w*|напоминани\w*|запомни\w*|помнишь|вспомни\w*|забудь|"
    r"памят\w*|заметк\w*|запис(?:ь|и|ей|ках?)|почт\w*|письм\w*|календар\w*|встреч\w*|"
    r"телеграм\w*|telegram|пришли|отправь|перешли|скинь|"
    r"мо(?:и|ей|ем|ём|их|я|е|ю)\s+(?:данн\w*|файл\w*|документ\w*|задач\w*|дел\w*|покупк\w*|машин\w*)|"
    r"кажд\w+\s+(?:день|утро|вечер|час|неделю|понедельник\w*)|по\s+расписанию|"
    r"расшифруй|перескажи\s+(?:видео|подкаст|запись)|"
    r"разберись|покопайся|исследуй|подробн\w*\s+(?:разбор|отчет|отчёт))",
    re.IGNORECASE,
)


def needs_hermes(text: str) -> bool:
    """Реплике нужен Hermes (память, напоминания, Telegram, долгое), а не
    быстрый агент."""
    return bool(_HERMES.search(_normalize(text)))


def is_player_command(text: str) -> bool:
    """Команда плееру («включи X», пауза, громче…) — для каналов без устройства
    (Telegram /local): сказать «не знаю, где включить», а не отдавать модели."""
    t = _normalize(text)
    if any(r.match(t) for r in (_PAUSE, _RESUME, _NEXT, _PREV, _LOUDER, _QUIETER, _VOLUME, _RADIO)):
        return True
    m = _PLAY.match(t)
    return bool(m) and not _NOT_MEDIA.match(m.group(1))


async def try_fast(text: str, device: str) -> FastReply | None:
    try:
        return await _try_fast(text, device)
    except Exception:
        # Что-то пошло не так быстрым путём — пусть разбирается Hermes, а не 500.
        logger.exception("Быстрый путь упал на «%s»", text)
        return None


async def _try_fast(text: str, device: str) -> FastReply | None:
    t = _normalize(text)
    # До проверки устройства: режим общий, и «включи локальную…» — не музыка.
    if _LLM_LOCAL.match(t):
        settings.llm_mode = "local"
        return FastReply("Перешёл на локальную модель: отвечаю сам на видеокарте, без облака. Поиск в интернете работает, но отвечаю проще.", "llm_mode")
    if _LLM_CLOUD.match(t):
        settings.llm_mode = "auto"
        return FastReply("Вернулся на облачную модель.", "llm_mode")
    # Справочное — и на asus: ни плеер, ни экран не нужны.
    if _TIME.match(t):
        return FastReply(say_time(datetime.now()), "time")
    if _DATE.match(t):
        return FastReply(say_date(datetime.now()), "date")
    if _WEATHER_NOW.match(t):
        return FastReply(await weather_now(), "weather")
    if _TIMERS.match(t):
        return FastReply(say_timers(timers.list(), time.time()), "list_timers")
    if not devices.is_web(device):
        return None  # asus — сервер, играть там нечему; пусть ответит Hermes
    player = devices.player_for(device)
    if (m := _TALE.match(t)) and not _COMPOUND.search(m.group(1) or ""):
        query = f"сказка {m.group(1)}" if m.group(1) else DEFAULT_TALE
        reply = await player.play_query(query, "audiobook")
        return FastReply(reply, "play_music")
    if _COMPOUND.search(t):
        return None

    if _PAUSE.match(t):
        return FastReply(await player.pause(), "pause")
    if _RESUME_BOOK.match(t):
        return FastReply(await player.resume_listening(None), "resume_listening")
    if _RESUME.match(t):
        return FastReply(await player.resume(), "resume")
    if _NEXT.match(t):
        return FastReply(await player.next(), "next")
    if _PREV.match(t):
        return FastReply(await player.previous(), "previous")
    if _LOUDER.match(t):
        return FastReply(await player.volume(delta=15), "volume")
    if _QUIETER.match(t):
        return FastReply(await player.volume(delta=-15), "volume")
    if m := _VOLUME.match(t):
        return FastReply(await player.volume(level=int(m.group(1))), "volume")
    if _LYRICS.match(t):
        if player.current is None:
            return FastReply("Сейчас ничего не играет.", "show_lyrics")
        devices.web_output(device).show("lyrics")
        return FastReply("Показываю текст.", "show_lyrics")
    if _LIKE.match(t):
        return FastReply(await player.rate(1), "rate_track")
    if _DISLIKE.match(t):
        return FastReply(await player.rate(-1), "rate_track")
    if _NOW.match(t):
        return FastReply(await player.now_playing(), "now_playing")

    if m := _TIMER.match(t):
        seconds = _duration(m.group(1))
        if seconds is None:
            return None
        timers.add(time.time() + seconds, TIMER_TEXT, device)
        return FastReply(f"Поставил таймер, сработает в {_clock(seconds)}.", "set_timer")

    if m := _RADIO.match(t):
        rest = (m.group(1) or "").strip()
        genre = next((g for ru, g in _GENRES.items() if rest.startswith(ru)), None)
        if not rest:
            reply = await player.play_radio(None, "pop", "RU")
        elif genre:
            reply = await player.play_radio(None, genre, None)
        else:
            reply = await player.play_radio(rest, None, None)
        return FastReply(reply, "play_radio")

    if _LIKED.match(t):
        return FastReply(await player.play_liked(), "play_liked")

    if m := _PLAY.match(t):
        query = m.group(1).strip()
        if (spec := wave_spec.parse(query, await _stations())) is not None:
            return FastReply(await player.play_wave(spec_mood(spec), spec=spec), "play_wave")
        if _NOT_MEDIA.match(query) or _NEEDS_AGENT.search(query) or len(query) < 2:
            return None
        kind: Kind = "audiobook" if _BOOK.search(query) else "podcast" if _PODCAST.search(query) else "music"
        if kind == "audiobook":
            query = _BOOK.sub(" ", query).strip() or query
        reply = await player.play_query(query, kind)
        return FastReply(reply, "play_music")

    return None
