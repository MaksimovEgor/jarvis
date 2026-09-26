"""Погода — Open-Meteo (бесплатно, без ключа): сейчас и на три дня.

Через web_search/web_fetch погода не получалась: сайты погоды рисуют цифры
джаваскриптом, агент открывал страницу за страницей и сдавался через 40 с.
Здесь — один запрос, меньше секунды.

Open-Meteo с российских IP отвечает 403 — ходим через тот же прокси VPS,
что и YouTube (settings.youtube_proxy).
"""

from __future__ import annotations

from typing import Any

import aiohttp
from aiohttp_socks import ProxyConnector

from app.config import settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "weather",
        "description": (
            "Погода: сейчас и прогноз на сегодня, завтра, послезавтра. Для любых "
            "вопросов о погоде, температуре, дожде, зонте, во что одеться. "
            "Без города — дом хозяина."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Город, если спросили не про дом"},
            },
        },
    },
}

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Коды погоды WMO → по-русски (https://open-meteo.com/en/docs, «WMO Weather interpretation codes»).
_WMO = {
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь и туман",
    51: "слабая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "ливень", 81: "ливни", 82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с сильным градом",
}
_DAYS = ("сегодня", "завтра", "послезавтра")


async def _get(session: aiohttp.ClientSession, url: str, params: dict[str, Any]) -> dict[str, Any]:
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


async def run(arguments: dict) -> str:
    city = (arguments.get("city") or "").strip() or settings.home_city
    connector = ProxyConnector.from_url(settings.youtube_proxy, rdns=True) if settings.youtube_proxy else None
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=10)) as session:
        found = (await _get(session, _GEO_URL, {"name": city, "count": 1, "language": "ru"})).get("results")
        if not found:
            return f"Не знаю город «{city}»."
        place = found[0]
        data = await _get(session, _FORECAST_URL, {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code",
            "wind_speed_unit": "ms",
            "timezone": "auto",
            "forecast_days": 3,
        })
    now, daily = data["current"], data["daily"]
    lines = [
        f"{place['name']}, сейчас: {now['temperature_2m']:.0f}°, ощущается как {now['apparent_temperature']:.0f}°, "
        f"{_WMO.get(now['weather_code'], 'без осадков')}, ветер {now['wind_speed_10m']:.0f} м/с."
    ]
    for i, day in enumerate(_DAYS[: len(daily["time"])]):
        rain = daily["precipitation_probability_max"][i]
        lines.append(
            f"{day}: от {daily['temperature_2m_min'][i]:.0f}° до {daily['temperature_2m_max'][i]:.0f}°, "
            f"{_WMO.get(daily['weather_code'][i], '')}" + (f", вероятность осадков {rain}%" if rain is not None else "") + "."
        )
    return "\n".join(lines)
