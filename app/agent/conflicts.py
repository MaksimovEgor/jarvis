"""Какие выполняющиеся просьбы отменяет новая реплика.

Джарвис принимает команду в любой момент — даже пока думает над прошлой.
Дальше решаем, что делать со старыми:

    «стоп» / «отмена» / «передумал»        → отменить всё
    «включи Queen» … «нет, включи Кино»    → старое «включи» отменить
    «включи Queen» … «напомни через 30 мин» → оба выполняются параллельно

Сначала дешёвые правила, потом (если задан LLM_API_KEY) — короткий вызов
LLM: он понимает и «нет, другую», и исправления. Ошибка или таймаут LLM —
ничего не отменяем: лишняя параллельная задача лучше потерянной.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.config import settings
from app.services.llm import get_llm

logger = logging.getLogger("jarvis.conflicts")

_CANCEL_ALL = re.compile(
    # Без «отмени»: «отмени таймер» — самостоятельная команда, не отмена всего.
    r"^\W*(стоп|хватит|не надо|передумал\w*|забудь|замолчи|стой)\b", re.IGNORECASE
)
# Реплика целиком — только «стоп»: ядро обрабатывает само, без Hermes.
_JUST_STOP = re.compile(
    r"^\W*(стоп|хватит|отмена|отмени|замолчи|тихо|стой|не надо)\W*$", re.IGNORECASE
)
_MEDIA = re.compile(r"\b(включи|поставь|сыграй|играй|запусти|врубай|вруби)\b", re.IGNORECASE)
_LLM_TIMEOUT = 5.0

_PROMPT = """\
Ты диспетчер голосового ассистента. Пока выполняются прошлые просьбы \
пользователя, пришла новая реплика. Определи, какие из выполняющихся просьб \
новая отменяет или заменяет:
- явная отмена: «стоп», «не надо», «передумал», «отмени»;
- та же задача с другими параметрами: «включи Queen» → «нет, включи Кино»;
- исправление ошибки распознавания или уточнение той же просьбы.
Независимые просьбы (напоминание, вопрос на другую тему, таймер) не отменяются.
Ответь только JSON без пояснений: {"cancel": [номера]}."""


async def _ask_llm(new: str, running: list[str]) -> list[int]:
    listing = "\n".join(f"{i + 1}. «{text}»" for i, text in enumerate(running))
    message = await get_llm().chat([
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": f"Выполняются:\n{listing}\nНовая: «{new}»"},
    ])
    match = re.search(r"\{.*\}", message.get("content") or "", re.DOTALL)
    numbers = json.loads(match.group(0)).get("cancel", []) if match else []
    return [n - 1 for n in numbers if isinstance(n, int) and 1 <= n <= len(running)]


def is_just_stop(text: str) -> bool:
    return bool(_JUST_STOP.match(text))


async def to_cancel(new: str, running: list[str]) -> list[int]:
    """Индексы в running, которые новая реплика отменяет."""
    if not running:
        return []
    if _CANCEL_ALL.search(new):
        return list(range(len(running)))
    if settings.llm_api_key:
        try:
            return await asyncio.wait_for(_ask_llm(new, running), _LLM_TIMEOUT)
        except Exception as exc:
            logger.warning("LLM-диспетчер не ответил (%s) — правила", exc)
    if _MEDIA.search(new):
        return [i for i, text in enumerate(running) if _MEDIA.search(text)]
    return []
