"""Spotting when a candidate declines to answer, or says nothing of substance.

Deliberately dumb and deterministic: no model call, no latency, no surprises.
This runs on the spoken-turn path, and a wrong guess here changes the interview,
so it only fires on things that are unambiguous.

The distinction that matters:

- **Refusal** — "no", "I'd rather not", "pass". The candidate is declining.
  Pushing the same question again is the wrong move, both practically and
  ethically.
- **Not substantive** — "um", "yeah", "right". Not a refusal, just not an answer
  yet. Normal in speech; the turn-taking layer already tolerates it.

Anything longer or more ambiguous is treated as a real answer. A candidate
saying "no, that's not how it went, what actually happened was..." is answering,
and must never be read as a refusal.
"""

import re

#: Phrases that, on their own, mean the candidate is declining. Matched against
#: the whole utterance, not searched within it — "no" alone is a refusal, but
#: "no, we killed that feature" is an answer.
_REFUSAL_PHRASES = {
    "no",
    "nope",
    "nah",
    "no thanks",
    "no thank you",
    "i don't want to",
    "i dont want to",
    "i do not want to",
    "i don't want to answer",
    "i don't want to answer that",
    "i'd rather not",
    "id rather not",
    "i would rather not",
    "i'd rather not say",
    "i'd rather not answer",
    "i prefer not to",
    "i'd prefer not to",
    "rather not",
    "not answering",
    "i'm not answering",
    "i am not answering",
    "no comment",
    "pass",
    "skip",
    "skip it",
    "skip this",
    "next question",
    "i don't want to talk about it",
    "i don't want to discuss this",
    "can we move on",
    "let's move on",
    "i want to stop",
    "i'd like to stop",
    "i want to end this",
    "i'd like to end this call",
    "i want to leave",
    "stop",
}

#: Fillers that carry no content. Not refusals — just not an answer yet.
_FILLERS = {
    "um",
    "uh",
    "erm",
    "hmm",
    "hm",
    "yeah",
    "yes",
    "yep",
    "ok",
    "okay",
    "right",
    "sure",
    "mhm",
    "i see",
    "got it",
    "well",
    "so",
}

#: Below this many words an utterance cannot carry a real answer.
MIN_SUBSTANTIVE_WORDS = 4


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and filler padding for exact matching."""
    cleaned = re.sub(r"[^\w\s']", " ", (text or "").lower())
    return " ".join(cleaned.split())


def looks_like_refusal(text: str) -> bool:
    """True only when the whole utterance is a refusal.

    Matching the entire utterance rather than searching inside it is the whole
    safety property: "no" is a refusal, "no, that's not what happened — what we
    actually did was..." is an answer, and confusing the two would have the bot
    abandon a topic the candidate was engaging with.
    """
    normalised = _normalise(text)
    if not normalised:
        return False
    if normalised in _REFUSAL_PHRASES:
        return True

    # Strip a leading filler: "um, no", "well, I'd rather not".
    words = normalised.split()
    if words and words[0] in _FILLERS:
        remainder = " ".join(words[1:])
        if remainder in _REFUSAL_PHRASES:
            return True
    return False


def is_substantive(text: str) -> bool:
    """True when an utterance could plausibly contain an actual answer."""
    normalised = _normalise(text)
    if not normalised:
        return False
    if looks_like_refusal(text):
        return False
    if normalised in _FILLERS:
        return False
    return len(normalised.split()) >= MIN_SUBSTANTIVE_WORDS
