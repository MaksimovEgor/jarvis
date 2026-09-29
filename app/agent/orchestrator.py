"""Агентный цикл: LLM решает, отвечать текстом или звать инструмент.

history — список сообщений сессии (без system-промпта, он подставляется
каждый раз свежим). Инструментальные сообщения внутри одного вызова живут
только в локальном messages и в history не попадают — иначе история быстро
раздувалась бы результатами web_fetch.

Локальная модель (Qwen2.5-3B IQ3 на GTX 1050) инструмент выбирает через раз
(поиск — ~50%), поэтому за неё решает код:

    облако  ─► модель сама зовёт web_search / web_fetch / music (цикл ниже)
    локально ─► «включи / отправь / напомни…» ─► честный отказ готовой фразой
             ├► «погода / курс / кто / почему / найди…» ─► web_search ─► модель пересказывает
             └► остальное (творческое, арифметика) ─► модель отвечает сама
    (музыку, паузу, громкость до агента перехватывает router — без LLM)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Callable

import httpx

from app import metrics
from app.agent.prompts import LOCAL_PROMPT, SYSTEM_PROMPT, now_line
from app.agent.tools import TOOLS, call_tool
from app.config import settings
from app.services.llm import get_llm

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4
DONE = "Готово."


# Когда локальной модели нужен поиск. Своим знаниям 3B-модели верить нельзя
# («Мастера и Маргариту» написал Бунин) — любой вопрос о фактах идёт через
# поиск; сама отвечает только на творческое и на арифметику.
_NEEDS_SEARCH = re.compile(
    r"\b(?:погод\w*|прогноз\w*|температур\w*|курс\w*|доллар\w*|евро|биткоин\w*|новост\w*|нового|"
    r"матч\w*|турнир\w*|чемпионат\w*|расписани\w*|цен[аыу]\w*|стоимост\w*|"
    r"найди|найти|поищи|загугли|узнай|сегодня|вчера|завтра|сейчас|последн\w*|свеж\w*)"
    r"|^(?:(?:джарвис|скажи|а|и)[,\s]+)*(?:кто|когда|где|куда|откуда|сколько|почему|зачем|отчего|"
    r"как(?:ой|ая|ое|ие|ов|ова|ую)|что\s+(?:такое|значит|было|случилось|произошло)|чем|чей|чья)\b",
    re.IGNORECASE,
)
_WEATHER = re.compile(r"\bпогод\w*|\bзонт\w*|\bдожд\w*", re.IGNORECASE)
# Посчитать модель может сама — поиск тут только мешает.
_ARITHMETIC = re.compile(r"\d.*(?:умнож|раздел|плюс|минус|[-+*/×÷]).*\d|сколько будет", re.IGNORECASE)
# Просьба что-то сделать. Модель на такое отвечает «сделал» или повторяет просьбу,
# поэтому отказ — готовой фразой. Музыку до сюда перехватывает router.
_ACTION = re.compile(
    r"^(?:(?:джарвис|пожалуйста)[,\s]+)*(?:включи|выключи|поставь|заведи|отправь|пришли|перешли|скинь|"
    r"позвони|напомни|запиши|удали|открой|закрой|запусти|останови|отмени|сохрани|закажи|купи|"
    r"напиши\s+(?:сообщение|письмо|маме|папе|ему|ей))\b",
    re.IGNORECASE,
)
LOCAL_CANT_ACT = (
    "На локальной модели я только отвечаю и ищу в интернете — сделать это не могу. "
    "Музыку включу, если Джарвис открыт на телефоне; остальное — когда вернёмся на облако."
)
_FILLER = re.compile(r"^(?:джарвис|jarvis)[,\s]*|^(?:найди|поищи|загугли|узнай)(?:\s+мне)?\s+", re.IGNORECASE)


# Конец фразы: знак препинания (и закрывающая кавычка/скобка), за которым пробел.
# «3.5» и «т.к.» без пробела не режутся.
_SENTENCE_END = re.compile(r"[.!?…]+[»\")]*\s+")


class SentenceSplitter:
    """Поток текста от LLM → целые фразы, как только они готовы."""

    def __init__(self, on_sentence: Callable[[str], None]) -> None:
        self._on_sentence = on_sentence
        self._buffer = ""

    def feed(self, delta: str) -> None:
        self._buffer += delta
        while m := _SENTENCE_END.search(self._buffer):
            sentence, self._buffer = self._buffer[: m.end()].strip(), self._buffer[m.end():]
            if sentence:
                self._on_sentence(sentence)

    def flush(self) -> None:
        """Хвост без точки — в конце шага LLM."""
        sentence, self._buffer = self._buffer.strip(), ""
        if sentence:
            self._on_sentence(sentence)


class CloudUnavailable(RuntimeError):
    """Облачная LLM не ответила, а ничего ещё не сделано — ход можно отдать другому."""


async def run_agent(
    history: list[dict], user_text: str, local: bool = False, fallback_local: bool = True,
    on_sentence: Callable[[str], None] | None = None,
) -> tuple[str, list[str]]:
    """local — сразу локальная модель на GPU asus, мимо облака (Telegram /local).
    fallback_local=False — облако не ответило: CloudUnavailable вместо локальной
    модели (ядро тогда отдаёт реплику Hermes).
    on_sentence — облако отвечает потоком, каждая готовая фраза (в том числе
    «Сейчас проверю.» перед поиском) уходит сюда сразу; локальная — без потока."""
    if local or settings.llm_mode == "local":
        return await _run_local(history, user_text)
    try:
        return await _run_cloud(history, user_text, on_sentence)
    except (httpx.HTTPError, RuntimeError) as exc:
        if not fallback_local:
            raise CloudUnavailable(str(exc)) from exc
        logger.warning("Облачная LLM не ответила (%s) — отвечает локальная", exc)
        return await _run_local(history, user_text)


async def _run_local(history: list[dict], user_text: str) -> tuple[str, list[str]]:
    if _ACTION.search(user_text.strip()):
        _remember(history, user_text, LOCAL_CANT_ACT)
        return LOCAL_CANT_ACT, []
    prompt, used = user_text, []
    if _WEATHER.search(user_text):
        found = await call_tool("weather", {})
        used.append("weather")
        prompt = f"{user_text}\n\nПогода:\n{found}\n\nОтветь по этим данным, коротко."
    elif _NEEDS_SEARCH.search(user_text.strip()) and not _ARITHMETIC.search(user_text):
        found = await call_tool("web_search", {"query": _FILLER.sub("", user_text).strip() or user_text})
        used.append("web_search")
        prompt = (
            f"{user_text}\n\nРезультаты поиска в интернете:\n{found}\n\n"
            "Ответь на вопрос по этим результатам, коротко. Если в них ответа нет — так и скажи."
        )
    message = await get_llm().local([{"role": "system", "content": LOCAL_PROMPT + now_line(datetime.now())}, *history, {"role": "user", "content": prompt}])
    reply = message.get("content") or ""
    _remember(history, user_text, reply)
    return reply, used


def _remember(history: list[dict], user_text: str, reply: str) -> None:
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    del history[: max(0, len(history) - settings.session_history_limit)]


async def _run_cloud(
    history: list[dict], user_text: str, on_sentence: Callable[[str], None] | None = None,
) -> tuple[str, list[str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT + now_line(datetime.now())}, *history, {"role": "user", "content": user_text}]
    llm = get_llm()
    used_tools: list[str] = []
    splitter = None
    if on_sentence is not None:
        def sentence(text: str) -> None:
            metrics.mark("llm_first")
            on_sentence(text)
        splitter = SentenceSplitter(sentence)

    for step in range(MAX_TOOL_ITERATIONS):
        # Последний шаг — без инструментов: ответить по тому, что уже нашлось,
        # а не «не получилось разобраться» после минуты поиска.
        last = step == MAX_TOOL_ITERATIONS - 1
        try:
            if splitter is None:
                message = await llm.cloud(messages, None if last else TOOLS)
            else:
                message = await llm.cloud_stream(messages, None if last else TOOLS, splitter.feed)
                splitter.flush()
        except (httpx.HTTPError, RuntimeError):
            if not used_tools:
                raise
            # Инструмент уже сработал (музыка включилась) — повторять ход
            # другим агентом нельзя, иначе действие выполнится дважды.
            logger.warning("LLM упала после %s — считаю сделанным", used_tools)
            _remember(history, user_text, DONE)
            return DONE, used_tools
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            reply = message.get("content") or ""
            _remember(history, user_text, reply)
            return reply, used_tools
        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            used_tools.append(name)
            result = await call_tool(name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })

    return "Не получилось разобраться за разумное число шагов, переформулируй запрос.", used_tools
