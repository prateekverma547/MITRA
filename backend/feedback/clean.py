"""The cleaned transcript (task 25): written after the call, beside the raw one.

The raw transcript is never changed (DEV_PLAN, "Transcripts: raw and cleaned").
After the call a text model (job "feedback": OPENAI_FEEDBACK_MODEL, whose slot
covers the cleaned transcript, bot/config.py) cleans the CANDIDATE's turns:
filler and false starts removed, punctuation added, clear transcription mistakes
fixed from context. Mitra's turns are the model's own text of what it spoke and
are copied unchanged.

Every cleaned turn passes guards in code, and a turn that fails any of them is
replaced by its raw text and marked, so the cleaned transcript can be less clean
but never wrong:
  - one cleaned turn per raw turn, same index (and the raw item id and timings);
  - every number in the raw turn kept (as digits or words), and no new number;
  - every named or technical term kept, unless the model declared it a
    correction (from -> to) or marked it uncertain;
  - no word that is not in the raw turn, except the "to" side of a declared
    correction;
  - an edit-size limit: at most MAX_REMOVED_SHARE of the words removed (and
    MIN_REMOVED_ALLOWED always allowed), at most MAX_CORRECTIONS corrections.

Stored in interviews.cleaned_transcript as a CleanedTranscript (C-A6, round 6).
Interviews cleaned before that have it in session_metrics["cleaned_transcript"];
`stored_cleaned_transcript` reads either. If cleaning fails, the status is
"missing" and the raw stands.
"""

from __future__ import annotations

import re

from loguru import logger
from pydantic import BaseModel, Field

MAX_REMOVED_SHARE = 0.4
MIN_REMOVED_ALLOWED = 5
MAX_CORRECTIONS = 3
#: Turns per model call: bounds each reply, keeps alignment simple.
CHUNK = 40

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20",
    "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
    "hundred": "100", "thousand": "1000", "million": "1000000", "billion": "1000000000", "percent": "%",
}


class Correction(BaseModel):
    from_text: str = Field(description="The raw words, exactly as they appear in the raw turn.")
    to_text: str = Field(description="What they should be, from context.")


class CleanTurn(BaseModel):
    index: int
    text: str
    corrections: list[Correction]
    uncertain: list[str] = Field(description="Raw words you could not be sure of; left as they were.")


class CleanReply(BaseModel):
    turns: list[CleanTurn]


SYSTEM = """You tidy a job interview transcript that a speech recogniser wrote down.
For EVERY turn given, return one cleaned turn with the same index. Rules:
- Remove filler words (um, uh, you know, like, I mean when used as filler) and false starts / repeated
  words ("we we built" -> "we built").
- Add punctuation and capital letters.
- Fix a CLEAR mishearing only when the context makes the right word certain (for example a product or
  company name spelled correctly elsewhere). List every such fix in corrections (from_text exactly as in
  the raw turn, to_text the fix).
- Never add content: no new words except the to_text of a listed correction. Never paraphrase or reorder.
- Keep every number, name and technical term. If unsure about a word, leave it and list it in uncertain.
- Never change the meaning."""


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("’", "'").replace("'", ""))


def _numbers(text: str) -> set[str]:
    found = set(re.findall(r"\d+(?:\.\d+)?", text or ""))
    found |= {_NUMBER_WORDS[w] for w in _words(text) if w in _NUMBER_WORDS and _NUMBER_WORDS[w] != "%"}
    return found


#: Never a named term, even capitalised: fillers and the pronoun.
_NOT_TERMS = {"i", "im", "ive", "id", "um", "uh", "erm", "hmm", "mm", "ah", "oh", "okay", "ok", "yeah", "so", "well",
              "like"}


def _terms(text: str) -> set[str]:
    """Named or technical terms: capitalised words that do not start a sentence, acronyms,
    and words mixing letters with digits or inner capitals (gRPC, PostgreSQL, k8s).

    A token is a WHOLE alphanumeric token that contains a letter, so "14B", "8x7B",
    "128K" and "16GB" are the terms "14b", "8x7b", "128k" and "16gb": the same unit
    `_words` reads (B-70, round 9). The pattern used to start at a letter, so "14B"
    gave the term "b", which `_words` never produces, and every turn naming a model
    size failed the guard even when unchanged. Numbers inside such terms are still
    checked by the number guard ("14B" to "13B" drops 14 and adds 13)."""
    terms = set()
    sentence_start = True
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#'’-]*|[.?!]", text or ""):
        if token in ".?!":
            sentence_start = True
            continue
        bare = token.strip(".-'’")
        starts = sentence_start
        sentence_start = token.endswith((".", "?", "!"))
        if not any(c.isalpha() for c in bare):
            continue  # a plain number: the number guard's job
        if not bare or bare.lower().replace("'", "").replace("’", "") in _NOT_TERMS:
            continue
        inner_caps = any(c.isupper() for c in bare[1:])
        mixed = any(c.isdigit() for c in bare)
        if inner_caps or mixed or (bare[0].isupper() and not starts):
            terms.add(bare.lower())
    return terms


