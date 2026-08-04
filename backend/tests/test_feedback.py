"""Feedback scoring — deterministic suite, no LLM calls.

`build_report` is pure, so everything that makes a report trustworthy can be
tested without the network: quotes are checked against the transcript, scores
that lose their evidence are downgraded, and nothing the candidate did not say
survives into a hiring record.

The tolerant, real-LLM half lives in `test_behavior.py`.
"""

import pytest

from feedback.health import assess_judgment
from feedback.score import build_report, verify_quote
from shared.contracts import (
    Competency,
    ConversationHealth,
    CoverageLevel,
    EvaluationSpec,
    RecommendationSignal,
    Speaker,
    Transcript,
    TranscriptTurn,
)

SPEC = EvaluationSpec(
    role_title="Lead Product Manager",
    seniority="Senior",
    experience_expectation="10+ years",
    duration_minutes=40,
    competencies=[
        Competency(id="delivery", name="LLM Delivery", description="Ships LLM products.", weight=0.6),
        Competency(id="stakeholders", name="Stakeholders", description="Navigates conflict.", weight=0.4),
    ],
    red_flags=["Cannot explain hallucination handling"],
)


def transcript(*pairs) -> Transcript:
    """(speaker, text) pairs -> a Transcript with sane indices and timestamps."""
    turns = []
    for i, (speaker, text) in enumerate(pairs):
        turns.append(
            TranscriptTurn(
                index=i,
                speaker=Speaker(speaker),
                text=text,
                at_seconds=float(i * 30),
            )
        )
    return Transcript(interview_id="int_1", turns=turns, duration_seconds=len(turns) * 30.0)


TRANSCRIPT = transcript(
    ("interviewer", "Tell me about an LLM product you shipped."),
    ("candidate", "We shipped a retrieval assistant for support agents, and I cut hallucinations by grounding every answer in the ticket history."),
    ("interviewer", "How did you handle legal?"),
    ("candidate", "Legal wanted a blanket disclaimer, engineering said it would kill trust, so we agreed on a confidence threshold instead."),
)


def report_from(payload, tr=TRANSCRIPT, spec=SPEC, health=None, judgment_health=None):
    return build_report(
        interview_id="int_1",
        blueprint_id="cand_1",
        spec=spec,
        transcript=tr,
        payload=payload,
        health=health,
        judgment_health=judgment_health,
    )


# -- quote verification ------------------------------------------------------


def test_a_real_quote_is_anchored_to_its_turn():
    quote = verify_quote("cut hallucinations by grounding every answer", 1, TRANSCRIPT)

    assert quote is not None
    assert quote.turn_index == 1
    assert quote.at_seconds == 30.0


def test_an_invented_quote_is_rejected():
    """The failure this whole module exists to prevent."""
    assert verify_quote("I have twelve years of experience at Google", 1, TRANSCRIPT) is None


def test_a_quote_cited_at_the_wrong_turn_is_repaired_not_dropped():
    """The model miscounted, but the candidate did say the words."""
    quote = verify_quote("we agreed on a confidence threshold", 1, TRANSCRIPT)

    assert quote is not None
    assert quote.turn_index == 3


def test_the_interviewers_own_words_are_not_evidence_about_the_candidate():
    """Otherwise the bot's question becomes the candidate's answer."""
    assert verify_quote("Tell me about an LLM product you shipped", 0, TRANSCRIPT) is None


def test_light_reformatting_survives():
    """Punctuation drift is a formatting difference, not a fabrication —
    rejecting it would throw away real evidence."""
    assert verify_quote("Legal wanted a blanket disclaimer!", 3, TRANSCRIPT) is not None


# -- scores must rest on evidence --------------------------------------------


def test_a_score_whose_evidence_is_fabricated_is_downgraded():
    report = report_from({
        "competency_scores": [{
            "competency_id": "delivery",
            "name": "LLM Delivery",
            "score": 4.5,
            "coverage": "sufficient",
            "rationale": "Strong delivery record.",
            "evidence": [{"turn_index": 1, "text": "I led a team of forty engineers"}],
        }],
        "summary": "Good.",
        "recommendation": "strong_evidence_for",
    })

    delivery = next(s for s in report.competency_scores if s.competency_id == "delivery")
    assert delivery.score is None
    assert delivery.insufficient_signal is True
    assert delivery.evidence == []
    assert "No verifiable transcript evidence" in delivery.rationale


