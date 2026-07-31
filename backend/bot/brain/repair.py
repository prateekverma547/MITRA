"""Telling apart the three things a candidate means when a question misfires.

A scripted run made the problem concrete. The candidate said "Sorry, could you
say that again?", "Sorry, you cut out there.", "Can you repeat the question?"
and "Sorry, I did not catch that." Nothing recorded any of it, the report saw
only the wreckage of their answers, and the interviewer sometimes responded by
abandoning the question and asking a different one.

Three meanings, three different repairs. Conflating them is what makes a voice
bot feel stupid:

    "sorry, what?"            they did not hear it   -> say the same thing again
    "what do you mean?"       they did not follow it -> say it in simpler words
    "I don't know"            they heard and followed it -> that is an answer

The third is the one worth being careful about. It is not a repair and it is not
a refusal: it is an honest answer to a question they cannot answer, and treating
it as either would punish candour or loop forever.

Deterministic and conservative, for the same reason as `refusal.py`: matched
against the whole utterance, so "sorry" is a repair request and "sorry, that's
not right, what actually happened was..." is an answer. Reading a real answer as
a repair request is the worse error, because it throws the answer away.
"""

from enum import StrEnum

from bot.brain.refusal import _FILLERS
from bot.brain.refusal import _normalise as _base_normalise


class RepairKind(StrEnum):
    """What the candidate needs, if anything."""

    NONE = "none"
    #: They could not hear it. Say the same thing again, unchanged.
    REPEAT = "repeat"
    #: They heard it but it did not land. Say it differently and more simply.
    SIMPLIFY = "simplify"
    #: Our own voice came back through their speakers.
    ECHO = "echo"


#: "I could not hear you." Repeating verbatim is the right answer; rephrasing
#: here is actively confusing, because the words were never the problem.
_REPEAT_PHRASES = {
    "sorry", "sorry what", "what", "pardon", "pardon me", "come again",
    "say that again", "can you say that again", "could you say that again",
    "can you repeat that", "can you repeat", "could you repeat that",
    "could you repeat the question", "can you repeat the question",
    "repeat that", "repeat the question", "say again", "once more",
    "i missed that", "i did not catch that", "i didnt catch that",
    "didnt catch that", "i did not hear that", "i didnt hear that",
    "i cant hear you", "i can not hear you", "you cut out",
    "you cut out there", "sorry you cut out", "sorry you cut out there",
    "you are breaking up", "youre breaking up", "i lost you",
    "sorry i did not catch that", "sorry i didnt catch that",
    "sorry could you say that again", "sorry can you say that again",
    "sorry can you repeat that", "sorry i missed that",
}

#: "I do not follow." Repeating the same sentence is useless; these need
#: different, simpler words.
_SIMPLIFY_PHRASES = {
    "i dont understand", "i do not understand", "i dont understand the question",
    "i do not understand the question", "i dont get the question",
    "i dont follow", "i do not follow", "what do you mean",
    "what do you mean by that", "not sure what you are asking",
    "not sure what youre asking", "im not sure what you are asking",
    "im not sure what youre asking", "can you rephrase that",
    "could you rephrase that", "can you rephrase", "could you rephrase",
    "can you explain the question", "could you explain the question",
    "can you be more specific", "could you be more specific",
    "what exactly are you asking", "i am not sure what you mean",
    "im not sure what you mean", "sorry i dont understand",
    "sorry i do not understand", "sorry i dont follow",
}

#: Not a repair at all. Listed so it is explicit that these are answers, and so
#: nobody later "fixes" the detector by folding them in.
_NOT_A_REPAIR = {
    "i dont know", "i do not know", "im not sure", "i am not sure",
    "no idea", "i have no idea", "i cant remember", "i can not remember",
    "i dont remember", "i do not remember",
}


def _normalise(text: str) -> str:
    """Lowercase, unpunctuated, and apostrophe-free.

    The shared normaliser keeps apostrophes, so "don't" and "dont" are
    different strings to it. Both reach us from STT depending on the moment, and
    listing every phrase twice would be a standing invitation to add the next
    one only once.
    """
    return _base_normalise(text).replace("'", "")


def _strip_filler(normalised: str) -> str:
    """Drop one leading filler: "um, sorry?", "well, what do you mean"."""
    words = normalised.split()
    if words and words[0] in _FILLERS:
        return " ".join(words[1:])
    return normalised


def classify(text: str) -> RepairKind:
    """What the candidate is asking for, matched against the whole utterance.

    Whole-utterance matching is the safety property. Someone who says "sorry, I
    should explain, we actually built two of them" is answering, and treating
    that as a request to repeat would discard the answer and re-ask a question
    they had already started to address.
    """
    normalised = _normalise(text)
    if not normalised:
        return RepairKind.NONE

    for candidate in (normalised, _strip_filler(normalised)):
        if not candidate:
            continue
        # Checked first and deliberately: "I don't know" is an answer, and it
        # must never be read as confusion or the interview loops on it.
        if candidate in _NOT_A_REPAIR:
            return RepairKind.NONE
        if candidate in _REPEAT_PHRASES:
            return RepairKind.REPEAT
        if candidate in _SIMPLIFY_PHRASES:
            return RepairKind.SIMPLIFY

    return RepairKind.NONE


def is_repair(text: str) -> bool:
    """True when the turn carried no answer, only a request to try again."""
    return classify(text) in (RepairKind.REPEAT, RepairKind.SIMPLIFY, RepairKind.ECHO)
