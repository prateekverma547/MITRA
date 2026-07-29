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
