"""Feedback scoring — deterministic suite, no LLM calls.

`build_report` is pure, so everything that makes a report trustworthy can be
tested without the network: quotes are checked against the transcript, scores
that lose their evidence are downgraded, and nothing the candidate did not say
survives into a hiring record.

The tolerant, real-LLM half lives in `test_behavior.py`.
"""

import pytest

from feedback.score import build_report, verify_quote
from shared.contracts import (
    Competency,
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


def report_from(payload, tr=TRANSCRIPT, spec=SPEC):
    return build_report(
        interview_id="int_1",
        blueprint_id="cand_1",
        spec=spec,
        transcript=tr,
        payload=payload,
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
