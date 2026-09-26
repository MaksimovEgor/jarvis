"""Плееры по устройствам и «чья сейчас реплика».

Музыка звучит там, где её попросили, как у Алисы: у каждого браузера
(телефон, Mac) свой плеер, device="web:<id>".

asus — только сервер: ничего не слушает и не воспроизводит. device="asus"
остался как «устройства нет» (старый listener, таймеры без экрана): туда
не играем, объявления уходят пушем/в Telegram (speaker.py).

Чья реплика — где звучать:

    свой агент ─► инструмент в той же asyncio-задаче, что и ход ─► ContextVar: точно
    Hermes ─► MCP отдельным HTTP-запросом, контекста хода нет ─► последний начатый
              из идущих ходов (раньше — одна глобальная переменная: закончился
              ход Mac — ход телефона играл «куда-то»)
    хода нет (Telegram, cron) ─► последний браузер, где был Джарвис
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.music.outputs import MpvOutput, WebOutput
from app.music.player import Player

ASUS = "asus"
DEVICE_RE = re.compile(r"^(asus|web:[A-Za-z0-9-]{8,64})$")

_players: dict[str, Player] = {}
# Устройства идущих ходов по порядку начала (одно устройство может быть дважды).
_turns: list[str] = []
# Устройство хода в текущей asyncio-задаче (свой агент, быстрый путь).
_turn_device: ContextVar[str | None] = ContextVar("turn_device", default=None)
# Последний браузер, где был Джарвис (реплика или открытая страница).
_last_web: str | None = None


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


def is_web(device: str | None) -> bool:
    return bool(device) and device != ASUS


def seen(device: str) -> None:
    """Браузер открыл страницу или что-то сказал — теперь он «последний»."""
    global _last_web
    if is_web(device):
        _last_web = device


@contextmanager
def turn(device: str) -> Iterator[None]:
    _turns.append(device)
    token = _turn_device.set(device)
    seen(device)
    try:
        yield
    finally:
        _turn_device.reset(token)
        _turns.remove(device)


def current_device() -> str | None:
    """Где звучать: браузер текущей реплики, иначе последний. None — после
    перезапуска ядра ещё ни один браузер не появлялся."""
    own = _turn_device.get()
    if is_web(own):
        return own
    for device in reversed(_turns):
        if is_web(device):
            return device
    return _last_web


def active_players() -> list[Player]:
    """Плееры, у которых что-то в очереди, — для «выключи везде»."""
    return [p for p in _players.values() if p.current is not None]


def current_player() -> Player | None:
    device = current_device()
    return player_for(device) if device else None
