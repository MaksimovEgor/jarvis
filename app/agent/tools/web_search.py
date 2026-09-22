from __future__ import annotations

import httpx

from app.config import settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Поиск в интернете через self-host SearXNG. Возвращает заголовки, "
            "ссылки и краткие описания найденных страниц."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "num_results": {
                    "type": "integer",
                    "description": "Сколько результатов вернуть (по умолчанию 5)",
                },
            },
            "required": ["query"],
        },
    },
}


async def run(arguments: dict) -> str:
    query = arguments["query"]
    num_results = int(arguments.get("num_results") or 5)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            settings.searxng_url.rstrip("/") + "/search",
            params={"q": query, "format": "json"},
        )
        resp.raise_for_status()

    results = resp.json().get("results", [])[:num_results]
    if not results:
        return "Ничего не нашлось."

    lines = [
        f"- {r.get('title')} — {r.get('url')}\n  {(r.get('content') or '')[:200]}"
        for r in results
    ]
    return "\n".join(lines)
