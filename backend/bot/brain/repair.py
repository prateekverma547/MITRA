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

import re
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
    #: "Hello?", "are you there?": checking the line, not answering. Say you are
    #: there and can hear them, then ask the same question again.
    PRESENCE = "presence"
    #: "How are you?" mid-interview. A person would answer it in a word and get
    #: back to the question; a live run answered it with the bot's job title.
    SMALL_TALK = "small_talk"
    #: Not produced by `classify`, which only reads text. The brain sets it when
    #: they still could not hear the question after the allowed attempts: the
    #: question is set aside, and the interviewer moves on rather than asking it
    #: a third time or following up on a question nobody heard.
    GIVE_UP = "give_up"
    #: Not produced by `classify`. The candidate spoke over the question before it
    #: was finished and what they said was not an answer ("hmm", "sorry?", "go
    #: on"). They never heard the whole question, so it is asked again in full.
    INTERRUPTED = "interrupted"
    #: Not produced by `classify`. They cut in to ask for a moment ("one second",
    #: "hold on"). Say "take your time" and keep the question for when they are back.
    HOLD = "hold"
    #: What reached us cannot have been what they said, so ask, once. "I know."
    #: as the whole reply to an open question is almost always a misheard "I
    #: don't know" (see `_UNCLEAR_REPLIES`). The interviewer checks rather than
    #: guessing, because guessing either way puts words in their mouth.
    UNCLEAR = "unclear"


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
    # Live in `int_39845b4313d4`, each read as an answer and each answered with
    # a new question: "What are you saying?", "What are we saying?" (as
    # transcribed), "The last question?".
    "what are you saying", "what are we saying", "what did you say",
    "what was that", "what was the question", "what is the question",
    "whats the question", "what was your question", "whats your question",
    "the last question", "last question", "what was the last question",
    "repeat the last question", "can you repeat the last question",
    "could you repeat the last question", "which question", "say it again",
    "can you say it again", "could you say it again", "again",
}

#: Checking the line. Matched whole, like everything here: "hello, so the first
#: thing we did was..." is an answer.
_PRESENCE_PHRASES = {
    "hello", "hello hello", "hello hello hello", "hello are you there",
    "are you there", "you there", "is anyone there", "anyone there",
    "can you hear me", "hello can you hear me", "can you hear me now",
    "are you still there", "hello are you still there", "mitra are you there",
    "hello mitra", "can you hear me properly",
}

#: Asking after the interviewer. Whole utterance only: "good, how are you?" in
#: the opening is an answer to the greeting and is left to the model.
_SMALL_TALK_PHRASES = {
    "how are you", "how are you doing", "and how are you", "how about you",
    "what about you", "and you", "how is it going", "hows it going",
    "how are you today", "how are you doing today",
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
    # How "I don't know" is actually said at speed, and how STT spells it.
    # The same answer, whatever the spelling.
    "dunno", "i dunno", "i don know", "i dont no", "i do no",
}

#: Replies that cannot be what the candidate said, given what they were asked.
#:
#: Live in `int_fb22eafe1628` the candidate said "I don't know" eight times and
#: every one was transcribed "I know." Spoken quickly, "I don't know" collapses
#: to "I dunno", and the weak middle syllable is what streaming STT drops. Every
#: one of those turns had finalised normally (`stt_lag` 0.52-0.61s, under the
#: 0.65s ceiling), so this is the vendor's final hypothesis, not a turn fired on
#: a partial: nothing in turn-taking can fix it.
#:
#: Nothing matched "I know", so it was taken as an empty answer and the
#: interviewer moved on, and the record shows somebody saying something
#: meaningless eight times instead of somebody being candid.
#:
#: **Never rewritten to "I don't know".** That would put words in a
#: candidate's mouth inside the hiring record, the failure this project ranks
#: worst. The transcript keeps what we heard; the interviewer asks once.
#:
#: Whole utterance only, like everything here: "I know the codebase well, we
#: built..." is an answer.
_UNCLEAR_REPLIES = {"i know", "i no"}