def test_a_score_with_real_evidence_survives_intact():
    report = report_from({
        "competency_scores": [{
            "competency_id": "delivery",
            "name": "LLM Delivery",
            "score": 4.0,
            "coverage": "sufficient",
            "rationale": "Shipped and measured it.",
            "evidence": [{"turn_index": 1, "text": "We shipped a retrieval assistant for support agents"}],
        }],
        "summary": "Good.",
        "recommendation": "some_evidence_for",
    })

    delivery = next(s for s in report.competency_scores if s.competency_id == "delivery")
    assert delivery.score == 4.0
    assert delivery.insufficient_signal is False
    assert delivery.evidence[0].turn_index == 1


def test_only_the_fabricated_quote_is_dropped_from_a_mixed_set():
    report = report_from({
        "competency_scores": [{
            "competency_id": "delivery",
            "name": "LLM Delivery",
            "score": 4.0,
            "coverage": "sufficient",
            "rationale": "Shipped it.",
            "evidence": [
                {"turn_index": 1, "text": "We shipped a retrieval assistant"},
                {"turn_index": 1, "text": "and I reported to the CEO"},
            ],
        }],
        "summary": "Good.",
        "recommendation": "some_evidence_for",
    })

    delivery = next(s for s in report.competency_scores if s.competency_id == "delivery")
    assert delivery.score == 4.0  # the surviving quote still supports it
    assert len(delivery.evidence) == 1


def test_a_competency_the_model_skipped_is_reported_as_unassessed():
    """Silence would read as "nothing to say" rather than "never assessed"."""
    report = report_from({
        "competency_scores": [{
            "competency_id": "delivery",
            "name": "LLM Delivery",
            "score": 4.0,
            "coverage": "sufficient",
            "rationale": "Shipped it.",
            "evidence": [{"turn_index": 1, "text": "We shipped a retrieval assistant"}],
        }],
        "summary": "Good.",
        "recommendation": "some_evidence_for",
    })

    ids = [s.competency_id for s in report.competency_scores]
    assert ids == ["delivery", "stakeholders"]
    stakeholders = report.competency_scores[1]
    assert stakeholders.insufficient_signal is True
    assert stakeholders.score is None


def test_coverage_never_flatters_an_unevidenced_competency():
    report = report_from({
        "competency_scores": [{
            "competency_id": "delivery",
            "name": "LLM Delivery",
            "score": None,
            "coverage": "sufficient",
            "insufficient_signal": True,
            "rationale": "They declined to discuss it.",
            "evidence": [],
        }],
        "summary": "Thin.",
        "recommendation": "insufficient_signal",
    })

    assert report.competency_scores[0].coverage == CoverageLevel.INSUFFICIENT


# -- red flags ---------------------------------------------------------------


def test_an_unevidenced_red_flag_is_dropped():
    """A red flag with no quote is an accusation, not a finding."""
    report = report_from({
        "competency_scores": [],
        "red_flags_observed": [
            {"description": "Could not explain hallucination handling", "evidence": []}
        ],
        "summary": "…",
        "recommendation": "mixed",
    })

    assert report.red_flags_observed == []


def test_an_evidenced_red_flag_is_kept():
    report = report_from({
        "competency_scores": [],
        "red_flags_observed": [{
            "description": "Deferred to legal without a position",
            "evidence": [{"turn_index": 3, "text": "Legal wanted a blanket disclaimer"}],
        }],
        "summary": "…",
        "recommendation": "mixed",
    })

    assert len(report.red_flags_observed) == 1
    assert report.red_flags_observed[0].evidence[0].turn_index == 3


# -- the recommendation is a signal, never a verdict -------------------------


def test_a_confident_signal_is_refused_when_most_of_the_spec_went_unassessed():
    report = report_from({
        "competency_scores": [{
            "competency_id": "delivery",
            "name": "LLM Delivery",
            "score": 5.0,
            "coverage": "sufficient",
            "rationale": "Excellent.",
            "evidence": [{"turn_index": 1, "text": "We shipped a retrieval assistant"}],
        }],
        "summary": "Strong on what we covered.",
        "recommendation": "strong_evidence_for",
    })

    # One of two competencies assessed: whatever was seen, the interview did not
    # gather enough to support a confident signal.
    assert report.recommendation == RecommendationSignal.LIMITED_EVIDENCE


