"""Deterministic tests for blueprint assembly and time budgeting.

No LLM. The model's job is judgement — which claims to test, what to ask. This
suite covers the parts that must be *exactly* right regardless of what the model
returns: budgets that sum to the configured duration, every competency planned,
and a blueprint that survives contract validation.

That split is deliberate. The contract validator rejects a blueprint whose
sections overrun the interview, so if the model were asked to produce minutes
that sum exactly, a plausible-looking arithmetic slip would fail generation
entirely.
"""

import pytest

from blueprint.generate import (
    CLOSING_MINUTES,
    MIN_SECTION_MINUTES,
    OPENING_MINUTES,
    BlueprintGenerationError,
    allocate_minutes,
    build_blueprint,
)
from shared.contracts import Competency, EvaluationSpec, InterviewBlueprint


def spec_with(*, duration: int = 40, weights: dict[str, float] | None = None) -> EvaluationSpec:
    weights = weights or {"alpha": 0.5, "beta": 0.3, "gamma": 0.2}
    return EvaluationSpec(
        role_title="Senior Product Manager",
        seniority="Senior",
        experience_expectation="10+ years",
        duration_minutes=duration,
        competencies=[
            Competency(id=cid, name=cid.title(), description=f"{cid} matters", weight=w)
            for cid, w in weights.items()
        ],
    )


def payload_for(spec: EvaluationSpec, *, emphasis: dict[str, float] | None = None) -> dict:
    emphasis = emphasis or {}
    return {
        "candidate_name": "Priya Raghavan",
        "candidate_summary": "Eleven years in payments product.",
        "claims_to_verify": [{"claim": "Cut payment failure rates by 38%.", "source": "cv"}],
        "suggested_opening": "Hello, this is an interview for the Senior PM role.",
        "interviewing_guidance": ["Push past frameworks to actual decisions."],
        "competency_plans": [
            {
                "competency_id": c.id,
                "name": c.name,
                "target_depth": f"Deep on {c.id}.",
                "emphasis": emphasis.get(c.id, 1.0),
                "seed_questions": [f"Tell me about {c.id}.", f"What went wrong with {c.id}?"],
            }
            for c in spec.competencies
        ],
    }


# -- time budgeting ----------------------------------------------------------


@pytest.mark.parametrize("duration", [20, 30, 40, 60, 90])
def test_budgets_always_sum_to_the_available_time(duration):
    """The contract rejects an over-budget blueprint, so this must be exact."""
    spec = spec_with(duration=duration)
    minutes = allocate_minutes(spec=spec, plans_by_id={})

    available = duration - OPENING_MINUTES - CLOSING_MINUTES
    assert sum(minutes.values()) == pytest.approx(available, abs=0.01)


def test_time_follows_employer_weight_when_emphasis_is_flat():
    spec = spec_with(weights={"alpha": 0.6, "beta": 0.2, "gamma": 0.2})
    minutes = allocate_minutes(spec=spec, plans_by_id=payload_by_id(spec))

    assert minutes["alpha"] > minutes["beta"]
    assert minutes["beta"] == pytest.approx(minutes["gamma"], abs=0.5)


def test_emphasis_shifts_time_toward_thin_evidence():
    """A competency the CV barely evidences needs more probing, not less.

    Same employer weights; only the model's emphasis differs.
    """
    spec = spec_with(weights={"alpha": 0.5, "beta": 0.5})
    flat = allocate_minutes(spec=spec, plans_by_id=payload_by_id(spec))
    weighted = allocate_minutes(
        spec=spec, plans_by_id=payload_by_id(spec, emphasis={"beta": 2.0})
    )

    assert weighted["beta"] > flat["beta"]
    assert weighted["alpha"] < flat["alpha"]
    # Still exact.
    assert sum(weighted.values()) == pytest.approx(sum(flat.values()), abs=0.01)


def test_every_competency_gets_a_usable_slot():
    """A competency the employer asked for must not be starved to a formality.

    An observed run allocated 1.0 minute to a low-weight competency — enough
    for one question and no follow-up, which cannot reach any useful depth.
    """
    spec = spec_with(
        duration=40, weights={"a": 0.45, "b": 0.30, "c": 0.15, "d": 0.07, "e": 0.03}
    )
    minutes = allocate_minutes(
        spec=spec, plans_by_id=payload_by_id(spec, emphasis={"a": 2.0})
    )

    assert min(minutes.values()) >= MIN_SECTION_MINUTES - 0.5
    assert sum(minutes.values()) == pytest.approx(
        spec.duration_minutes - OPENING_MINUTES - CLOSING_MINUTES, abs=0.01
    )
    # Weighting still applies above the floor.
    assert minutes["a"] > minutes["b"] > minutes["e"]