#: "Yes" to "Did you say you don't know?". Whole utterance, after one leading
#: filler, like everything else here.
#:
#: Live in `int_8e3db77a60a9` this "Yes." went to the model as a bare answer to
#: nothing. The voice rules tell it to say "Sorry, my mistake" when it misheard,
#: so it did, and asked the same question again; the next "I know." was checked
#: again, and the interview spent most of its eighteen minutes in that loop.
_AFFIRMATIONS = {
    "yes", "yeah", "yep", "yup", "ya", "yah", "haan", "correct", "right",
    "exactly", "thats right", "that is right", "thats correct",
    "that is correct", "yes thats right", "yes correct", "yes exactly",
    "yes i did", "yeah i did", "i did", "yes i said that", "yes sir",
    "yes maam", "yes yes",
}


def confirms_dont_know(text: str) -> bool:
    """True when `text`, said after the misheard check, means "yes, I don't know".

    Either a plain affirmation ("Yes.", "That's right.") or one followed by the
    answer itself ("Yes, I don't know.", "Yeah, I said I don't know."). A bare
    "I don't know" is deliberately not included: it is already an answer in its
    own words and needs no interpreting.
    """
    # Tried with and without one leading filler: the shared filler list counts
    # "yeah" and "yes" as filler, so stripping alone would remove the very word
    # being looked for, and "um, yes" needs the strip.
    normalised = _normalise(text)
    for candidate in (normalised, _strip_filler(normalised)):
        words = candidate.split()
        if not words:
            continue
        if candidate in _AFFIRMATIONS:
            return True
        if words[0] not in {"yes", "yeah", "yep", "yup", "ya", "correct", "right"}:
            continue
        rest = " ".join(words[1:])
        if rest.startswith("i said "):
            rest = rest[len("i said "):]
        if rest in _NOT_A_REPAIR:
            return True
    return False


#: Openings that make a question answerable with a bare yes or no. After "Do
#: you know what UAT is?", "I know." is a reasonable answer and is left alone.
_CLOSED_QUESTION_OPENERS = {
    "do", "does", "did", "have", "has", "had", "are", "is", "was", "were",
    "can", "could", "would", "will", "should", "shall", "may",
}


def asks_yes_or_no(question: str) -> bool:
    """True when the last question in `question` can be answered yes or no.

    Reads the final question sentence only, so "Okay. Do you know..." and
    "Thanks. Walk me through..." are judged by what was actually asked.
    """
    sentences = [s for s in _base_normalise_sentences(question) if s]
    if not sentences:
        return False
    words = sentences[-1].split()
    return bool(words) and words[0] in _CLOSED_QUESTION_OPENERS


def _base_normalise_sentences(text: str) -> list[str]:
    """Split on sentence ends, then normalise each piece the usual way."""
    return [_normalise(piece) for piece in re.split(r"[.?!]", text or "")]


#: Politeness words that change nothing about what is being asked. "Can you
#: please repeat?" is the same request as "can you repeat?", and a live run
#: showed the phrase list missing it on every one of seven real requests, so
#: each was read as an answer and the interviewer asked a new question instead.
#: Dropped here rather than listed phrase by phrase, because the list will
#: never catch every way of adding one.
_POLITENESS = {"please", "pls"}


def _normalise(text: str) -> str:
    """Lowercase, unpunctuated, apostrophe-free, and without politeness words.

    The shared normaliser keeps apostrophes, so "don't" and "dont" are
    different strings to it. Both reach us from STT depending on the moment, and
    listing every phrase twice would be a standing invitation to add the next
    one only once.
    """
    words = _base_normalise(text).replace("'", "").split()
    return " ".join(w for w in words if w not in _POLITENESS)


