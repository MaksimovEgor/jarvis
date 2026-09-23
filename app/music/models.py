from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Source = Literal["youtube", "radio", "local"]
# music — песни (очередь похожих, всегда с начала); audiobook/podcast —
# длинное, продолжается с места остановки.
Kind = Literal["music", "audiobook", "podcast"]

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

    @property
    def key(self) -> str:
        return f"{self.source}:{self.ref}"

    @property
    def is_long(self) -> bool:
        if self.source == "radio":
            return False
        return self.kind != "music" or (self.duration or 0) > LONG_SECONDS
