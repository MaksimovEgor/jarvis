from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Literal

Source = Literal["youtube", "radio", "local"]
# music — песни (очередь похожих, всегда с начала); audiobook/podcast —
# длинное, продолжается с места остановки.
Kind = Literal["music", "audiobook", "podcast"]

# Лайк / дизлайк.
Rating = Literal[1, -1]
# Время суток — рекомендации под него: утром бодрее, ночью тише.
Slot = Literal["morning", "day", "evening", "night"]
Mood = Literal["auto", "energetic", "calm", "focus", "sleep", "discover"]
# Откуда трек в очереди — для метрики волны и подписи в плеере.
Origin = Literal["wave", "liked", "query", "mix", "radio", "local"]
Outcome = Literal["finished", "skipped", "disliked", "error", "stalled", "stopped"]
# Откуда звук: лайки на диске, кэш на диске, из интернета (качается), поток (длинное).
FromWhere = Literal["liked", "cache", "net", "stream"]

SLOT_RU: dict[Slot, str] = {"morning": "утро", "day": "день", "evening": "вечер", "night": "ночь"}
MOOD_RU: dict[Mood, str] = {
    "auto": "", "energetic": "бодрое", "calm": "спокойное", "focus": "для работы",
    "sleep": "для сна", "discover": "новое",
}


# «(Official HD Video)», «[4K Upgrade]», «(Lyrics)» — в плеере и в речи это шум.
_TITLE_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(?:official|video|clip|lyric|audio|visuali[sz]er|hd|4k|remaster|клип|премьера)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    return _TITLE_NOISE.sub("", title).strip() or title


def slot_at(ts: float) -> Slot:
    """06–11 утро, 11–17 день, 17–23 вечер, 23–06 ночь (местное время)."""
    hour = time.localtime(ts).tm_hour
    if 6 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "day"
    if 17 <= hour < 23:
        return "evening"
    return "night"


def is_weekend(ts: float) -> bool:
    return time.localtime(ts).tm_wday >= 5

# Длиннее — считаем «длинным» даже при kind=music (концерт, сборник, выпуск новостей).
LONG_SECONDS = 20 * 60


@dataclass(frozen=True)
class Track:
    title: str
    source: Source
    # youtube — id видео, radio — URL потока, local — путь к файлу.
    ref: str
    duration: float | None = None
    kind: Kind = "music"
    # Исполнитель, если известен (YouTube: канал «X - Topic» или «X - Песня»).
    artist: str | None = None
    origin: Origin = "query"

    @property
    def key(self) -> str:
        return f"{self.source}:{self.ref}"

    @property
    def is_long(self) -> bool:
        if self.source == "radio":
            return False
        return self.kind != "music" or (self.duration or 0) > LONG_SECONDS
