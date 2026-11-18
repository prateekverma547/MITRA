"""Remove identity from a CV before a model scores it.

The scorer never sees the candidate's name, contact details, personal details
(date of birth, age, gender, marital status, nationality, religion, address,
family) or the names of the institutions they studied at. These are the
classic routes by which bias enters a screening score, and a screening tool is
judged on them.

This is deterministic and errs towards removing too much: a phrase wrongly
removed costs the model a little context, while a name left in costs the
screening its fairness. The removed kinds are recorded on the FitAnalysis
(`identity_fields_removed`) so the record says what the scorer could not see.

Employers stay in: what someone did, and where, is the evidence being scored.
"""

import re
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_URL = re.compile(r"(https?://\S+|www\.\S+|\b(?:linkedin|github|gitlab|behance|kaggle)\.com/\S*)", re.I)
# Ten or more digits, allowing a leading +, spaces, dots, dashes and brackets.
_PHONE = re.compile(r"(?<!\w)\+?\(?\d[\d\s().-]{8,}\d(?!\w)")
_PERSONAL_LINE = re.compile(
    r"^(\s*[-•*]?\s*)(date of birth|d\.?o\.?b\.?|age|gender|sex|marital status|nationality|religion|"
    r"caste|address|permanent address|father'?s name|mother'?s name|spouse)\s*[:\-–]\s*.*$",
    re.I | re.M,
)
_INSTITUTION_WORDS = r"(?:University|Institute|College|School|Academy|Polytechnic|Vidyalaya|Vidyapeeth)"
_INSTITUTION = re.compile(
    rf"(?:[A-Z][\w.&'’-]*\s+){{0,6}}{_INSTITUTION_WORDS}"
    rf"(?:\s+(?:of|for|and|&)(?:\s+(?:[A-Z][\w.&'’-]*|&|and|of))+)?",
)
# Short all-capitals names institutions are known by: IIT, NIT, BITS, UPES.
_INSTITUTION_ACRONYM = re.compile(r"\b(?:IIT|IIIT|NIT|BITS|IIM|IISc|UPES|VIT|SRM|DTU|NSUT|AIIMS)\b(?:\s+[A-Z][a-z]+)?")
_REPLACEMENTS = {
    "email": "[email]",
    "phone": "[phone]",
    "links": "[link]",
    "institution": "[institution]",
    "name": "[candidate]",
}


@dataclass
class Redacted:
    text: str
    removed: list[str] = field(default_factory=list)


def _name_pattern(name: str) -> re.Pattern | None:
    parts = [re.escape(p) for p in re.split(r"\s+", name.strip()) if len(p) > 1]
    if not parts:
        return None
    # The full name in any spacing, then each part alone (initials excepted).
    return re.compile(r"\b(" + r"\s+".join(parts) + r"|" + r"|".join(parts) + r")\b", re.I)


def _looks_like_a_name_line(line: str) -> bool:
    words = line.strip().split()
    return (
        1 < len(words) <= 4
        and all(w[:1].isupper() for w in words)
        and not any(ch.isdigit() for ch in line)
        and not any(w.strip(":").lower() in {"resume", "curriculum", "vitae", "cv", "profile"} for w in words)
    )


def redact(cv_text: str, *, candidate_name: str | None = None) -> Redacted:
    text = cv_text
    removed: list[str] = []

    def sub(kind: str, pattern: re.Pattern, replacement: str | None = None) -> None:
        nonlocal text
        new, count = pattern.subn(replacement if replacement is not None else _REPLACEMENTS[kind], text)
        if count:
            text = new
            if kind not in removed:
                removed.append(kind)

    # Contact details first: they often contain the name ("priya.sharma@..."),
    # and replacing the name inside them would break the pattern that finds them.
    sub("email", _EMAIL)
    sub("links", _URL)
    sub("phone", _PHONE)

    # Then the name: the one HR typed, then a name-shaped first line.
    if candidate_name and (pattern := _name_pattern(candidate_name)):
        sub("name", pattern)
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is not None and _looks_like_a_name_line(lines[first]) and "[candidate]" not in lines[first]:
        lines[first] = "[candidate]"
        text = "\n".join(lines)
        if "name" not in removed:
            removed.append("name")

    sub("personal details", _PERSONAL_LINE, r"\1\2: [removed]")
    sub("institution", _INSTITUTION)
    sub("institution", _INSTITUTION_ACRONYM)
    return Redacted(text=text, removed=removed)
