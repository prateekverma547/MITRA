"""Did the interviewer actually ask about the term it was handed?

The brain puts one unexplained term in front of the model and supplies the
sentence to ask. The model may use it, and may not. Counting the handing-over as
an ask produced a live falsehood:

    "You have said session management a couple of times now, and I still do not
     know what it actually does."

said to a candidate asked exactly once. The interviewer told somebody they had
failed to answer a question that was never put to them. It also spent the
two-attempt allowance on turns where nothing was asked, so a term could be let
go with "let's leave that one there" after one real ask, or none.

So this counts questions, not offers, the same way `repair.py` counts attempts
rather than opportunities.

**Deterministic and conservative, and the direction of the error is chosen.**
Undercounting means a term takes an extra turn to be let go, which is mildly
annoying. Overcounting means telling a candidate something untrue, which is the
bug. Every rule below is written to fail towards False.

Two ways a question counts as being about the term, because a question can be
about something without naming it:

    "You said session management. Walk me through how that works."   named
    "How does that actually work?"                                   deictic

The second is only read as an ask because it is only ever checked when a term
was put in front of the model on that turn, and the block it was given supplies
almost exactly that sentence. Out of that context it would mean nothing.
"""

import re

#: Asking what a thing is or how it works, with the reference built in. Every
#: one of these points back at what the candidate just said, which is what makes
#: them readable as an ask about the offered term rather than a fresh question.
#:
#: Deliberately short. A longer list would catch more real asks and would also
#: start catching questions about something else, and that is the trade this
#: file is written to refuse.
_DEICTIC_CUES = (
    "how does that",
    "how did that",
    "how does it",
    "how that actually works",
    "how that works",
    "walk me through that",
    "walk me through how",
    "what does that do",
    "what does it do",
    "what does that mean",
    "what do you mean by that",
    "what exactly does that",
    "how exactly does that",
)

#: Words too common to identify a term by. "the platform" and "the system" would
#: otherwise match almost any question.
_WEAK = {
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "with",
    "system", "systems", "platform", "layer", "approach", "process", "thing",
    "that", "this", "it", "your", "our", "we", "you", "used", "using", "use",
}


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def _distinctive(term: str) -> list[str]:
    """The words that actually identify this term.

    Short and common words are dropped, so "the platform" identifies nothing and
    a question is never counted as being about it on that basis alone. That is
    the conservative direction: such a term simply takes longer to spend its
    allowance.
    """
    return [w for w in _words(term) if len(w) > 3 and w not in _WEAK]


def _singularish(word: str) -> str:
    """Trailing-s tolerance only.

    The same rule the interview register uses, and for the same reason: "hash
    tables" and "hash table" are the same term, and anything looser stops being
    a match and starts being a guess.
    """
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def asked_about(term: str, question: str) -> bool:
    """True when this question put the term to the candidate.

    Only meaningful for a question asked on a turn where `term` was offered to
    the model. It is not a general-purpose topic classifier and would be wrong
    used as one.
    """
    if not term or not question:
        return False

    # **A statement asked nothing.** The spoken acknowledgement repeats the
    # candidate's own term back at them ("Okay. So you used session
    # management."), which matches every word test below and is not a question.
    # Counting it burned an attempt without anything having been put to the
    # candidate. VOICE_RULES requires exactly one question mark in a real reply,
    # so requiring one here is the same rule read from the other end.
    if "?" not in question:
        return False

    asked = set(map(_singularish, _words(question)))
    distinctive = [_singularish(w) for w in _distinctive(term)]

    # Named. One distinctive word is enough: "session management" is asked about
    # by a question mentioning "session".
    if distinctive and any(w in asked for w in distinctive):
        return True

    # Not named, but pointing back at it. Only readable as an ask because a term
    # was offered on this turn; see the module docstring.
    lowered = " ".join(_words(question))
    return any(cue in lowered for cue in _DEICTIC_CUES)
