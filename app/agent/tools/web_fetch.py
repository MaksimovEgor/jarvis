from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Скачивает страницу по URL и возвращает её текст — использовать "
            "после web_search, чтобы прочитать конкретную найденную страницу."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Ссылка на страницу"},
            },
            "required": ["url"],
        },
    },
}

_MAX_CHARS = 4000


async def run(arguments: dict) -> str:
    url = arguments["url"]
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:_MAX_CHARS]
