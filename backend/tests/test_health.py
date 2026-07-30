"""Conversation health: telling a bad connection apart from a bad candidate.

The failure this prevents is specific. Someone joins on a cheap headset, half
their words do not arrive, and the transcript reads as incoherent. Nothing in
the report says the recording was poor, so a human reads it as a person who
could not answer, and that goes into a hiring record.

Pure and deterministic, so it is all testable without audio or a model.
"""

import pytest

from feedback.health import assess
from shared.contracts import ConversationHealth, Speaker, Transcript, TranscriptTurn


def transcript(*pairs) -> Transcript:
    turns = [
        TranscriptTurn(index=i, speaker=Speaker(who), text=text, at_seconds=float(i * 20))
        for i, (who, text) in enumerate(pairs)
    ]
    return Transcript(interview_id="int_1", turns=turns, duration_seconds=len(turns) * 20.0)


def silence(*stages) -> dict:
    return {
        "silence_events": [
            {"stage": s, "action": "nudge", "dead_air_seconds": 22.0} for s in stages
        ]
    }


GOOD = transcript(
    ("interviewer", "Tell me about a product you shipped."),
    ("candidate", "We built a retrieval assistant for support agents and cut handling time by a third."),
    ("interviewer", "How did you measure that?"),
    ("candidate", "We compared median handling time before and after, over eight weeks."),
)


# -- a clean interview is reported as clean ----------------------------------


def test_a_clean_interview_is_not_flagged():
    health = assess(GOOD)

    assert health.candidate_turns == 2
    assert health.degraded is False
    assert health.was_clean is True
    assert health.as_sentence() is None


def test_one_bad_moment_is_not_a_bad_recording():
    """Every interview has a "sorry?" in it. Flagging that would make the
    signal meaningless exactly when it matters."""
    health = assess(GOOD, repair_requests=1)

    assert health.degraded is False


# -- the failure it exists to catch ------------------------------------------


def test_a_candidate_who_could_not_be_heard_is_flagged():
    broken = transcript(
        ("interviewer", "Tell me about a product you shipped."),
        ("candidate", "sorry"),
        ("interviewer", "Can you say that again?"),
        ("candidate", "I said"),
        ("interviewer", "I only caught part of that."),
        ("candidate", "for the"),
        ("interviewer", "Take your time."),
        ("candidate", "the"),
    )

    health = assess(broken, silence(1, 2), repair_requests=3)

    assert health.fragmentary_turns == 4
    assert health.degraded is True
    sentence = health.as_sentence()
    assert "recording was poor" in sentence
    assert "not a conclusion about the candidate" in sentence


def test_a_disconnect_alone_is_enough():
    """Dropping out of the call is unambiguous; it needs no corroboration."""
    health = assess(GOOD, {"disconnects": 1})

    assert health.degraded is True


def test_repeated_prompting_is_enough():
    health = assess(GOOD, silence(1, 1, 2))

    assert health.prompted_silences == 3
    assert health.degraded is True


# -- echo --------------------------------------------------------------------


def test_our_own_voice_coming_back_is_counted_as_echo():
    """Laptop speakers instead of headphones: the bot hears itself, transcribes
    it as the candidate, and would otherwise answer its own question."""
    echoing = transcript(
        ("interviewer", "Tell me about a product you shipped recently."),
        ("candidate", "Tell me about a product you shipped recently"),
    )

    health = assess(echoing)

    assert health.echo_turns == 1


def test_repeating_a_question_back_while_thinking_is_not_echo():
    """People do this. Treating it as echo would discard a real answer."""
    thinking = transcript(
        ("interviewer", "Tell me about a product you shipped recently."),
        ("candidate", "A product I shipped, right, so the biggest one was a payments migration "
                      "that took about nine months and I led the discovery for it."),
    )

    health = assess(thinking)

    assert health.echo_turns == 0


def test_a_short_agreement_is_not_echo():
    brief = transcript(
        ("interviewer", "Shall we start?"),
        ("candidate", "yes"),
    )

    assert assess(brief).echo_turns == 0


# -- it never invents a verdict ----------------------------------------------


