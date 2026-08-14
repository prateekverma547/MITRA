"""Tests for blueprint loading and the prompt rendered from it.

These guard the Milestone 3 seam. The bot must be driven entirely by blueprint
data, so that swapping the fixture for a generated blueprint changes nothing in
consuming code. A test that asserts on hardcoded role strings inside the bot
would defeat the point — so these assert that the *data* reaches the prompt.
"""

import pytest

from bot.blueprint_source import load_blueprint
from bot.persona import build_system_instruction
from shared.contracts import (
    Competency,
    CompetencyPlan,
    EvaluationSpec,
    InterviewBlueprint,
)


def test_fixture_loads_and_validates_against_the_contract():
    blueprint = load_blueprint()

    assert isinstance(blueprint, InterviewBlueprint)
    assert blueprint.role_title == "Senior Product Manager"
    assert blueprint.evaluation_spec.experience_expectation == "10-11 years in product management"
    assert blueprint.schema_version == 1


def test_fixture_covers_every_required_competency():
    blueprint = load_blueprint()
    covered = {plan.competency_id for plan in blueprint.competency_plans}
    declared = {c.id for c in blueprint.evaluation_spec.competencies}

    # A competency the spec asks for but the blueprint has no plan for would be
    # silently never interviewed.
    assert covered == declared
    assert len(covered) == 6


def test_every_competency_has_seed_questions_and_a_depth_target():
    for plan in load_blueprint().competency_plans:
        assert len(plan.seed_questions) >= 3, plan.competency_id
        assert plan.target_depth.strip(), plan.competency_id


def test_time_budgets_fit_the_interview_duration():
    blueprint = load_blueprint()
    planned = sum(p.time_budget_minutes for p in blueprint.competency_plans)

    assert planned <= blueprint.total_duration_minutes


def test_every_shipped_fixture_spends_its_whole_duration():
    """Overcommitment is caught by the contract; undercommitment was not.

    A plan summing to 10 minutes of a 20 minute interview hands half the
    employer's time back without saying so. `allocate_minutes` settles its
    rounding onto the exact total, so a generated blueprint cannot reach it, and
    the only way in is a hand-authored fixture — which is what these are, and
    which is how every measurement in this repo is taken.

    **Tested here rather than on the contract deliberately.** As a validator it
    failed 49 tests that build small blueprints to exercise something else, none
    of them malformed, and a rule that makes every future test balance minutes
    it does not care about is a rule that gets worked around. Overcommitment is
    a plan that cannot be executed; undercommitment is one that is merely
    wasteful, and they do not deserve the same enforcement.
    """
    from bot.blueprint_source import FIXTURES_DIR

    for path in sorted(FIXTURES_DIR.glob("*.json")):
        blueprint = load_blueprint(blueprint_id=path.stem)
        planned = (
            blueprint.opening_minutes
            + blueprint.closing_minutes
            + sum(p.time_budget_minutes for p in blueprint.competency_plans)
        )
        duration = blueprint.evaluation_spec.duration_minutes
        assert abs(planned - duration) < 0.1, (
            f"{path.stem} budgets {planned:g} of {duration} minutes"
        )


def test_unknown_blueprint_fails_loudly_and_lists_alternatives():
    with pytest.raises(FileNotFoundError) as exc:
        load_blueprint(blueprint_id="does_not_exist")

    assert "pm_senior" in str(exc.value)


def test_prompt_names_the_role_and_forbids_inventing_another():
    prompt = build_system_instruction(load_blueprint())

    assert "Senior Product Manager" in prompt
    assert "10-11 years in product management" in prompt
    # The off-topic redirect and the anti-drift rule are DoD requirements.
    assert "bring it back" in prompt
    assert "never invent one" in prompt.lower()


def test_prompt_carries_every_competency_and_its_depth_target():
    blueprint = load_blueprint()
    prompt = build_system_instruction(blueprint)

    for plan in blueprint.competency_plans:
        assert plan.name in prompt
        assert plan.target_depth in prompt
        for question in plan.seed_questions:
            assert question in prompt


def test_prompt_is_driven_by_data_not_hardcoded():
    """Swap the blueprint, and the interview changes with zero code edits."""
    other = InterviewBlueprint(
        blueprint_id="test-sre",
        evaluation_spec=EvaluationSpec(
            role_title="Staff Site Reliability Engineer",
            seniority="Staff",
            experience_expectation="12+ years running production systems",
            competencies=[
                Competency(id="oncall", name="Incident response", description="x", weight=1.0)
            ],
        ),
        competency_plans=[
            CompetencyPlan(
                competency_id="oncall",
                name="Incident response",
                target_depth="Can narrate a real outage they were paged for.",
                seed_questions=["Tell me about the worst outage you have handled."],
                time_budget_minutes=10,
            )
        ],
        suggested_opening="Greet them and say this is for a Staff SRE role.",
    )

    prompt = build_system_instruction(other)

    assert "Staff Site Reliability Engineer" in prompt
    assert "Tell me about the worst outage you have handled." in prompt
    assert "Product Manager" not in prompt


def test_candidate_context_appears_only_when_a_cv_is_attached():
    """Milestone 1 has no CV; Milestone 2 will supply one."""
    role_only = load_blueprint()
    assert role_only.candidate_summary is None
    assert "What you know about this candidate" not in build_system_instruction(role_only)

    with_cv = role_only.model_copy(
        update={"candidate_summary": "Twelve years in fintech product."}
    )
    assert "What you know about this candidate" in build_system_instruction(with_cv)
