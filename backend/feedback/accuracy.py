"""Transcript accuracy (task 26): word error rate against a known script.

Word error rate = (substitutions + deletions + insertions) / words in the script,
computed on normalised words, so it measures what was heard, not how it was
written: lower case, punctuation removed (a decimal point kept), "%" read as
"percent", hyphens as spaces, and spoken numbers turned into digits
("thirty eight" and "38" are the same word list). Both sides go through the
same normaliser.

Also: whether each named target term (a technical term, a number, a name, a
Hindi word) appears in the transcript, normalised the same way.
"""

from __future__ import annotations

import re

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}


def _numbers_to_digits(words: list[str]) -> list[str]:
    """Runs of spoken number words become one digit string ("two thousand nineteen" -> "2019")."""
    out, run = [], []

    def flush():
        if not run:
            return
        total, current = 0, 0
        for w in run:
            if w in _UNITS:
                current += _UNITS[w]
            elif w in _TENS:
                current += _TENS[w]
            elif w == "hundred":
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * _SCALES[w]
                current = 0
        out.append(str(total + current))
        run.clear()

    for w in words:
        if w in _UNITS or w in _TENS or w in _SCALES:
            run.append(w)
        else:
            flush()
            out.append(w)
    flush()
    return out


def _decimals_and_joins(words: list[str]) -> list[str]:
    """ "and" inside a spoken number is dropped ("two hundred and fifty" -> 250), and
    "<number> point <digits>" becomes one decimal ("ninety nine point nine five" -> 99.95)."""
    numeric = set(_UNITS) | set(_TENS) | set(_SCALES)
    words = [w for i, w in enumerate(words)
             if not (w == "and" and 0 < i < len(words) - 1 and words[i - 1] in numeric and words[i + 1] in numeric)]
    out, i = [], 0
    while i < len(words):
        if words[i] == "point" and out and out[-1].isdigit() and i + 1 < len(words):
            digits = []
            j = i + 1
            while j < len(words) and (words[j] in _UNITS and _UNITS[words[j]] < 10 or words[j].isdigit()):
                digits.append(str(_UNITS[words[j]]) if words[j] in _UNITS else words[j])
                j += 1
            if digits:
                out[-1] = out[-1] + "." + "".join(digits)
                i = j
                continue
        out.append(words[i])
        i += 1
    return out


def normalise(text: str) -> list[str]:
    text = (text or "").lower().replace("’", "'").replace("%", " percent ")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)          # 2,400 -> 2400
    text = re.sub(r"[-/]", " ", text)
    words = re.findall(r"\d+(?:\.\d+)?|[a-z0-9']+", text)
    words = [w.strip("'") for w in words if w.strip("'")]
    numeric = set(_UNITS) | set(_TENS) | set(_SCALES)
    words = [w for i, w in enumerate(words)
             if not (w == "and" and 0 < i < len(words) - 1 and words[i - 1] in numeric and words[i + 1] in numeric)]
    # Digits after "point" are read one by one ("point nine five" -> "point 95") before
    # number words are combined, or "nine five" would be added up to 14.
    spoken, i = [], 0
    while i < len(words):
        spoken.append(words[i])
        if words[i] == "point":
            digits = []
            j = i + 1
            while j < len(words) and words[j] in _UNITS and _UNITS[words[j]] < 10:
                digits.append(str(_UNITS[words[j]]))
                j += 1
            if digits:
                spoken.append("".join(digits))
                i = j
                continue
        i += 1
    return _decimals_and_joins(_numbers_to_digits(spoken))


def edit_counts(reference: list[str], hypothesis: list[str]) -> dict:
    """Substitutions, deletions and insertions of the cheapest alignment."""
    n, m = len(reference), len(hypothesis)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            same = reference[i - 1] == hypothesis[j - 1]
            cost[i][j] = min(cost[i - 1][j - 1] + (0 if same else 1), cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    s = d = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            s += reference[i - 1] != hypothesis[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            d, i = d + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return {"substitutions": s, "deletions": d, "insertions": ins, "reference_words": n}


def word_error_rate(reference: str, hypothesis: str) -> float:
    counts = edit_counts(normalise(reference), normalise(hypothesis))
    return (counts["substitutions"] + counts["deletions"] + counts["insertions"]) / max(1, counts["reference_words"])


def term_found(term: str, hypothesis: str) -> bool:
    """The target term's normalised words appear, in order and together, in the transcript."""
    t, h = normalise(term), normalise(hypothesis)
    return any(h[i:i + len(t)] == t for i in range(len(h) - len(t) + 1)) if t else False
