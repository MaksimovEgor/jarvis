"""Текст → то, что Vosk-TTS может произнести.

Модель знает только кириллицу и несколько знаков (! ' ( ) , - . : ; ?).
На всём остальном она падает с KeyError, и кусок уходил на запасной Piper —
посреди ответа менялся голос. Падала на цифрах («громкость 20»), латинице
(«Foo Fighters»), кавычках «».

    числа        → прописью: «20» → «двадцать», «11:21» → «одиннадцать двадцать один»
    латиница     → транслитерация по звучанию: «Queen» → «квин»
    символы      → словами: % → процентов, + → плюс, & → и, № → номер
    прочее       → убирается
"""

from __future__ import annotations

import re

_ALLOWED = re.compile(r"[^а-яё!'(),\-.:;?\s]", re.IGNORECASE)

# --- числа -----------------------------------------------------------------

_UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_F = ["", "одна", "две"] + _UNITS[3:]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят",
         "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот",
             "восемьсот", "девятьсот"]
# (формы для 1, 2–4, 5+; женский род)
_SCALES = [
    (("тысяча", "тысячи", "тысяч"), True),
    (("миллион", "миллиона", "миллионов"), False),
    (("миллиард", "миллиарда", "миллиардов"), False),
]


def _triple(n: int, feminine: bool) -> list[str]:
    units = _UNITS_F if feminine else _UNITS
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest < 20:
        words.append(_TEENS[rest - 10])
    else:
        words += [_TENS[rest // 10], units[rest % 10]]
    return [w for w in words if w]


def _form(n: int, forms: tuple[str, str, str]) -> str:
    if 10 <= n % 100 < 20:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def number_words(n: int) -> str:
    if n == 0:
        return "ноль"
    if n >= 10**12:
        return " ".join(number_words(int(d)) for d in str(n))  # номера, id — по цифрам
    words: list[str] = []
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            continue
        if i == 0:
            words += _triple(g, False)
        else:
            forms, feminine = _SCALES[i - 1]
            words += _triple(g, feminine) + [_form(g, forms)]
    return " ".join(words)


def _fraction(digits: str) -> str:
    """«07» → «ноль семь»: ведущие нули иначе теряются."""
    zeros = len(digits) - len(digits.lstrip("0"))
    rest = digits.lstrip("0")
    return " ".join(["ноль"] * zeros + ([number_words(int(rest))] if rest else []))


def _numbers(text: str) -> str:
    text = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}\b)", "", text)  # 1 000 001 → 1000001
    text = re.sub(r"(\d+)[.,](\d+)", lambda m: f"{number_words(int(m[1]))} и {_fraction(m[2])}", text)
    return re.sub(r"\d+", lambda m: f" {number_words(int(m[0]))} ", text)


# --- латиница --------------------------------------------------------------

# Сначала сочетания, потом буквы — порядок важен.
_LATIN = [
    ("tion", "шн"), ("ght", "т"), ("sch", "ш"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"),
    ("wh", "в"), ("ck", "к"), ("qu", "кв"), ("kn", "н"), ("oo", "у"), ("ee", "и"), ("ea", "и"),
    ("ou", "ау"), ("ai", "эй"), ("ay", "эй"), ("ey", "эй"), ("oy", "ой"), ("ow", "оу"),
    ("ce", "се"), ("ci", "си"), ("cy", "си"), ("ge", "дже"), ("gi", "джи"),
    ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "х"),
    ("i", "и"), ("j", "дж"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"),
    ("q", "к"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("w", "в"), ("x", "кс"),
    ("y", "и"), ("z", "з"),
]
_LATIN_RE = re.compile("|".join(src for src, _ in _LATIN))
_LATIN_MAP = dict(_LATIN)


def _latin_word(word: str) -> str:
    word = word.lower()
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]  # немая e на конце: «Queen» не трогаем, «Dance» → «данс»
    return _LATIN_RE.sub(lambda m: _LATIN_MAP[m[0]], word)


# --- сборка ----------------------------------------------------------------

_SYMBOLS = {
    "%": " процентов ", "+": " плюс ", "&": " и ", "№": " номер ", "°": " градусов ",
    "=": " равно ", "@": " собака ", "/": " ", "\\": " ", "|": " ", "#": " ", "*": " ",
}


def speakable(text: str) -> str:
    text = re.sub(r"(\d{1,2}):(\d{2})\b", r"\1 \2", text)  # время 11:21
    text = _numbers(text)
    for symbol, word in _SYMBOLS.items():
        text = text.replace(symbol, word)
    text = re.sub(r"\s[—–]\s|[—–]", ", ", text)
    text = re.sub(r"[A-Za-z]+", lambda m: _latin_word(m[0]), text)
    text = _ALLOWED.sub(" ", text)
    text = re.sub(r"\s+([,.!?:;])", r"\1", text)
    return " ".join(text.split())
