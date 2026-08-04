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

from shared.contracts import (
    ConversationHealth,
    EvaluationSpec,
    JudgmentHealth,
    Speaker,
    Transcript,
)

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

#: How much of the candidate's turn has to be one unbroken stretch of the
#: interviewer's previous sentence before we call it echo. Set high: people
#: legitimately repeat a question back while thinking, and calling that echo
#: would discard a real answer.
#:
#: The value is unchanged from when this counted shared words instead. What is
#: measured changed, not where the line sits, and the margin says the line is
#: not doing the work: on real transcripts the false cases score 8 to 50 percent
#: and a genuine return scores 100.
ECHO_RUN_FRACTION = 0.7

#: Silence stages that mean the interviewer had to prompt. Stage 3 is the
#: graceful close, which is an outcome rather than a prompt.
PROMPTING_STAGES = (1, 2)


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()


def _longest_shared_run(said: list[str], ours: list[str]) -> int:
    """Longest stretch of `said` that appears consecutively in `ours`.

    Word by word rather than by set, because sequence is the whole signal here.
    Turn-sized inputs, so the straightforward scan is fast enough and readable.
    """
    best = 0
    for start in range(len(said)):
        for origin in range(len(ours)):
            run = 0
            while (
                start + run < len(said)
                and origin + run < len(ours)
                and said[start + run] == ours[origin + run]
            ):
                run += 1
            best = max(best, run)
    return best


def _looks_like_echo(candidate_text: str, previous_bot_text: str) -> bool:
    """True when the candidate's turn is our own sentence coming back at us.

    Happens when someone uses laptop speakers instead of headphones: the bot
    hears itself, transcribes it as the candidate, and answers its own question.

    **What it has to be distinguished FROM is the harder half, and getting that
    wrong is how this shipped broken.** It used to compare word *sets* and ignore
    order, which cannot tell echo from an ordinary conversation, because a
    conversation reuses the other person's words by design. Two things it fired
    on, across every stored interview and nothing else:

    - **Greetings.** "Good afternoon. I'm Mitra, an AI interviewer. How are you
      doing?" answered with "I'm doing great. How are you?" is seven words, six
      of them in the bot's sentence, 86 percent overlap and not remotely echo.
      Two people greeting each other use the same words in both directions.
    - **Fragments.** "I'm working on" scored 100 percent against a long bot turn
      simply because all four of its words appear somewhere in it.

    Every report therefore carried "was heard through their own speakers" when
    that had never happened.

    Sequence is what separates them. Real echo is our sentence returning **in
    order**, because it is our audio: it survives words being dropped by a poor
    mic, but not being rearranged. Conversational mirroring shares the words and
    reorders them, and a fragment shares a handful with no run at all. So this
    measures the longest unbroken stretch of the candidate's turn that appears
    consecutively in ours, and the two cases separate cleanly: measured on real
    transcripts, greetings score 43 to 50 percent and a fragment 50, while a
    verbatim or near-verbatim return scores 100.

    The same lesson as `_ACKNOWLEDGEMENTS` above, one signal along: a flag that
    fires on ordinary conversation cannot be the thing that decides anything.
    """
    said = _words(candidate_text)
    ours = _words(previous_bot_text)
    if len(said) < 4 or not ours:
        return False
    return _longest_shared_run(said, ours) / len(said) >= ECHO_RUN_FRACTION


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


def assess_judgment(
    session_metrics: dict | None, spec: EvaluationSpec
) -> JudgmentHealth:
    """Read how much of our own analysis actually ran, out of a finished session.

    Same shape and the same reasoning as `assess` above: derived from what the
    bot already wrote rather than recorded live, so an interview that has already
    happened picks this up the next time its report is rebuilt.

    Two sources, both written by `BrainDirector`. The counters say how much was
    attempted and how much landed. The `judgment` and `judgment_failed` events
    say *which* competency each one belonged to, which is what makes a weighted
    reading possible: the section id of a competency judgement is the competency
    id, so the employer's own weights apply directly.

    Interviews recorded before any of this existed carry none of it, and come
    back with everything at zero, which reads as not degraded. That is the honest
    answer: we do not know, and inventing a fault would be worse.
    """
    metrics = session_metrics or {}

    attempted = int(metrics.get("judgments_attempted") or 0)
    succeeded = int(metrics.get("judgments_succeeded") or 0)
    failed = int(metrics.get("judgments_failed") or 0)

    # A competency that produced even one successful judgement has claims behind
    # it. Only one that failed and never succeeded is missing from the report.
    succeeded_in: set[str] = set()
    failed_in: set[str] = set()
    for event in metrics.get("brain_events") or []:
        section = event.get("section")
        if not section:
            continue
        if event.get("kind") == "judgment":
            succeeded_in.add(section)
        elif event.get("kind") == "judgment_failed":
            failed_in.add(section)

    unjudged = failed_in - succeeded_in
    by_id = {c.id: c for c in spec.competencies}
    total_weight = sum(c.weight for c in spec.competencies) or 1.0
    lost_weight = sum(by_id[cid].weight for cid in unjudged if cid in by_id)

    return JudgmentHealth(
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        # Names rather than ids, because this is read by a person. Ordered by the
        # spec so the report does not shuffle between rebuilds.
        unjudged_competencies=[c.name for c in spec.competencies if c.id in unjudged],
        unjudged_weight=round(lost_weight / total_weight, 4),
    )