def _is_subsequence(needle: list[str], hay: list[str]) -> bool:
    it = iter(hay)
    return all(word in it for word in needle)


def _apply_corrections(raw_words: list[str], corrections: list[dict]) -> list[str]:
    """The raw words with each declared correction applied (from -> to), first match each."""
    words = list(raw_words)
    for k in corrections:
        src, dst = _words(k.get("from_text", "")), _words(k.get("to_text", ""))
        if not src:
            continue
        for i in range(len(words) - len(src) + 1):
            if words[i:i + len(src)] == src:
                words[i:i + len(src)] = dst
                break
    return words


def check_turn(raw: str, cleaned: CleanTurn | dict) -> list[str]:
    """The guards for one candidate turn. [] when it passes."""
    c = cleaned if isinstance(cleaned, dict) else cleaned.model_dump()
    text = c.get("text") or ""
    corrections = c.get("corrections") or []
    uncertain = {w for u in c.get("uncertain") or [] for w in _words(u)}
    problems = []
    if len(corrections) > MAX_CORRECTIONS:
        problems.append(f"too many corrections ({len(corrections)})")
    to_words = {w for k in corrections for w in _words(k.get("to_text", ""))}
    from_words = {w for k in corrections for w in _words(k.get("from_text", ""))}
    for k in corrections:
        if not set(_words(k.get("from_text", ""))) <= set(_words(raw)):
            problems.append(f"correction source not in the raw turn: {k.get('from_text')!r}")
    raw_numbers, new_numbers = _numbers(raw), _numbers(text)
    allowed_number_changes = _numbers(" ".join(k.get("from_text", "") for k in corrections))
    missing = raw_numbers - new_numbers - allowed_number_changes
    added = new_numbers - raw_numbers - _numbers(" ".join(k.get("to_text", "") for k in corrections))
    if missing:
        problems.append(f"number dropped: {sorted(missing)}")
    if added:
        problems.append(f"number added: {sorted(added)}")
    cleaned_words = set(_words(text))
    lost_terms = {t for t in _terms(raw)
                  if not set(_words(t)) <= cleaned_words and not set(_words(t)) <= (from_words | uncertain)}
    if lost_terms:
        problems.append(f"term dropped: {sorted(lost_terms)}")
    new_words = cleaned_words - set(_words(raw)) - to_words
    if new_words:
        problems.append(f"new words: {sorted(new_words)}")
    elif not _is_subsequence(_words(text), _apply_corrections(_words(raw), corrections)):
        # Cleaning only deletes and corrects: the cleaned words, in order, must come
        # from the raw words (after the declared corrections). An inserted "the" fails.
        problems.append("words added or reordered")
    raw_count = len(_words(raw))
    removed = raw_count - sum(1 for w in _words(text) if w in set(_words(raw)))
    if removed > max(MIN_REMOVED_ALLOWED, int(raw_count * MAX_REMOVED_SHARE)):
        problems.append(f"too much removed ({removed} of {raw_count} words)")
    return problems


def align(raw_turns: list[dict], reply_turns: list[dict]) -> list[str]:
    """The whole-reply guard: exactly one cleaned turn per raw candidate turn, same indexes."""
    want = [t["index"] for t in raw_turns]
    got = [t.get("index") for t in reply_turns]
    problems = []
    if sorted(got) != sorted(want):
        missing, extra = sorted(set(want) - set(got)), sorted(set(got) - set(want))
        problems.append(f"turns do not match the raw ones: missing {missing}, extra {extra}")
    if len(got) != len(set(got)):
        problems.append("a turn index appears twice")
    return problems


