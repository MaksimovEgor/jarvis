"""Быстрые команды — без Hermes, как у Алисы: «включи Queen», «пауза»,
«громче», «что играет», «таймер на 5 минут».

Через Hermes такая команда шла 10–60 секунд: он полноценный агент — ищет,
читает скиллы, а главное, ходы одного разговора выполняет по очереди, и
«включи песню» ждал, пока он доделает прошлую просьбу. Здесь — правила и
сразу плеер: доли секунды плюс поиск на YouTube.

Не подошло под правила (или быстрый путь ничего не нашёл) — None, реплика
уходит в Hermes как раньше.
Правила нарочно узкие: лучше отдать Hermes, чем включить не то.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from app.config import settings
from app.music import devices, wave_spec, yandex
from app.music.models import Kind
from app.music.wave import spec_mood
from app.timers import timers


logger = logging.getLogger("jarvis.router")

# Ответы плеера «ничего не нашёл» — тогда пусть попробует Hermes.
_NOT_FOUND = ("Не нашёл", "На YouTube ничего", "Нигде ничего")


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
    if not devices.is_web(device):
        return None  # asus — сервер, играть там нечему; пусть ответит Hermes
    if _COMPOUND.search(t):
        return None
    player = devices.player_for(device)

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
        timers.add(time.time() + seconds, "Время вышло, таймер сработал.", device)
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
        return None if reply.startswith(_NOT_FOUND) else FastReply(reply, "play_radio")

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
        return None if reply.startswith(_NOT_FOUND) else FastReply(reply, "play_music")

    return None