#: Apology words that can open a request: "pardon, could you say that again?".
#: Only ever stripped from the front, and the remainder must still match a whole
#: phrase, so "sorry, I should explain" is still an answer.
_APOLOGY = {"sorry", "pardon", "apologies", "apologise", "apologize"}


def _strip_filler(normalised: str) -> str:
    """Drop one leading filler or apology: "um, sorry?", "pardon, what do you mean"."""
    words = normalised.split()
    if words and (words[0] in _FILLERS or words[0] in _APOLOGY):
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
        if candidate in _PRESENCE_PHRASES:
            return RepairKind.PRESENCE
        if candidate in _SMALL_TALK_PHRASES:
            return RepairKind.SMALL_TALK
        if candidate in _UNCLEAR_REPLIES:
            return RepairKind.UNCLEAR

    return RepairKind.NONE


#: Said over an unfinished question, these carry no answer: a backchannel, a
#: reflex apology, or "carry on". Matched as the whole utterance, as everything
#: here is. Measured against the real model before this existed: after "Hmm." or
#: "Sorry, one second." over a half-asked question, it moved on to a new question
#: in six tries out of six, and the cut-off question was never heard in full.
_INTERJECTIONS = {
    "hmm", "hm", "mm", "mhm", "mmhm", "uh huh", "uhhuh", "oh", "ah", "um", "uh",
    "yeah", "yes", "yep", "yup", "ok", "okay", "right", "sure", "alright",
    "sorry", "oh sorry", "sorry go on", "sorry go ahead", "sorry carry on",
    "go on", "go ahead", "carry on", "continue", "please continue",
    "no go ahead", "no go on", "no sorry go ahead", "sorry about that",
    "sorry i interrupted", "sorry to interrupt", "sorry for interrupting",
    "yes go ahead", "yeah go ahead", "okay go ahead", "ok go ahead",
    "im back", "i am back", "okay im back", "ok im back", "sorry im back",
    "okay im ready", "im ready", "ready",
}

#: Asking for a moment. Not an answer either, but the right reply is to wait,
#: not to fire the question straight back at them.
_HOLD_PHRASES = {
    "one second", "one sec", "a second", "just a second", "give me a second",
    "give me one second", "hold on", "hold on a second", "wait", "wait a second",
    "one moment", "just a moment", "give me a moment", "a moment",
    "one minute", "just a minute", "give me a minute", "sorry one second",
    "sorry one sec", "sorry hold on", "sorry wait", "sorry just a second",
    "sorry give me a second", "sorry one moment", "sorry just a moment",
}


def classify_interjection(text: str) -> RepairKind:
    """What a candidate meant by speaking over an unfinished question.

    NONE means they were answering, or starting to: that is kept as an answer,
    because throwing away a real answer is the worse error. Otherwise HOLD when
    they asked for a moment, INTERRUPTED for everything that carries no answer,
    including "sorry, what?": they never heard the whole question, so asking it
    again in full is the right reply to that too.
    """
    normalised = _normalise(text)
    if not normalised:
        return RepairKind.INTERRUPTED
    for candidate in (normalised, _strip_filler(normalised)):
        if not candidate:
            return RepairKind.INTERRUPTED
        if candidate in _HOLD_PHRASES:
            return RepairKind.HOLD
        if candidate in _INTERJECTIONS:
            return RepairKind.INTERRUPTED
    if classify(text) in (
        RepairKind.REPEAT, RepairKind.SIMPLIFY, RepairKind.PRESENCE, RepairKind.SMALL_TALK
    ):
        return RepairKind.INTERRUPTED
    return RepairKind.NONE


def is_repair(text: str) -> bool:
    """True when the turn carried no answer, only a request to try again."""
    return classify(text) in (
        RepairKind.REPEAT, RepairKind.SIMPLIFY, RepairKind.ECHO,
        RepairKind.PRESENCE, RepairKind.SMALL_TALK,
    )