def test_nothing_assessed_means_insufficient_signal():
    report = report_from({
        "competency_scores": [],
        "summary": "…",
        "recommendation": "strong_evidence_for",
    })

    assert report.recommendation == RecommendationSignal.INSUFFICIENT_SIGNAL


def test_a_report_is_never_a_decision():
    report = report_from({"competency_scores": [], "summary": "…", "recommendation": "mixed"})
    assert report.is_decision is False


# -- silence -----------------------------------------------------------------


async def test_a_silent_interview_is_not_scored_at_all():
    """Asking a model to score silence invites it to fill the silence in."""
    from feedback.score import FeedbackScorer

    silent = transcript(("interviewer", "Tell me about yourself."))
    scorer = FeedbackScorer.__new__(FeedbackScorer)  # no client needed; never called
    report = await FeedbackScorer.score(
        scorer,
        interview_id="int_1",
        blueprint_id="cand_1",
        spec=SPEC,
        transcript=silent,
    )

    assert report.recommendation == RecommendationSignal.INSUFFICIENT_SIGNAL
    assert all(s.score is None for s in report.competency_scores)
    assert "says nothing about them" in report.summary


# -- declining is not a low score --------------------------------------------


def test_declining_is_carried_as_missing_signal_not_as_weakness():
    """"Declined to answer" and "answered poorly" say different things about a
    person, and only one of them is about their ability."""
    report = report_from({
        "competency_scores": [{
            "competency_id": "stakeholders",
            "name": "Stakeholders",
            "score": None,
            "coverage": "not_started",
            "insufficient_signal": True,
            "rationale": "The candidate declined to answer questions in this area.",
            "evidence": [],
        }],
        "summary": "…",
        "recommendation": "insufficient_signal",
    })

    stakeholders = next(s for s in report.competency_scores if s.competency_id == "stakeholders")
    assert stakeholders.score is None
    assert "Stakeholders" in " ".join(report.coverage_gaps)


# -- judgement health --------------------------------------------------------
#
# The same problem ConversationHealth solves for audio, one layer up. When the
# off-path judge fails, no claims are extracted, and a report with no claims is
# indistinguishable from a report on a candidate who said nothing specific. The
# failure must be stated and must cap the signal, without casting any doubt on
# the claims that did survive.


def judged(succeeded_in=(), failed_in=(), *, attempted=None, spec=SPEC):
    """Build JudgmentHealth the way the scorer will: from session metrics."""
    events = [{"kind": "judgment", "section": s} for s in succeeded_in]
    events += [{"kind": "judgment_failed", "section": s} for s in failed_in]
    metrics = {
        "judgments_attempted": len(events) if attempted is None else attempted,
        "judgments_succeeded": len(succeeded_in),
        "judgments_failed": len(failed_in),
        "brain_events": events,
    }
    return assess_judgment(metrics, spec)


FULL_MARKS = {
    "competency_scores": [
        {
            "competency_id": "delivery",
            "name": "LLM Delivery",
            "score": 5.0,
            "coverage": "sufficient",
            "rationale": "Shipped and measured it.",
            "evidence": [{"turn_index": 1, "text": "We shipped a retrieval assistant"}],
        },
        {
            "competency_id": "stakeholders",
            "name": "Stakeholders",
            "score": 4.0,
            "coverage": "sufficient",
            "rationale": "Held a line and found a middle.",
            "evidence": [{"turn_index": 3, "text": "we agreed on a confidence threshold instead"}],
        },
    ],
    "summary": "Strong throughout.",
    "recommendation": "strong_evidence_for",
}


def test_no_judgement_failures_leaves_the_report_exactly_as_it_is_today():
    """The common case must not move. A healthy session gains nothing but a record."""
    baseline = report_from(FULL_MARKS)
    healthy = report_from(FULL_MARKS, judgment_health=judged(succeeded_in=["delivery", "stakeholders"]))

    assert baseline.recommendation == RecommendationSignal.STRONG_EVIDENCE_FOR
    assert healthy.recommendation == RecommendationSignal.STRONG_EVIDENCE_FOR
    assert healthy.judgment_health.degraded is False
    assert healthy.judgment_health.as_sentence is None
    assert [s.score for s in healthy.competency_scores] == [s.score for s in baseline.competency_scores]


