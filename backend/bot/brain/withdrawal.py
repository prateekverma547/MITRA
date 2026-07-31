"""A candidate saying they want to stop, as opposed to not wanting to answer.

These are different things and the system was treating them as one:

    "I'd rather not answer that"        -> refuse the question, change topic
    "I don't want to continue"          -> they are leaving

Until now the second only ended anything after four consecutive refusals, so
somebody who had said plainly that they wanted to stop got asked several more
questions first. That is the opposite of respecting it.

**Held to a stricter standard than any other detector here, because the
consequence is terminal.** A missed withdrawal costs one more question, which
the candidate can repeat. A false one ends someone's interview and cannot be
undone. So: whole-utterance matching, an explicit phrase list rather than
keywords, and nothing acts on a single detection anyway. The brain offers to
stop and waits for them to say yes.

"I don't want to talk about my last employer" must never end an interview.
"""

from bot.brain.refusal import _FILLERS
from bot.brain.refusal import _normalise as _base_normalise

#: Said by someone who wants the interview to end. Every one of these is about
#: the interview itself, not about a question in it. Anything that could be read
#: as being about a topic is deliberately absent.
_WITHDRAWAL_PHRASES = {
    "i dont want to continue",
    "i do not want to continue",
    "i dont want to continue the interview",
    "i do not want to continue the interview",
    "i dont want to continue this interview",
    "i do not want to continue this interview",
    "i want to stop",
    "i want to stop the interview",
    "i want to stop here",
    "id like to stop",
    "i would like to stop",
    "id like to stop here",
    "i would like to stop here",
    "id like to end the interview",
    "i would like to end the interview",
    "i want to end the interview",
    "i want to end this interview",
    "can we stop",
    "can we stop here",
    "can we stop the interview",
    "lets stop here",
    "lets stop",
    "i dont want to do this",
    "i do not want to do this",
    "i dont want to do this interview",
    "i do not want to do this interview",
    "im done",
    "i am done",
    "im finished",
    "i quit",
    "i am withdrawing",
    "im withdrawing",
    "i withdraw",
    "i need to go",
    "i have to go",
    "i need to leave",
    "i have to leave",
    "please end the interview",
    "end the interview",
    "stop the interview",
}

#: Said in reply to "would you like to stop?". Only consulted once the offer has
#: been made, so a bare "yes" can never end an interview on its own.
_CONFIRMATIONS = {
    "yes", "yes please", "yeah", "yep", "yes lets stop", "yes stop",
    "please", "correct", "thats right", "id like to stop", "i would like to stop",
    "lets stop", "stop", "end it", "please end it", "yes end it",
    "im done", "i am done", "confirm", "yes im sure", "yes i am sure",
}

#: Said by someone who wants to carry on after all.
_DECLINED_TO_STOP = {
    "no", "no thanks", "no thank you", "im fine", "i am fine", "lets continue",
    "lets carry on", "carry on", "continue", "keep going", "no lets continue",
    "no im fine", "no i am fine", "sorry lets continue", "i want to continue",
    "no its fine", "no it is fine", "lets keep going",
}


def _normalise(text: str) -> str:
    return _base_normalise(text).replace("'", "")


#: People apologise when they withdraw. Almost nobody says "I want to stop"
#: flatly; they say "sorry, I don't want to continue". A live run failed to
#: detect exactly that sentence, and the interviewer responded by asking the
#: candidate for feedback about the interview process and then carrying on with
#: the questions. "sorry" is not in the shared filler set, and should not be:
#: elsewhere it often begins a real answer.
_APOLOGIES = ("sorry", "im sorry", "i am sorry", "apologies", "my apologies",
              "sorry but", "im really sorry", "i am really sorry")


def _without_filler(normalised: str) -> str:
    words = normalised.split()
    if words and words[0] in _FILLERS:
        return " ".join(words[1:])
    return normalised


def _without_apology(normalised: str) -> str:
    for opener in sorted(_APOLOGIES, key=len, reverse=True):
        if normalised.startswith(opener + " "):
            return normalised[len(opener) + 1:]
    return normalised


def _matches(text: str, phrases: set[str]) -> bool:
    normalised = _normalise(text)
    if not normalised:
        return False
    candidates = {
        normalised,
        _without_filler(normalised),
        _without_apology(normalised),
        _without_apology(_without_filler(normalised)),
        _without_filler(_without_apology(normalised)),
    }
    return any(c in phrases for c in candidates if c)


def wants_to_stop(text: str) -> bool:
    """True only when the whole utterance asks to end the interview.

    Whole-utterance matching is the safety property, and it matters more here
    than anywhere else in the system. "I don't want to continue down that line,
    what actually happened was..." is somebody carrying on talking.
    """
    return _matches(text, _WITHDRAWAL_PHRASES)


def confirms_stopping(text: str) -> bool:
    """True when they accept the offer to stop. Only meaningful after it is made."""
    return _matches(text, _CONFIRMATIONS)


def declines_stopping(text: str) -> bool:
    """True when they would rather carry on."""
    return _matches(text, _DECLINED_TO_STOP)