async def clean_transcript(transcript: dict, *, request=None, role_title: str = "") -> dict:
    """The cleaned transcript as a dict for session_metrics. Never raises."""
    from shared.text_models import request_structured

    request = request or request_structured
    turns = transcript.get("turns") or []
    candidate = [t for t in turns if t.get("speaker") == "candidate" and not t.get("pre_interview")]
    cleaned: dict[int, dict] = {}
    usd, guard_failures, problems_all = 0.0, 0, []
    try:
        for start in range(0, len(candidate), CHUNK):
            chunk = candidate[start:start + CHUNK]
            user = (f"Role being interviewed for: {role_title}\n\nTURNS (index: text)\n"
                    + "\n".join(f"{t['index']}: {t['text']}" for t in chunk))
            result = await request(job="feedback", response_model=CleanReply, temperature=0,
                                   messages=[{"role": "system", "content": SYSTEM},
                                             {"role": "user", "content": user}])
            cost = getattr(getattr(result, "record", None), "cost_usd", None)
            usd += cost or 0.0
            reply = result.payload.get("turns") or []
            problems = align(chunk, reply)
            if problems:
                raise ValueError("; ".join(problems))
            by_index = {r["index"]: r for r in reply}
            for raw in chunk:
                c = by_index[raw["index"]]
                found = check_turn(raw["text"], c)
                if found:
                    guard_failures += 1
                    problems_all.append({"index": raw["index"], "problems": found})
                    cleaned[raw["index"]] = {"text": raw["text"], "corrections": [], "uncertain": [],
                                             "guard": found}
                else:
                    cleaned[raw["index"]] = {"text": c["text"], "corrections": c.get("corrections") or [],
                                             "uncertain": c.get("uncertain") or [], "guard": "ok"}
    except Exception as exc:  # noqa: BLE001  (the raw transcript always stands)
        logger.error(f"cleaned transcript not written: {exc!r}")
        return {"status": "missing", "reason": repr(exc)[:300], "usd": round(usd, 6)}
    out_turns = []
    for t in turns:
        base = {k: t.get(k) for k in ("index", "item_id", "speaker", "audio_start_seconds", "audio_end_seconds",
                                      "at_seconds", "pre_interview")}
        if t["index"] in cleaned:
            out_turns.append({**base, **cleaned[t["index"]]})
        else:
            out_turns.append({**base, "text": t.get("text", ""), "corrections": [], "uncertain": [],
                              "guard": "copied"})
    return {"status": "ok", "model_job": "feedback", "turns": out_turns, "usd": round(usd, 6),
            "guard_failures": guard_failures, "guard_problems": problems_all,
            "candidate_turns": len(candidate)}


async def generate_cleaned_transcript(interview_id: str) -> None:
    """After the transcript is committed (bot/persistence.py, beside feedback scoring):
    clean it and store it in interviews.cleaned_transcript (C-A6). Never raises."""
    try:
        from app.db import Candidate, Interview, get_sessionmaker

        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None or not interview.transcript:
                return
            raw = dict(interview.transcript)
            role = ""
            candidate = await session.get(Candidate, interview.candidate_id)
            if candidate is not None and candidate.blueprint:
                role = (candidate.blueprint.get("evaluation_spec") or {}).get("role_title", "")
        result = await clean_transcript(raw, role_title=role)
        from shared.interview_contracts import CleanedTranscript

        stored = CleanedTranscript.model_validate(result).model_dump(mode="json")
        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            interview.cleaned_transcript = stored
            await session.commit()
        logger.info(f"[{interview_id}] cleaned transcript: {result['status']}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] cleaned transcript failed: {exc!r}")


def _is_name(text: str) -> bool:
    """A name or technical term as written: every word starts with a capital, or
    has an inner capital or a digit ("Meridian Pay", "Razorpay", "gRPC", "k8s").
    Not the pronoun "I" on its own, and not ordinary words ("led", "routing")."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#-]*", text or "")
    if not words or [w.lower() for w in words] == ["i"]:
        return False
    return all(w[0].isupper() or any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w) for w in words)


def declared_name_fixes(cleaned: dict | None) -> dict[int, list[tuple[list[str], list[str]]]]:
    """The cleaned transcript's declared NAME fixes, by raw turn index, for quote
    verification (feedback/score.py, rule "declared_fix", A-52).

    Only fixes that survived this module's guards (status ok, turn guard ok; a
    turn that failed keeps no corrections), only where the "to" side is a name
    (`_is_name`), and never one that changes a negation word. Words are compared
    as feedback.numbers.value_tokens, the same form verification uses.
    """
    from feedback.numbers import NEGATIONS, value_tokens

    out: dict[int, list[tuple[list[str], list[str]]]] = {}
    if not isinstance(cleaned, dict) or cleaned.get("status") != "ok":
        return out
    for turn in cleaned.get("turns") or []:
        if turn.get("guard") != "ok" or turn.get("speaker") != "candidate":
            continue
        for fix in turn.get("corrections") or []:
            src, dst = value_tokens(fix.get("from_text", "")), value_tokens(fix.get("to_text", ""))
            if not src or not dst or src == dst or not _is_name(fix.get("to_text", "")):
                continue
            if [w for w in src if w in NEGATIONS] != [w for w in dst if w in NEGATIONS]:
                continue
            out.setdefault(int(turn["index"]), []).append((src, dst))
    return out


def stored_cleaned_transcript(interview) -> dict | None:
    """The cleaned transcript of a stored interview: the C-A6 column, or, for an
    interview cleaned before it existed, session_metrics["cleaned_transcript"].
    Both are read; nothing is migrated (a stored record is never rewritten)."""
    column = getattr(interview, "cleaned_transcript", None)
    if column:
        return column
    return (getattr(interview, "session_metrics", None) or {}).get("cleaned_transcript")
