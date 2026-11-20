"""C-A21 (Session A, A-99): an optional goal per plan question, additive (C-168).

Requirements: `CompetencyPlan.question_goals`, one plain-words goal per question in
`seed_questions` order (None for a question without one); old plans without it load
and run unchanged; a list that does not line up with the questions is refused.
The old plans are the real fixtures the bot runs (bot/fixtures/*.json).
"""

import json
from pathlib import Path

import pytest

from shared.contracts import CompetencyPlan, InterviewBlueprint

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = sorted((BACKEND / "bot" / "fixtures").glob("*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_old_plans_without_goals_still_load_and_run(path):
    raw = json.loads(path.read_text())
    assert all("question_goals" not in p for p in raw["competency_plans"])  # really an old plan
    plan = InterviewBlueprint.model_validate(raw)
    assert all(p.question_goals == [] and p.goal_of(0) is None for p in plan.competency_plans)
    # "Run": the bot's own loader and the realtime prompt build from it (Session A's code, read only).
    from bot.blueprint_source import load_blueprint
    from bot.realtime_prompt import build_base_instruction

    assert load_blueprint(blueprint_id=path.stem).blueprint_id == plan.blueprint_id
    assert build_base_instruction(plan, first_name="Asha", time_of_day="afternoon")


def _with_goals(goals):
    raw = json.loads((BACKEND / "bot" / "fixtures" / "pm_senior.json").read_text())
    first = raw["competency_plans"][0]
    first["question_goals"] = goals(len(first["seed_questions"]))
    return raw


def test_a_goal_per_question_loads_and_round_trips():
    raw = _with_goals(lambda n: ["Whether they set the strategy themselves", *([None] * (n - 1))])
    plan = InterviewBlueprint.model_validate(raw)
    first = plan.competency_plans[0]
    assert first.goal_of(0) == "Whether they set the strategy themselves" and first.goal_of(1) is None
    assert first.goal_of(99) is None
    again = InterviewBlueprint.model_validate(plan.model_dump(mode="json"))
    assert again.competency_plans[0].question_goals == first.question_goals


@pytest.mark.parametrize("count", [1, 99])
def test_goals_that_do_not_line_up_with_the_questions_are_refused(count):
    with pytest.raises(ValueError, match="one per question"):
        InterviewBlueprint.model_validate(_with_goals(lambda n: ["a goal"] * (count if count < n else n + 1)))


def test_the_field_name_is_question_goals():
    assert "question_goals" in CompetencyPlan.model_fields
    assert CompetencyPlan.model_fields["question_goals"].is_required() is False
