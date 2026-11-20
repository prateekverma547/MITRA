"""Words with every number written as its value, for quote verification (A-52).

The speech recogniser writes the same number in different forms, sometimes
within one speaker ("thirty-eight percent", "38%", "2.4 million", "1.5 lakh").
A quote that writes it differently from the raw turn is the same words, so
`value_tokens` turns every number into one token holding its value:

    "thirty-eight percent"          -> ["38", "percent"]
    "38%"                           -> ["38", "percent"]
    "two point four million"        -> ["2400000"]
    "2.4 million", "2,400,000"      -> ["2400000"]
    "50k", "fifty thousand"         -> ["50000"]
    "1.5 lakh", "one lakh fifty thousand", "1,50,000" -> ["150000"]
    "three crore"                   -> ["30000000"]

Everything that is not a number is a lower-case word without punctuation or
apostrophes ("can't" -> "cant"), so a negation is always its own word.

Two numbers said one after another stay two numbers ("twenty nineteen" is
[20, 19], never 39): a value is only built from a form that has one reading.
Different values never compare equal ("thirty" is 30, "13" is 13).
"""

from __future__ import annotations

import re
from decimal import Decimal

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_SCALES = {"thousand": 10**3, "lakh": 10**5, "lakhs": 10**5, "lac": 10**5, "lacs": 10**5, "million": 10**6,
           "crore": 10**7, "crores": 10**7, "billion": 10**9}

#: Words that make a statement negative. A quote and the words it is matched
#: against must hold the same ones in the same order (score._same_negations).
NEGATIONS = frozenset({
    "not", "no", "never", "nothing", "none", "nobody", "neither", "nor", "cannot", "cant", "couldnt", "didnt",
    "doesnt", "dont", "wont", "wouldnt", "isnt", "wasnt", "arent", "werent", "hasnt", "havent", "hadnt",
    "shouldnt", "mustnt", "neednt", "aint", "nt"})


def _tokens(text: str) -> list[str]:
    text = (text or "").lower().replace("’", "'").replace("'", "")
    text = text.replace("%", " percent ")
    text = re.sub(r"\bper\s+cent\b", "percent", text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)          # 2,400,000 and 1,50,000
    text = re.sub(r"[-/]", " ", text)
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text)


def _digit_value(token: str) -> Decimal | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(k?)", token)
    if not m:
        return None
    value = Decimal(m.group(1))
    return value * 1000 if m.group(2) else value


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def value_tokens(text: str) -> list[str]:
    """The words of `text`, every spoken or written number replaced by its value."""
    words = _tokens(text)
    out: list[str] = []
    total = current = Decimal(0)
    active = False
    last = ""          # kind of the last number word: digit, unit, tens, hundred, scale, decimal

    def flush():
        nonlocal total, current, active, last
        if active:
            out.append(_fmt(total + current))
        total = current = Decimal(0)
        active, last = False, ""

    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        digit = _digit_value(w)
        if digit is not None:
            if active and last not in ("hundred", "scale"):
                flush()
            current += digit
            active, last = True, "digit"
        elif w in _UNITS:
            if active and (last in ("unit", "digit", "decimal") or (last == "tens" and _UNITS[w] >= 10)):
                flush()
            current += _UNITS[w]
            active, last = True, "unit"
        elif w in _TENS:
            if active and last in ("unit", "tens", "digit", "decimal"):
                flush()
            current += _TENS[w]
            active, last = True, "tens"
        elif w == "a" and nxt in ({"hundred"} | set(_SCALES)) and not active:
            current, active, last = Decimal(1), True, "unit"
        elif w == "hundred" and (active and last in ("unit", "tens", "digit")):
            current = current * 100
            last = "hundred"
        elif w in _SCALES and active and last in ("unit", "tens", "digit", "hundred", "decimal"):
            total += current * _SCALES[w]
            current = Decimal(0)
            last = "scale"
        elif w == "point" and active and last in ("unit", "tens", "digit") and (
                (nxt in _UNITS and _UNITS[nxt] < 10) or re.fullmatch(r"\d+", nxt or "")):
            digits, j = "", i + 1
            while j < len(words) and ((words[j] in _UNITS and _UNITS[words[j]] < 10) or re.fullmatch(r"\d+", words[j])):
                digits += str(_UNITS[words[j]]) if words[j] in _UNITS else words[j]
                j += 1
            current += Decimal("0." + digits)
            last = "decimal"
            i = j
            continue
        elif w == "and" and active and last in ("hundred", "scale") and (
                nxt in _UNITS or nxt in _TENS or _digit_value(nxt) is not None):
            pass  # "two hundred and fifty", "one lakh and twenty thousand"
        else:
            flush()
            out.append(w)
        i += 1
    flush()
    return out
