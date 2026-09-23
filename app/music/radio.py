"""Интернет-радио через Radio Browser API (radio-browser.info): бесплатно,
без ключа, десятки тысяч станций, включая русские. С asus доступен напрямую.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.music.models import Track

# API просит осмысленный User-Agent.
_HEADERS = {"User-Agent": "Jarvis/1.0 (home voice assistant)"}


async def search(name: str | None = None, genre: str | None = None, country_code: str | None = None) -> list[Track]:
    params: dict[str, str | int] = {
        "hidebroken": "true", "order": "clickcount", "reverse": "true", "limit": 10,
    }
    if name:
        params["name"] = name
    if genre:
        params["tag"] = genre.lower()
    if country_code:
        params["countrycode"] = country_code.upper()

    async with httpx.AsyncClient(base_url=settings.radio_browser_url, headers=_HEADERS, timeout=10) as client:
        resp = await client.get("/json/stations/search", params=params)
        resp.raise_for_status()
        stations = resp.json()

    return [
        # Названия в базе бывают с табами и лишними пробелами.
        Track(title=" ".join(s["name"].split()), source="radio", ref=s.get("url_resolved") or s["url"])
        for s in stations
        if s.get("url_resolved") or s.get("url")
    ]
