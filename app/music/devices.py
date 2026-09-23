"""Плееры по устройствам и «чья сейчас реплика».

Музыка звучит там, где её попросили, как у Алисы:

    гарнитура asus (listener)  → device="asus"      → mpv на asus
    веб на телефоне / Mac      → device="web:<id>"  → <audio> в этом браузере
    Telegram, cron Hermes      → хода Jarvis нет    → последнее устройство
                                                      (30 мин), иначе asus

Hermes вызывает MCP-инструменты отдельным HTTP-запросом и не знает, с какого
устройства пришла реплика. Это знает ядро: на время хода (/chat/*) оно
запоминает устройство, и инструменты берут плеер через current_player().
Пользователь один, ходы не пересекаются — глобального «текущего» достаточно.

Почему без хода — последнее устройство, а не asus: Hermes обрабатывает
запросы по одному, и запрос, который Jarvis уже бросил по таймауту, он всё
равно выполнит позже — музыка тогда внезапно играла дома, хотя просили с
телефона.
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Iterator

from app.music.outputs import MpvOutput, WebOutput
from app.music.player import Player

ASUS = "asus"
DEVICE_RE = re.compile(r"^(asus|web:[A-Za-z0-9-]{8,64})$")
# Столько после последней реплики «текущим» остаётся её устройство.
LAST_DEVICE_TTL = 30 * 60

_players: dict[str, Player] = {}
_turn: str | None = None
_last: tuple[str, float] = (ASUS, 0.0)


def player_for(device: str) -> Player:
    player = _players.get(device)
    if player is None:
        output = MpvOutput() if device == ASUS else WebOutput(device)
        player = _players[device] = Player(device, output)
    return player


def web_output(device: str) -> WebOutput:
    output = player_for(device).output
    if not isinstance(output, WebOutput):
        raise ValueError(f"{device} — не веб-устройство")
    return output


@contextmanager
def turn(device: str) -> Iterator[None]:
    global _turn, _last
    _turn = device
    try:
        yield
    finally:
        _last = (device, time.time())
        if _turn == device:
            _turn = None


def current_device() -> str:
    if _turn is not None:
        return _turn
    device, at = _last
    return device if time.time() - at < LAST_DEVICE_TTL else ASUS


def active_players() -> list[Player]:
    """Плееры, у которых что-то в очереди, — для «выключи везде»."""
    return [p for p in _players.values() if p.current is not None]


def current_player() -> Player:
    return player_for(current_device())
