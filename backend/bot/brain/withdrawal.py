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



#: A verb meaning "leave this one and carry on", and the small objects it may
#: take. The object list is deliberately short: it, this, that, one, question.
#: Anything bigger is not a question, it is the interview.
_SKIP_VERB = r"(?:skip|pass\s+on|come\s+back\s+to)"
_SKIP_OBJECT = r"(?:it|this|that|these|those|one|ones|question|questions|topic|part|ahead)"

#: Asking to leave the *question*, not the interview.
#:
#: Searched inside the utterance, like the explicit stop patterns and for the
#: same measured reason: nobody says it as the whole of a sentence. The live one
#: was "right now, I don't know the answer of this question. Can we skip it?".
#:
#: Every pattern requires a **frame that addresses the person asking** — a
#: request ("can we skip"), a preference ("I'd rather skip"), a suggestion
#: ("let's move on"), or a bare imperative at the start of the sentence. Without
#: that frame, "we had to skip that release because of budget" is a skip
#: request, which is the same false positive the tool description already warns
#: the model about with "we had to end that project early".
_SKIP_PATTERNS = [
    rf"\b(?:can|could|shall|should|may)\s+(?:we|i)\s+(?:just\s+|please\s+)*{_SKIP_VERB}\b",
    r"\b(?:can|could|shall|should)\s+(?:we|i)\s+(?:just\s+|please\s+)*move\s+on\b",
    rf"\b(?:lets|let\s+us)\s+(?:just\s+)*(?:{_SKIP_VERB}\b|move\s+on\b)",
    rf"\bi(?:d|\s+would)\s+(?:rather|like\s+to)\s+(?:just\s+)*{_SKIP_VERB}\b",
    rf"\bi\s+want\s+to\s+(?:just\s+)*{_SKIP_VERB}\b",
    r"\b(?:next|another)\s+question\b",
]

#: The same, said as an instruction with nothing in front of it. Anchored,
#: because "skip it" is a skip and "I skipped it and it broke" is an answer.
_SKIP_IMPERATIVE = rf"^(?:just\s+|please\s+)*{_SKIP_VERB}\s+{_SKIP_OBJECT}\b"


def asked_to_skip_question(text: str) -> bool:
    """True when they want to leave this question and carry on with the rest.

    **This is a negative guard on the stop path, which is why it lives here.**
    A skip and a withdrawal share a shape — "can we skip it" against "can we end
    it" — and the live interview `int_7e31aaff4ab2` shows what that costs. The
    candidate said "right now, I don't know the answer of this question. Can we
    skip it?" and was asked whether they would like to end the interview.

    None of the detectors in this repo fired. `classify` returned NONE: no end
    verb, and the soft phrases have to be the whole utterance. `looks_like_refusal`
    holds "skip it" but matches only whole utterances too. `classify_repair`
    returned NONE. What fired was the interviewer model's own `end_interview`
    tool, reading a request to move on as a request to leave.

    **Everything here fails towards still hearing a withdrawal.** Four guards,
    and any one of them is enough to refuse to call something a skip:

    - naming the interview, call, session, conversation or meeting at all;
    - any end verb anywhere in the utterance;
    - any soft stop phrase anywhere in it, searched rather than matched whole,
      which is stricter here than `classify` is deliberately;
    - any stop intent `classify` itself can see.

    So "I'd like to skip the rest of this interview", "skip this and let's stop"
    and "I'm done, next question" all return False and take the ordinary stop
    path. The cost of that is a candidate being asked one question they would
    rather have skipped. The cost of the opposite is a candidate who wanted to
    leave being kept in the room, and those are not the same size of mistake.
    """
    normalised = _normalise(text)
    if not normalised:
        return False
    if re.search(_THIS_THING, normalised):
        return False
    if re.search(rf"\b{_END_VERB}\b", normalised):
        return False
    if any(phrase in normalised for phrase in _SOFT_PHRASES):
        return False
    if classify(text) is not StopIntent.NONE:
        return False

    forms = _stripped(normalised)
    if any(re.search(_SKIP_IMPERATIVE, form) for form in forms):
        return True
    return any(re.search(pattern, normalised) for pattern in _SKIP_PATTERNS)


#: Words without which a sentence cannot be about leaving. Deliberately broad:
#: this is not a detector, it is the floor under the model's `end_interview`
#: call. Every phrasing in `scripts/tool_call_check.py` that must end the
#: interview carries at least one of these.
_LEAVING_WORDS = re.compile(
    r"\b(?:end|ending|stop|stopping|finish|quit|leave|leaving|done|enough|bye|"
    r"goodbye|reschedule|continue|interested|comfortable|wrap|band|bas|khatam|"
    r"cancel|terminate|close|abort|exit|disconnect|withdraw)\b"
    r"|\bcall it a day\b|\bjump off\b|\b(?:isnt|not) for me\b"
    r"|\b(?:need|have|got|gotta|must)\s+(?:to\s+)?go\b"
    r"|\bdo this\b|\bchanged my mind\b|\bno more\b|\bthe rest\b"
    # Naming the interview at all: "can we skip the rest of this interview".
    rf"|\b{_THIS_THING}\b"
)


def could_mean_leaving(text: str) -> bool:
    """False only when nothing in the utterance could be about leaving.

    The model's `end_interview` tool is trusted for recall, and it overreaches.
    Live in `int_39845b4313d4` it fired on "But I didn't say it." (a candidate
    correcting a misheard word) and on "I don't" (an answer cut off by speech
    recognition), and both times the candidate was asked whether they wanted to
    end the interview. Neither sentence has a word in it that leaving needs.

    Fails towards hearing a withdrawal: any one of these words is enough, so the
    measured phrasings the patterns miss ("can we wrap this up", "this isn't for
    me", "bas, ab band karo") still reach the confirmation question.
    """
    return bool(_LEAVING_WORDS.search(_normalise(text or "")))