def test_every_judgement_failing_degrades_the_report_and_caps_the_signal():
    report = report_from(
        FULL_MARKS, judgment_health=judged(failed_in=["delivery", "stakeholders"])
    )

    assert report.judgment_health.degraded is True
    assert report.recommendation == RecommendationSignal.LIMITED_EVIDENCE

    sentence = report.judgment_health.as_sentence
    assert sentence
    assert "did not finish on our side" in sentence
    # It must not read as a remark about the person, and must not undermine the
    # evidence that is there.
    assert "checked against it" in sentence
    assert "—" not in sentence and "–" not in sentence  # PROSE_STYLE


def test_a_partial_failure_keeps_the_surviving_claims_and_still_flags_the_gap():
    """Losing one competency's analysis does not make the rest suspect."""
    report = report_from(
        FULL_MARKS,
        judgment_health=judged(succeeded_in=["delivery"], failed_in=["stakeholders"]),
    )

    # Everything the model produced for the competency that was judged survives
    # untouched: same score, same verified quote.
    delivery = next(s for s in report.competency_scores if s.competency_id == "delivery")
    assert delivery.score == 5.0
    assert delivery.evidence and delivery.evidence[0].turn_index == 1

    assert report.judgment_health.degraded is True
    assert report.judgment_health.unjudged_competencies == ["Stakeholders"]
    sentence = report.judgment_health.as_sentence
    assert "Stakeholders" in sentence
    # Names the topic that is missing, and nothing else.
    assert "LLM Delivery" not in sentence


def test_a_competency_that_failed_once_but_succeeded_once_is_not_counted_as_lost():
    """One successful judgement means claims were extracted. That is enough."""
    health = judged(succeeded_in=["delivery", "stakeholders"], failed_in=["stakeholders"])

    assert health.unjudged_competencies == []
    assert health.unjudged_weight == 0.0
    assert health.degraded is False


def test_losing_a_light_competency_does_not_degrade_the_report():
    """Weight, not count. Losing a tenth of the spec is not losing the picture."""
    spec = EvaluationSpec(
        role_title="Lead Product Manager",
        seniority="Senior",
        experience_expectation="10+ years",
        duration_minutes=40,
        competencies=[
            Competency(id="delivery", name="LLM Delivery", description="Ships.", weight=0.6),
            Competency(id="stakeholders", name="Stakeholders", description="Navigates.", weight=0.3),
            Competency(id="mentoring", name="Mentoring", description="Coaches.", weight=0.1),
        ],
    )
    light = judged(succeeded_in=["delivery", "stakeholders"], failed_in=["mentoring"], spec=spec)
    heavy = judged(succeeded_in=["stakeholders", "mentoring"], failed_in=["delivery"], spec=spec)

    assert light.unjudged_weight == 0.1
    assert light.degraded is False, "a tenth of the spec is not a degraded report"
    assert heavy.unjudged_weight == 0.6
    assert heavy.degraded is True


def test_cancelled_judgements_are_not_failures():
    """Work stopped at teardown is routine. Counting it as a fault would flag
    every healthy interview, and a signal that fires on everything decides
    nothing."""
    health = judged(succeeded_in=["delivery", "stakeholders"], attempted=5)

    assert health.cancelled == 3
    assert health.failed == 0
    assert health.degraded is False


def test_an_interview_recorded_before_any_of_this_is_not_flagged():
    """Absent counters mean we do not know, and inventing a fault is worse."""
    health = assess_judgment({}, SPEC)

    assert health.attempted == 0
    assert health.degraded is False
    assert health.as_sentence is None


