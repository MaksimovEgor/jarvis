"""Справочное быстрым путём, без LLM: время, дата, погода «сейчас», таймеры."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.agent import prompts, router
from app.agent.tools import weather
from app.music import devices
from app.timers import Alarm

DEVICE = "web:router-test-01"
NOW = datetime(2026, 9, 27, 21, 47)


def test_time() -> None:
    assert router.say_time(NOW) == "Сейчас 21:47."


@pytest.mark.parametrize("day, expected", [
    (datetime(2026, 9, 27), "Сегодня воскресенье, двадцать седьмое сентября."),
    (datetime(2026, 1, 1), "Сегодня четверг, первое января."),
    (datetime(2026, 12, 30), "Сегодня среда, тридцатое декабря."),
    (datetime(2026, 3, 31), "Сегодня вторник, тридцать первое марта."),
])
def test_date(day: datetime, expected: str) -> None:
    assert router.say_date(day) == expected


@pytest.mark.parametrize("text, tool", [
    ("Который час?", "time"),
    ("Джарвис, сколько времени", "time"),
    ("сколько время", "time"),
    ("сколько сейчас время", "time"),
    ("а сколько времени", "time"),
    ("подскажи время", "time"),
    ("скажи который час", "time"),
    ("время сейчас", "time"),
    ("который час сейчас", "time"),
    ("сколько на часах", "time"),
    ("какое сегодня число", "date"),
    ("какое число", "date"),
    ("какой сегодня день недели", "date"),
    ("какое сегодня число и день недели", "date"),
    ("что сегодня за день", "date"),
    ("сегодня какое число", "date"),
    ("какая погода", "weather"),
    ("какая сегодня погода", "weather"),
    ("погода на улице", "weather"),
    ("что на улице", "weather"),
    ("сколько градусов за окном", "weather"),
    ("какие у меня таймеры", "list_timers"),
    ("какие напоминания", "list_timers"),
    ("какие таймеры стоят", "list_timers"),
    ("какие у меня будильники", "list_timers"),
    ("есть ли у меня напоминания", "list_timers"),
])
async def test_phrases_are_fast(monkeypatch: pytest.MonkeyPatch, text: str, tool: str) -> None:
    monkeypatch.setattr(weather, "run", AsyncMock(return_value="Москва, сейчас: 12°"))
    reply = await router.try_fast(text, DEVICE)
    assert reply is not None and reply.tool == tool


@pytest.mark.parametrize("text", [
    "погода завтра",
    "какая погода в Питере",
    "какое время лучше для пробежки",
    "сколько времени займёт дорога до дачи",
    "сколько часов в сутках на Марсе",
    "нужен ли зонт",
    "холодно ли на улице",
    "что у меня стоит на сегодня",
    "напомни в 9 про встречу",
    "какой день недели был 9 мая 1945 года",
])
async def test_not_quick_goes_to_agent(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(weather, "run", AsyncMock(return_value="Москва, сейчас: 12°"))
    assert await router.try_fast(text, DEVICE) is None


async def test_weather_now_first_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weather, "run", AsyncMock(return_value=(
        "Москва, сейчас: 21°, ощущается как -1°, ясно, ветер 3 м/с.\nсегодня: от 5° до 12°."
    )))
    assert await router.weather_now() == "Москва, сейчас: 21 градус, ощущается как -1 градус, ясно, ветер 3 м/с."


def test_timers_empty() -> None:
    assert router.say_timers([], NOW.timestamp()) == "Таймеров и напоминаний нет."


def test_timers_two() -> None:
    now = NOW.timestamp()
    alarms = [
        Alarm("b", datetime(2026, 9, 28, 9, 0).timestamp(), "Позвонить маме."),
        Alarm("a", datetime(2026, 9, 27, 22, 5).timestamp(), router.TIMER_TEXT),
        Alarm("old", now - 60, "уже было"),
    ]
    assert router.say_timers(alarms, now) == "Два: в 22:05 — таймер; завтра в 09:00 — Позвонить маме."


async def test_quick_commands_work_on_asus() -> None:
    reply = await router.try_fast("который час", devices.ASUS)
    assert reply is not None and reply.tool == "time"


def test_prompt_knows_now() -> None:
    line = prompts.now_line(datetime(2026, 9, 27, 21, 47))
    assert "воскресенье, 27 сентября 2026, 21:47" in line
