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

from app.music import devices
from app.music.models import Kind
from app.timers import timers


logger = logging.getLogger("jarvis.router")

# Ответы плеера «ничего не нашёл» — тогда пусть попробует Hermes.
_NOT_FOUND = ("Не нашёл", "На YouTube ничего")


@dataclass
class FastReply:
    text: str
    tool: str


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
_RESUME_BOOK = re.compile(r"^(?:продолжи|продолжай|давай\s+дальше)\s+(?:слушать\s+)?(?:аудио)?книгу$")
_TIMER = re.compile(r"^(?:поставь\s+|заведи\s+|засеки\s+)?таймер\s+на\s+(.+)$")
_RADIO = re.compile(rf"^{_PLAY_VERB}{_POLITE}\s+радио(?:станцию)?(?:\s+(.+))?$")
_PLAY = re.compile(rf"^{_PLAY_VERB}{_POLITE}\s+(.+?)(?:\s+(?:на|с|из)\s+(?:ют[ую]бе?|ют[ую]ба|youtube))?$")

# «включи свет», «поставь будильник» — не музыка, пусть решает Hermes.
_NOT_MEDIA = re.compile(r"^(?:свет|лампу|телевизор|будильник|таймер|напоминани\w*|кондиционер|чайник|отопление)\b")
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


def _clock(seconds: float) -> str:
    return time.strftime("%H:%M", time.localtime(time.time() + seconds))


async def try_fast(text: str, device: str) -> FastReply | None:
    try:
        return await _try_fast(text, device)
    except Exception:
        # Что-то пошло не так быстрым путём — пусть разбирается Hermes, а не 500.
        logger.exception("Быстрый путь упал на «%s»", text)
        return None


async def _try_fast(text: str, device: str) -> FastReply | None:
    t = _normalize(text)
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

    if m := _PLAY.match(t):
        query = m.group(1).strip()
        if _NOT_MEDIA.match(query) or _NEEDS_AGENT.search(query) or len(query) < 2:
            return None
        kind: Kind = "audiobook" if _BOOK.search(query) else "podcast" if _PODCAST.search(query) else "music"
        if kind == "audiobook":
            query = _BOOK.sub(" ", query).strip() or query
        # «Включи музыку» без уточнений — пусть Hermes подберёт по вкусу хозяина.
        if query in ("музыку", "что-нибудь", "что нибудь"):
            return None
        reply = await player.play_youtube(query, kind)
        return None if reply.startswith(_NOT_FOUND) else FastReply(reply, "play_music")

    return None