def test_degraded_audio_and_degraded_judgement_do_not_hide_each_other():
    """Two independent failures. Neither may silently stand in for the other."""
    audio = ConversationHealth(candidate_turns=8, repair_requests=3, disconnects=1)
    analysis = judged(failed_in=["delivery", "stakeholders"])
    assert audio.degraded and analysis.degraded

    report = report_from(FULL_MARKS, health=audio, judgment_health=analysis)

    # Both survive on the report, separately, with their own sentences.
    assert report.conversation_health.degraded is True
    assert report.judgment_health.degraded is True
    assert report.conversation_health.as_sentence != report.judgment_health.as_sentence
    assert "recording was poor" in report.conversation_health.as_sentence
    assert "our own analysis" in report.judgment_health.as_sentence

    # And the cap is reached once, not twice over.
    assert report.recommendation == RecommendationSignal.LIMITED_EVIDENCE


def test_each_degradation_caps_the_signal_on_its_own():
    audio_only = report_from(
        FULL_MARKS,
        health=ConversationHealth(candidate_turns=8, repair_requests=3, disconnects=1),
    )
    analysis_only = report_from(
        FULL_MARKS, judgment_health=judged(failed_in=["delivery", "stakeholders"])
    )

    assert audio_only.recommendation == RecommendationSignal.LIMITED_EVIDENCE
    assert audio_only.judgment_health is None
    assert analysis_only.recommendation == RecommendationSignal.LIMITED_EVIDENCE
    assert analysis_only.conversation_health is None


# -- the sentence is written once, in Python, and sent ------------------------
#
# It used to be written twice, here and in the panel's JavaScript, and the two
# drifted: different wording for echo and for silences, a different closing
# line, and a disagreement about the empty case, where Python says nothing and
# the panel asserted the candidate "could not be heard clearly". So the scoring
# model was told one thing and the employer read another. Now the report carries
# the sentence and the panel renders what it is given.


def test_both_sentences_serialise_into_the_report_json():
    """A plain method does not serialise. That is why the panel wrote its own."""
    report = report_from(
        FULL_MARKS,
        health=ConversationHealth(candidate_turns=8, repair_requests=3, disconnects=1),
        judgment_health=judged(failed_in=["delivery", "stakeholders"]),
    )

    payload = report.model_dump(mode="json")

    assert payload["conversation_health"]["as_sentence"].startswith("The recording was poor")
    assert payload["judgment_health"]["as_sentence"].startswith("None of our own analysis")
    # And the flag the panel guards on still travels with it.
    assert payload["conversation_health"]["degraded"] is True
    assert payload["judgment_health"]["degraded"] is True


def test_a_healthy_report_carries_no_sentence_in_its_json():
    report = report_from(
        FULL_MARKS,
        health=ConversationHealth(candidate_turns=8),
        judgment_health=judged(succeeded_in=["delivery", "stakeholders"]),
    )

    payload = report.model_dump(mode="json")

    assert payload["conversation_health"]["degraded"] is False
    assert payload["conversation_health"]["as_sentence"] is None
    assert payload["judgment_health"]["degraded"] is False
    assert payload["judgment_health"]["as_sentence"] is None


def test_the_sentence_survives_being_stored_and_read_back():
    """Reports are JSON in a column. The sentence has to come back with them."""
    from shared.contracts import FeedbackReport

    stored = report_from(
        FULL_MARKS,
        health=ConversationHealth(candidate_turns=8, repair_requests=3, disconnects=1),
    ).model_dump(mode="json")

    revived = FeedbackReport.model_validate(stored)

    assert revived.conversation_health.as_sentence == (
        report_from(
            FULL_MARKS,
            health=ConversationHealth(candidate_turns=8, repair_requests=3, disconnects=1),
        ).conversation_health.as_sentence
    )
    assert "The recording was poor" in revived.model_dump(mode="json")[
        "conversation_health"
    ]["as_sentence"]


def test_the_panel_no_longer_builds_a_sentence_of_its_own():
    """The drift this change exists to end. Inspected, not executed, the way
    test_copy_style.py already checks panel copy."""
    from pathlib import Path

    panel = (Path(__file__).resolve().parents[2] / "frontend" / "admin" / "admin.js").read_text()

    assert "function healthSentence" not in panel
    # The wordings that drifted must not reappear anywhere in the panel.
    for phrase in (
        "The recording was poor",
        "could not be heard clearly",
        "rather than headphones",
        "long enough to be prompted",
    ):
        assert phrase not in panel, f"the panel is composing prose again: {phrase!r}"
    # It reads what it is sent instead.
    assert "as_sentence" in panel
