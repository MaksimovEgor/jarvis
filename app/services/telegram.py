"""Сообщение хозяину в Telegram — сразу, от бота Hermes.

У Hermes на платформе API нет send_message (намеренно), и «пришли мне в
Telegram» он делал одноразовой cron-задачей: сообщение приходило через
минуты и в обёртке «Cronjob Response». Здесь — прямой sendMessage.

Токен бота и чат берутся из ~/.hermes/.env в момент отправки (в Jarvis они
не копируются). Путь: прокси point (TELEGRAM_PROXY), запасной — напрямую на
живой IP Telegram (провайдер режет DNS-адрес), как в ~/tg-notify.sh.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger("jarvis.telegram")

_API = "https://api.telegram.org"
_DIRECT_IP = "149.154.167.220"


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path(settings.hermes_env_file).expanduser().read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("'\"")
    return values


async def send(text: str) -> None:
    env = _env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_HOME_CHANNEL")
    if not token or not chat:
        raise RuntimeError("в ~/.hermes/.env нет TELEGRAM_BOT_TOKEN/TELEGRAM_HOME_CHANNEL")
    payload = {"chat_id": chat, "text": text}
    attempts: list[tuple[str | None, str, dict[str, str], dict[str, str]]] = [
        (env.get("TELEGRAM_PROXY") or None, f"{_API}/bot{token}/sendMessage", {}, {}),
        # Напрямую на IP, но Host и SNI — имя: сертификат проверяется как
        # для api.telegram.org.
        (None, f"https://{_DIRECT_IP}/bot{token}/sendMessage",
         {"Host": "api.telegram.org"}, {"sni_hostname": "api.telegram.org"}),
    ]
    last_error = ""
    for proxy, url, headers, extensions in attempts:
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=15, trust_env=False) as client:
                resp = await client.post(url, json=payload, headers=headers, extensions=extensions)
            if resp.status_code == 200 and resp.json().get("ok"):
                return
            last_error = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        logger.warning("Telegram: попытка не прошла (%s)", last_error)
    raise RuntimeError(f"Telegram не принял сообщение: {last_error}")
