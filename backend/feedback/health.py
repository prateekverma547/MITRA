"""Working out how well the candidate could actually be heard.

Derived from the stored transcript and session metrics rather than recorded
live, for two reasons. It is a pure function, so it can be tested without audio
and without a model. And an interview that already happened picks up a health
record the next time its report is rebuilt, which matters because these
heuristics will improve and the interviews they most need to explain are the
ones already in the database.

Nothing here decides anything about the candidate. It decides whether the
recording is a plausible explanation for thin answers, so that a report can say
so instead of quietly scoring someone down for a bad microphone.
"""

import re

from shared.contracts import ConversationHealth, Speaker, Transcript

#: Below this, a turn is too short to carry an answer to an interview question.
FRAGMENT_WORDS = 3

#: Fillers and acknowledgements do not count as fragments, however short they
#: are. OpenAI's STT emits "uh", "so" and "okay" as segments of their own, so
#: counting them made every real session look broken: measured across eight
#: recorded interviews, five were flagged, and the flag was worthless because it
#: fired on the healthy ones too.
_ACKNOWLEDGEMENTS = {
    "hi", "hello", "hey", "thanks", "thank you", "correct", "exactly", "true",
    "no", "nope", "yeah", "yes", "yep", "ok", "okay", "right", "sure", "well",
    "so", "um", "uh", "erm", "hmm", "hm", "mhm", "i see", "got it",
}

#: How much of the interviewer's previous sentence has to reappear in the
#: candidate's turn before we call it echo. Set high: people legitimately repeat
#: a question back while thinking, and calling that echo would discard a real
#: answer.
ECHO_OVERLAP = 0.7

#: Silence stages that mean the interviewer had to prompt. Stage 3 is the
#: graceful close, which is an outcome rather than a prompt.
PROMPTING_STAGES = (1, 2)


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()


def _looks_like_echo(candidate_text: str, previous_bot_text: str) -> bool:
    """True when the candidate's turn is mostly our own sentence coming back.

    Happens when someone uses laptop speakers instead of headphones: the bot
    hears itself, transcribes it as the candidate, and answers its own question.
    """
    said = _words(candidate_text)
    ours = set(_words(previous_bot_text))
    if len(said) < 4 or not ours:
        return False
    overlap = sum(1 for word in said if word in ours) / len(said)
    return overlap >= ECHO_OVERLAP


def assess(
    transcript: Transcript,
    session_metrics: dict | None = None,
    *,
    repair_requests: int = 0,
) -> ConversationHealth:
    """Read the channel out of a finished interview.

    `repair_requests` is passed in rather than derived: the interviewer knows
    when it asked for something again, and inferring it from its own wording
    would be guesswork about our own behaviour.
    """
    metrics = session_metrics or {}

    candidate_turns = 0
    fragmentary = 0
    echoes = 0
    previous_bot_text = ""

    for turn in transcript.turns:
        if turn.speaker == Speaker.INTERVIEWER:
            previous_bot_text = turn.text
            continue

        candidate_turns += 1
        words = _words(turn.text)
        if len(words) < FRAGMENT_WORDS and " ".join(words) not in _ACKNOWLEDGEMENTS:
            fragmentary += 1
        elif _looks_like_echo(turn.text, previous_bot_text):
            echoes += 1

    silence_events = metrics.get("silence_events") or []
    prompted = sum(1 for e in silence_events if e.get("stage") in PROMPTING_STAGES)
    dead_air = sum(float(e.get("dead_air_seconds") or 0.0) for e in silence_events)

    return ConversationHealth(
        candidate_turns=candidate_turns,
        fragmentary_turns=fragmentary,
        repair_requests=repair_requests,
        echo_turns=echoes,
        prompted_silences=prompted,
        dead_air_seconds=round(dead_air, 1),
        disconnects=int(metrics.get("disconnects") or 0),
    )
