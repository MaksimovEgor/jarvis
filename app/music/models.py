from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Source = Literal["youtube", "radio", "local"]


@dataclass(frozen=True)
class Track:
    title: str
    source: Source
    # youtube — id видео, radio — URL потока, local — путь к файлу.
    ref: str
    duration: float | None = None
