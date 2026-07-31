"""A candidate saying they want to stop, as opposed to not wanting to answer.

    "I'd rather not answer that"        -> refuse the question, change topic
    "I don't want to continue"          -> they are leaving

**The first version of this failed live and the transcript is the reason it was
rewritten.** It matched whole utterances against a fixed list of phrases, so it
caught "I want to stop" and missed everything a person actually says:

    "I don't want to continue this interview. Just end this interview."
    "Just end in the interview."
    "In the interview, I don't want to continue the interview"
    "No, just end the interview."

Four clear requests to stop, none detected, and the interviewer answered the
third by asking a fresh question about learning new business domains.

Two things changed.

**Intent is looked for inside the utterance, not as the whole of it.** People
speak in compound sentences, apologise first, repeat themselves, and get
mangled by speech recognition. "Just end in the interview" is what the
transcript actually contained.

**Being asked twice is the insult.** An explicit instruction to end is acted on
at once. Only a softer, more ambiguous signal gets the courtesy of an offer, and
the same person saying it a second time is not asked a third time.

The safety property is no longer strict matching. It is that every pattern here
names the interview, the call, or the session. "I don't want to continue down
that line" is somebody carrying on talking, and the offer step is what absorbs
whatever slips through.
"""

import re
from enum import StrEnum

from bot.brain.refusal import _normalise as _base_normalise


class StopIntent(StrEnum):
    """How plainly they asked."""

    NONE = "none"
    #: "Just end this interview." An instruction. Act on it.
    EXPLICIT = "explicit"
    #: "I'm done." Might be about the interview, might be about a topic. Ask.
    SOFT = "soft"


def _normalise(text: str) -> str:
    return _base_normalise(text).replace("'", "")


#: Unmistakable: a verb meaning "finish" applied to the interview itself. Any of
#: these anywhere in the utterance is an instruction, not a hint.
#:
#: The gap between verb and object is bounded so this cannot span two unrelated
#: clauses, and deliberately loose enough to survive speech recognition: the
#: live transcript contained "just end in the interview", not "just end the
#: interview".
_END_VERB = r"(?:end|stop|finish|terminate|cancel|quit|close|abort)"
_THIS_THING = r"(?:interview|call|session|conversation|meeting)"

_EXPLICIT_PATTERNS = [
    rf"\b{_END_VERB}\b(?:\s+\w+){{0,2}}\s+\b{_THIS_THING}\b",
    rf"\bdont want to (?:continue|do|carry on with)\b(?:\s+\w+){{0,2}}\s+\b{_THIS_THING}\b",
    rf"\bdo not want to (?:continue|do|carry on with)\b(?:\s+\w+){{0,2}}\s+\b{_THIS_THING}\b",
    rf"\bwant to (?:{_END_VERB})\b(?:\s+\w+){{0,2}}\s+\b{_THIS_THING}\b",
    rf"\b{_THIS_THING}\b\s+(?:should|can|must)\s+(?:be\s+)?{_END_VERB}",
    r"\bi (?:am |m )?withdraw(?:ing)?\b",
    r"\bplease (?:just )?(?:end|stop) (?:it|this)\b",
    r"\bjust end it\b",
    r"\bhang up\b",
]

#: Might be leaving, might be about a topic or a project. Worth one question.
_SOFT_PHRASES = {
    "i want to stop", "i dont want to continue", "i do not want to continue",
    "id like to stop", "i would like to stop", "i want to stop here",
    "lets stop", "lets stop here", "can we stop", "can we stop here",
    "im done", "i am done", "im finished", "i quit", "i give up",
    "i need to go", "i have to go", "i need to leave", "i have to leave",
    "i dont want to do this", "i do not want to do this",
    "thats enough", "that is enough", "no more questions",
}

#: Said in reply to "would you like to stop?". Only consulted once the offer has
#: been made, so a bare "yes" can never end an interview on its own.
_CONFIRMATIONS = {
    "yes", "yes please", "yeah", "yep", "yes lets stop", "yes stop", "sure",
    "please", "correct", "thats right", "id like to stop", "i would like to stop",
    "lets stop", "stop", "end it", "please end it", "yes end it", "yes please end it",
    "im done", "i am done", "confirm", "yes im sure", "yes i am sure", "definitely",
}

#: Said by someone who would rather carry on after all.
_DECLINED_TO_STOP = {
    "no", "no thanks", "no thank you", "im fine", "i am fine", "lets continue",
    "lets carry on", "carry on", "continue", "keep going", "no lets continue",
    "no im fine", "no i am fine", "sorry lets continue", "i want to continue",
    "no its fine", "no it is fine", "lets keep going", "no lets keep going",
}

#: Openers people put in front of any of the above. Stripped before the
#: whole-utterance comparisons, never before the pattern search, which does not
#: care where in the sentence the intent appears.
_OPENERS = (
    "sorry", "im sorry", "i am sorry", "apologies", "my apologies", "sorry but",
    "um", "uh", "well", "so", "ok", "okay", "right", "yeah", "no", "actually",
    "look", "honestly", "please", "just", "erm", "hmm",
)


def _stripped(normalised: str) -> set[str]:
    """The utterance with leading fillers and apologies peeled off, one at a time."""
    forms = {normalised}
    current = normalised
    for _ in range(3):
        words = current.split()
        if not words:
            break
        for opener in sorted(_OPENERS, key=len, reverse=True):
            parts = opener.split()
            if words[: len(parts)] == parts:
                current = " ".join(words[len(parts):])
                forms.add(current)
                break
        else:
            break
    return {f for f in forms if f}


def classify(text: str) -> StopIntent:
    """How plainly they asked to stop.

    Explicit patterns are searched for anywhere in the utterance, because that
    is where they occur: "I don't want to continue this interview. Just end this
    interview." Soft phrases still have to be the whole of it, so "I want to
    stop doing manual QA, that's why I moved" stays an answer.
    """
    normalised = _normalise(text)
    if not normalised:
        return StopIntent.NONE

    for pattern in _EXPLICIT_PATTERNS:
        if re.search(pattern, normalised):
            return StopIntent.EXPLICIT

    if any(form in _SOFT_PHRASES for form in _stripped(normalised)):
        return StopIntent.SOFT

    return StopIntent.NONE


def wants_to_stop(text: str) -> bool:
    """True when the candidate asked to end the interview, plainly or softly."""
    return classify(text) is not StopIntent.NONE


def asked_explicitly(text: str) -> bool:
    """True when they instructed rather than hinted. Acted on without asking."""
    return classify(text) is StopIntent.EXPLICIT


def confirms_stopping(text: str) -> bool:
    """True when they accept the offer to stop. Only meaningful after it is made."""
    normalised = _normalise(text)
    if any(form in _CONFIRMATIONS for form in _stripped(normalised)):
        return True
    # Saying it again, in any form, is a yes. Being asked a third time is the
    # thing this whole module exists to stop happening.
    return wants_to_stop(text)


def declines_stopping(text: str) -> bool:
    """True when they would rather carry on."""
    normalised = _normalise(text)
    return any(form in _DECLINED_TO_STOP for form in _stripped(normalised))
