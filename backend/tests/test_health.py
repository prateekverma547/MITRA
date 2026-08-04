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
    assert health.as_sentence is None


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
    sentence = health.as_sentence
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


# -- it has to survive the trip to the browser -------------------------------


def test_degraded_survives_serialisation():
    """It is a computed field for this reason. As a plain property it vanished
    on the way out, so the panel's banner checked an undefined value and never
    fired, while every unit test here passed."""
    payload = ConversationHealth(candidate_turns=10, disconnects=2).model_dump(mode="json")

    assert payload["degraded"] is True
    assert ConversationHealth(candidate_turns=10).model_dump(mode="json")["degraded"] is False




# -- echo: what it has to be told apart from ---------------------------------
#
# The detector compared word sets and ignored order, so it could not tell our
# audio coming back from an ordinary exchange. Across every stored interview it
# fired three times and every one was wrong: twice on a greeting, once on a
# fragment. Zero true positives, so every report carried "was heard through
# their own speakers" and none of them was right about it.
#
# There is no real echo in any recording we have. The true positives below are
# therefore constructed, and that is stated rather than glossed: they are what
# echo looks like, not what it looked like.

#: Verbatim from the greeting the bot speaks, in the stored transcripts.
GREETING = (
    "Good afternoon, Priya. I'm Mitra, an AI interviewer. "
    "I'll be speaking with you today. How are you doing?"
)

#: Verbatim from int_25259a2d4f80, where a fragment scored 100 percent.
LONG_TURN = (
    "Glad to hear you're doing well. I'm here to conduct your interview. This is "
    "for the Senior Product Manager — Payments role. It will take about 40 "
    "minutes. So, tell me what you're working on at the moment."
)


def test_greeting_each_other_is_not_echo():
    """The bug. Verbatim from int_b3d0a830156b: seven words, six of them in the
    bot's sentence, and two people saying hello."""
    hello = transcript(
        ("interviewer", GREETING),
        ("candidate", "I'm doing great. How are you?"),
    )

    assert assess(hello).echo_turns == 0


def test_the_other_greeting_from_the_transcripts_is_not_echo_either():
    """int_44c4f118812d, the same exchange worded slightly differently."""
    hello = transcript(
        ("interviewer", GREETING),
        ("candidate", "I'm great. How are you?"),
    )

    assert assess(hello).echo_turns == 0


def test_a_fragment_that_happens_to_reuse_our_words_is_not_echo():
    """int_25259a2d4f80. Four words, all of them somewhere in a long bot turn,
    100 percent by the old measure and not echo by any reading."""
    fragment = transcript(
        ("interviewer", LONG_TURN),
        ("candidate", "I'm working on"),
    )

    assert assess(fragment).echo_turns == 0


def test_our_sentence_returning_verbatim_is_still_echo():
    """SYNTHETIC. No stored session contains real echo, so this is constructed:
    a laptop speaker feeding the mic returns our own audio, transcribed as the
    candidate."""
    echoing = transcript(
        ("interviewer", "Tell me about a product you shipped recently."),
        ("candidate", "Tell me about a product you shipped recently"),
    )

    assert assess(echoing).echo_turns == 1


def test_our_sentence_returning_with_words_lost_is_still_echo():
    """SYNTHETIC. A real mic pickup drops words, it does not reorder them. The
    run has to survive that or the detector only catches perfect echo, which
    does not happen."""
    echoing = transcript(
        ("interviewer", LONG_TURN),
        ("candidate", "tell me what you're working on at the moment"),
    )

    assert assess(echoing).echo_turns == 1


def test_echo_of_the_greeting_itself_is_still_caught():
    """SYNTHETIC, and the one that proves the fix is not simply an exemption for
    greetings. The same bot turn, genuinely returned, still fires."""
    echoing = transcript(
        ("interviewer", GREETING),
        ("candidate", "I'll be speaking with you today. How are you doing?"),
    )

    assert assess(echoing).echo_turns == 1


def test_the_thinking_repeat_still_survives():
    """Named in the comment above `ECHO_RUN_FRACTION` as the reason the
    threshold is high. It has to keep passing after the change."""
    thinking = transcript(
        ("interviewer", "Tell me about a product you shipped recently."),
        ("candidate", "A product I shipped, right, so the biggest one was a payments migration "
                      "that took about nine months and I led the discovery for it."),
    )

    assert assess(thinking).echo_turns == 0


def test_a_real_greeting_no_longer_puts_a_false_clause_in_the_report():
    """The consequence, not just the count. The sentence a person reads must
    stop saying the candidate was heard through their own speakers."""
    from shared.contracts import ConversationHealth

    hello = transcript(
        ("interviewer", GREETING),
        ("candidate", "I'm doing great. How are you?"),
    )

    health = assess(hello, {"disconnects": 1})

    assert health.echo_turns == 0
    assert health.degraded is True, "the dropped call still degrades it, on its own merits"
    assert "own speakers" not in (health.as_sentence or "")


def test_a_genuine_echo_still_reaches_the_sentence():
    """SYNTHETIC. The clause has to remain available for the case it describes."""
    echoing = transcript(
        ("interviewer", "Tell me about a product you shipped recently."),
        ("candidate", "Tell me about a product you shipped recently"),
        ("interviewer", "Tell me about a product you shipped recently."),
        ("candidate", "Tell me about a product you shipped recently"),
    )

    health = assess(echoing)

    assert health.echo_turns == 2
    assert health.degraded is True
    assert "own speakers" in health.as_sentence