def test_an_empty_transcript_is_not_called_degraded():
    """Nothing to judge is not the same as judged badly."""
    health = assess(transcript(("interviewer", "Hello.")))

    assert health.candidate_turns == 0
    assert health.degraded is False


def test_missing_metrics_are_survivable():
    """Older interviews have no session metrics at all."""
    health = assess(GOOD, None)

    assert health.degraded is False
    assert health.dead_air_seconds == 0.0


# -- what the report does with it --------------------------------------------


def test_a_degraded_recording_caps_a_confident_recommendation():
    """A confident reading cannot come from a recording nobody could hear."""
    from feedback.score import build_report
    from shared.contracts import Competency, EvaluationSpec, RecommendationSignal

    spec = EvaluationSpec(
        role_title="Business Analyst",
        seniority="Mid",
        experience_expectation="5 years",
        duration_minutes=40,
        competencies=[Competency(id="a", name="Analysis", description="d", weight=1.0)],
    )
    payload = {
        "competency_scores": [{
            "competency_id": "a", "name": "Analysis", "score": 4.5,
            "coverage": "sufficient", "rationale": "Strong.",
            "evidence": [{"turn_index": 1, "text": "We built a retrieval assistant"}],
        }],
        "summary": "Good.",
        "recommendation": "strong_evidence_for",
    }

    report = build_report(
        interview_id="int_1", blueprint_id="cand_1", spec=spec, transcript=GOOD,
        payload=payload, health=ConversationHealth(candidate_turns=10, disconnects=2),
    )

    assert report.recommendation == RecommendationSignal.LIMITED_EVIDENCE
    assert report.conversation_health.degraded is True


def test_a_clean_recording_leaves_the_recommendation_alone():
    from feedback.score import build_report
    from shared.contracts import Competency, EvaluationSpec, RecommendationSignal

    spec = EvaluationSpec(
        role_title="Business Analyst",
        seniority="Mid",
        experience_expectation="5 years",
        duration_minutes=40,
        competencies=[Competency(id="a", name="Analysis", description="d", weight=1.0)],
    )
    payload = {
        "competency_scores": [{
            "competency_id": "a", "name": "Analysis", "score": 4.5,
            "coverage": "sufficient", "rationale": "Strong.",
            "evidence": [{"turn_index": 1, "text": "We built a retrieval assistant"}],
        }],
        "summary": "Good.",
        "recommendation": "strong_evidence_for",
    }

    report = build_report(
        interview_id="int_1", blueprint_id="cand_1", spec=spec, transcript=GOOD,
        payload=payload, health=assess(GOOD),
    )

    assert report.recommendation == RecommendationSignal.STRONG_EVIDENCE_FOR


# -- calibrated against real recordings --------------------------------------


def test_filler_is_not_counted_as_a_broken_answer():
    """OpenAI's STT emits "uh", "so" and "okay" as turns of their own. Counting
    them flagged five of eight real interviews, healthy ones included, which
    made the flag worth nothing."""
    natural = transcript(
        ("interviewer", "Tell me about your last project."),
        ("candidate", "Uh"),
        ("interviewer", "Take your time."),
        ("candidate", "So"),
        ("interviewer", "Go on."),
        ("candidate", "Okay"),
        ("interviewer", "Whenever you are ready."),
        ("candidate", "yeah"),
    )

    health = assess(natural)

    assert health.fragmentary_turns == 0
    assert health.degraded is False


def test_fragments_alone_do_not_flag_a_recording():
    """They occur in every session. Only a real signal beside them counts."""
    choppy = transcript(
        ("interviewer", "Tell me about your last project."),
        ("candidate", "for the"),
        ("interviewer", "Go on."),
        ("candidate", "increment"),
        ("interviewer", "And then?"),
        ("candidate", "It was"),
        ("interviewer", "Keep going."),
        ("candidate", "video call"),
    )

    assert assess(choppy).fragmentary_turns == 4
    assert assess(choppy).degraded is False
    # One real signal alongside them, and it does count.
    assert assess(choppy, repair_requests=1).degraded is True