def test_too_many_competencies_for_the_clock_still_splits_evenly():
    """Over-specified spec: nobody gets a good slot, but nobody gets 30 seconds."""
    spec = spec_with(duration=20, weights={c: 1 / 7 for c in "abcdefg"})
    minutes = allocate_minutes(spec=spec, plans_by_id=payload_by_id(spec))

    available = 20 - OPENING_MINUTES - CLOSING_MINUTES
    assert sum(minutes.values()) == pytest.approx(available, abs=0.01)
    assert min(minutes.values()) >= 1.5


def test_emphasis_is_clamped_to_a_sane_range():
    """A model returning emphasis 50 must not starve every other competency."""
    spec = spec_with(weights={"alpha": 0.5, "beta": 0.5})
    minutes = allocate_minutes(
        spec=spec, plans_by_id=payload_by_id(spec, emphasis={"alpha": 50.0})
    )

    assert minutes["beta"] >= 5.0
    assert minutes["alpha"] / minutes["beta"] <= 4.5


def test_non_numeric_emphasis_falls_back_instead_of_crashing():
    spec = spec_with(weights={"alpha": 0.5, "beta": 0.5})
    plans = payload_by_id(spec)
    plans["alpha"]["emphasis"] = "very high"

    minutes = allocate_minutes(spec=spec, plans_by_id=plans)
    assert minutes["alpha"] == pytest.approx(minutes["beta"], abs=0.5)


def test_duration_too_short_for_opening_and_closing_is_rejected():
    spec = spec_with(duration=20)
    spec = spec.model_copy(update={"duration_minutes": 20})
    # Force an impossible case by shrinking below the reserved time.
    with pytest.raises(BlueprintGenerationError, match="no room"):
        allocate_minutes(
            spec=spec.model_copy(update={"duration_minutes": OPENING_MINUTES}),
            plans_by_id={},
        )


def payload_by_id(spec: EvaluationSpec, *, emphasis: dict[str, float] | None = None) -> dict:
    return {
        p["competency_id"]: p for p in payload_for(spec, emphasis=emphasis)["competency_plans"]
    }


# -- assembly ----------------------------------------------------------------


def test_generated_blueprint_validates_against_the_contract():
    spec = spec_with()
    blueprint = build_blueprint(
        blueprint_id="cand_1", spec=spec, payload=payload_for(spec)
    )

    assert isinstance(blueprint, InterviewBlueprint)
    assert blueprint.role_title == "Senior Product Manager"
    assert blueprint.candidate_name == "Priya Raghavan"
    assert len(blueprint.claims_to_verify) == 1
    # The whole plan fits the configured duration.
    planned = (
        blueprint.opening_minutes
        + blueprint.closing_minutes
        + sum(p.time_budget_minutes for p in blueprint.competency_plans)
    )
    assert planned <= spec.duration_minutes


def test_missing_competency_plan_is_rejected():
    """A competency with no plan would simply never be interviewed."""
    spec = spec_with()
    payload = payload_for(spec)
    payload["competency_plans"] = [
        p for p in payload["competency_plans"] if p["competency_id"] != "beta"
    ]

    with pytest.raises(BlueprintGenerationError, match="beta"):
        build_blueprint(blueprint_id="cand_1", spec=spec, payload=payload)


def test_competency_without_seed_questions_is_rejected():
    spec = spec_with()
    payload = payload_for(spec)
    payload["competency_plans"][0]["seed_questions"] = []

    with pytest.raises(BlueprintGenerationError, match="no seed questions"):
        build_blueprint(blueprint_id="cand_1", spec=spec, payload=payload)


def test_missing_opening_falls_back_rather_than_failing():
    """A missing nicety must not throw away an otherwise good blueprint."""
    spec = spec_with()
    payload = payload_for(spec)
    payload["suggested_opening"] = ""

    blueprint = build_blueprint(blueprint_id="cand_1", spec=spec, payload=payload)
    assert spec.role_title in blueprint.suggested_opening


def test_blueprint_is_consumable_by_the_brain():
    """The generated blueprint must drive the brain with no adaptation.

    This is the join between Milestone 2 and the brain built ahead of it — if
    generation produced something the brain could not run, the whole
    re-sequencing argument would collapse.
    """
    from bot.brain.brain import InterviewBrain

    spec = spec_with()
    blueprint = build_blueprint(blueprint_id="cand_1", spec=spec, payload=payload_for(spec))

    brain = InterviewBrain(blueprint)
    section_ids = [s.id for s in brain.sections]

    assert section_ids[0] == "opening"
    assert section_ids[-1] == "closing"
    assert set(section_ids[1:-1]) == {c.id for c in spec.competencies}
    assert brain.plan_turn().system_instruction
