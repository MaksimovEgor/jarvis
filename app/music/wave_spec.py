"""Фраза → настройки волны, как в «Моей волне» Яндекса.

    «включи что-нибудь грустное на русском»  → mood_energy=sad, language=russian
    «включи музыку для бега»                 → station=activity:run
    «поставь музыку 90-х»                    → station=epoch:nineties
    «включи русский рок»                     → station=genre:rusrock (каталог станций Яндекса)
    «включи Queen»                           → None: это не волна, а поиск

Чистая функция: каталог станций передаёт вызывающий (yandex.stations()).
"""

from __future__ import annotations

import re
from typing import TypeVar

from app.music.models import Diversity, Language, MoodEnergy, WaveSpec

# Фраза про волну, а не про конкретную песню.
_BARE = {"музыку", "волну", "мою волну", "что-нибудь", "что нибудь", "какую-нибудь музыку",
         "музыку какую-нибудь", "что-то", "что то"}
_MARKER = re.compile(r"\b(?:музык\w*|песн\w*|трек\w*|волн\w*|что[- ]нибудь|что[- ]то|какую[- ]нибудь)\b")
_LEADS = re.compile(r"^(?:для|под|на)\s")

_MOOD: tuple[tuple[MoodEnergy, str, re.Pattern[str]], ...] = (
    ("sad", "грустное", re.compile(r"грустн|печальн|меланхол|тоскл")),
    ("fun", "весёлое", re.compile(r"весел|радостн|позитивн")),
    ("active", "бодрое", re.compile(r"бодр|энергичн|драйв|зажигательн")),
    ("calm", "спокойное", re.compile(r"спокойн|расслаб|релакс|лиричн|медленн|тих\w*")),
)
_DIVERSITY: tuple[tuple[Diversity, str, re.Pattern[str]], ...] = (
    ("discover", "незнакомое", re.compile(r"\bнов|незнаком|свеж|неизвестн")),
    ("popular", "популярное", re.compile(r"популярн|\bхит")),
    ("favorite", "любимое", re.compile(r"любим")),
)
_LANGUAGE: tuple[tuple[Language, str, re.Pattern[str]], ...] = (
    ("without-words", "без слов", re.compile(r"без\s+слов|инструментал")),
    ("russian", "русское", re.compile(r"русск|на\s+русском|отечествен")),
    ("not-russian", "иностранное", re.compile(r"иностран|зарубеж|английск|на\s+английском")),
)
# Станции-занятия «Моей волны». Порядок важен: «для сна» раньше «для работы».
_ACTIVITY: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("fall-asleep", "засыпаю", re.compile(r"для\s+сна|перед\s+сном|на\s+ночь|засып|(?:у|за)снуть|колыбельн")),
    ("wake-up", "просыпаюсь", re.compile(r"просып|пробужд|для\s+утра|утренн")),
    ("run", "бег", re.compile(r"\bбег\w*|пробежк")),
    ("workout", "тренируюсь", re.compile(r"трениров|\bспорт|качалк|для\s+зала")),
    ("driving", "за рулём", re.compile(r"за\s+рул|в\s+машин|вожден")),
    ("road-trip", "в дороге", re.compile(r"в\s+дорог|путешеств|поездк")),
    ("study-background", "для концентрации", re.compile(r"учеб|учит|концентрац|сосредоточ")),
    ("work-background", "работаю", re.compile(r"для\s+работы|работаю|фонов|для\s+фона")),
    ("party", "вечеринка", re.compile(r"вечерин|тусов")),
    ("romantic-date", "свидание", re.compile(r"свидан|романтич")),
    ("beloved", "для влюблённых", re.compile(r"влюбл")),
    ("sex", "для секса", re.compile(r"для\s+секса")),
)
_EPOCH: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("the-greatest-hits", "вечные хиты", re.compile(r"вечн\w*\s+хит")),
    ("fifties", "50-е", re.compile(r"\b50\b|пятидесят")),
    ("sixties", "60-е", re.compile(r"\b60\b|шестидесят")),
    ("seventies", "70-е", re.compile(r"\b70\b|семидесят")),
    ("eighties", "80-е", re.compile(r"\b80\b|восьмидесят")),
    ("nineties", "90-е", re.compile(r"\b90\b|девяност")),
    ("zeroes", "2000-е", re.compile(r"\b2000\b|нулев|двухтысячн")),
    ("tenths", "2010-е", re.compile(r"\b2010\b|десятых")),
    ("twenties", "2020-е", re.compile(r"\b2020\b|двадцатых")),
)
_NOISE = re.compile(r"\b(?:мою|мне|музык\w*|песн\w*|трек\w*|волн\w*|что[- ]нибудь|что[- ]то|какую[- ]нибудь|эпохи|годов|х)\b")


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").replace("-х", " х").replace("-е", " е").split())


V = TypeVar("V", bound=str)


def _first(options: tuple[tuple[V, str, re.Pattern[str]], ...], text: str) -> tuple[V, str] | None:
    return next(((value, label) for value, label, pattern in options if pattern.search(text)), None)


def parse(query: str, stations: dict[str, str] | None = None) -> WaveSpec | None:
    """Настройки волны или None, если фраза — поиск конкретной музыки."""
    q = _norm(query)
    if q in _BARE:
        return WaveSpec()

    station, labels = None, []
    if activity := _first(_ACTIVITY, q):
        station = f"activity:{activity[0]}"
        labels.append(activity[1])
    elif epoch := _first(_EPOCH, q):
        station = f"epoch:{epoch[0]}"
        labels.append(epoch[1])
    mood = _first(_MOOD, q)
    diversity = _first(_DIVERSITY, q)
    language = _first(_LANGUAGE, q)
    labels += [x[1] for x in (mood, diversity, language) if x]

    # Жанр/настроение-станция Яндекса по названию: «русский рок», «мрачное».
    if station is None and stations:
        core = " ".join(_NOISE.sub(" ", q).split())
        names = {_norm(name): sid for sid, name in stations.items()}
        if core in names:
            station = names[core]
            labels = [stations[station].lower()]
            # «русский» — часть названия жанра, а не язык.
            mood, diversity, language = None, None, None

    dims = station is not None or mood or diversity or language
    if not dims or not (_MARKER.search(q) or _LEADS.match(q) or station):
        return None
    return WaveSpec(
        station=station or WaveSpec().station,
        mood_energy=mood[0] if mood else "all",
        diversity=diversity[0] if diversity else "default",
        language=language[0] if language else "any",
        label=" · ".join(labels),
    )
