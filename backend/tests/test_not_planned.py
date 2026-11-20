"""C-A22 (Session A, round 16): a plan sized to a shorter booking (C-172).

Requirements: `InterviewBlueprint.not_planned: list[NotPlannedArea]`, each
{competency_id, name, reason: "booked_length"}; a spec competency with no planned area
validates only when it is listed there; additive (old plans load unchanged). The
plans are the real fixtures the bot runs.
"""

import json
from pathlib import Path

import pytest

from shared.contracts import InterviewBlueprint, NotPlannedArea

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = sorted((BACKEND / "bot" / "fixtures").glob("*.json"))


def pm():
    return json.loads((BACKEND / "bot" / "fixtures" / "pm_senior.json").read_text())


def sized_down(raw, keep: int):
    """The plan with only its first `keep` areas, the rest dropped (as a short booking would)."""
    dropped = raw["competency_plans"][keep:]
    raw["competency_plans"] = raw["competency_plans"][:keep]
    return raw, dropped


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_old_plans_load_with_nothing_listed(path):
    raw = json.loads(path.read_text())
    assert "not_planned" not in raw
    assert InterviewBlueprint.model_validate(raw).not_planned == []


def test_a_plan_sized_to_a_short_booking_validates_when_the_dropped_areas_are_listed():
    raw, dropped = sized_down(pm(), keep=3)
    raw["not_planned"] = [{"competency_id": p["competency_id"], "name": p["name"], "reason": "booked_length"}
                          for p in dropped]
    plan = InterviewBlueprint.model_validate(raw)
    assert [n.competency_id for n in plan.not_planned] == [p["competency_id"] for p in dropped]
    assert len(plan.competency_plans) == 3
    again = InterviewBlueprint.model_validate(plan.model_dump(mode="json"))
    assert again.not_planned == plan.not_planned
    # The bot builds its prompt from the shorter plan (Session A's code, read only).
    from bot.realtime_prompt import build_base_instruction

    assert build_base_instruction(plan, first_name="Asha", time_of_day="morning")


def test_a_dropped_area_that_is_not_listed_is_still_refused():
    raw, dropped = sized_down(pm(), keep=3)
    with pytest.raises(ValueError, match="In spec but unplanned"):
        InterviewBlueprint.model_validate(raw)
    raw["not_planned"] = [{"competency_id": dropped[0]["competency_id"], "name": dropped[0]["name"],
                           "reason": "booked_length"}]  # one listed, the others not
    with pytest.raises(ValueError, match="In spec but unplanned"):
        InterviewBlueprint.model_validate(raw)


def test_listed_and_planned_at_once_is_refused():
    raw = pm()
    first = raw["competency_plans"][0]
    raw["not_planned"] = [{"competency_id": first["competency_id"], "name": first["name"], "reason": "booked_length"}]
    with pytest.raises(ValueError, match="both plans and lists"):
        InterviewBlueprint.model_validate(raw)


def test_listing_something_outside_the_spec_or_twice_is_refused():
    raw, dropped = sized_down(pm(), keep=5)
    entry = {"competency_id": dropped[0]["competency_id"], "name": dropped[0]["name"], "reason": "booked_length"}
    with pytest.raises(ValueError, match="not in the spec"):
        InterviewBlueprint.model_validate({**raw, "not_planned": [entry, {**entry, "competency_id": "invented"}]})
    with pytest.raises(ValueError, match="twice"):
        InterviewBlueprint.model_validate({**raw, "not_planned": [entry, entry]})


def test_the_only_reason_is_the_booked_length():
    with pytest.raises(ValueError):
        NotPlannedArea(competency_id="x", name="X", reason="ran_out_of_time")
    assert NotPlannedArea(competency_id="x", name="X", reason="booked_length").reason == "booked_length"
