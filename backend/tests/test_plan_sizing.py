"""Round 16, part 2: the plan sized to the booked length (A-98), dropped areas listed in
C-172's not_planned. Deterministic."""

import json
from pathlib import Path
from types import SimpleNamespace

from blueprint.refine import MINUTES_PER_AREA, areas_that_fit, refine_for_booking, size_to_booking
from shared.contracts import HRFindingsForTechnical, InterviewBlueprint

FIX = Path(__file__).resolve().parents[1] / "bot" / "fixtures"
BP = InterviewBlueprint.model_validate(json.loads((FIX / "pm_senior.json").read_text()))


def test_the_area_count_follows_the_booking():
    counts = {m: len(size_to_booking(BP, m).competency_plans) for m in (20, 30, 40)}
    assert counts == {20: 4, 30: 6, 40: 6}
    assert areas_that_fit(BP, 20) == int((20 - BP.opening_minutes - BP.closing_minutes) // MINUTES_PER_AREA)


def test_the_least_central_areas_are_dropped_and_listed_as_not_planned():
    report = {}
    sized = size_to_booking(BP, 20, report=report)
    weights = {c.id: c.weight for c in BP.evaluation_spec.competencies}
    kept = [p.competency_id for p in sized.competency_plans]
    dropped = [n.competency_id for n in sized.not_planned]
    assert min(weights[k] for k in kept) >= max(weights[d] for d in dropped)
    assert all(n.reason == "booked_length" for n in sized.not_planned) and report["dropped"] == dropped
    assert kept == [p.competency_id for p in BP.competency_plans if p.competency_id in kept], "plan order kept"


def test_ids_follow_position_and_budgets_fit():
    sized = size_to_booking(BP, 20)
    for i, p in enumerate(sized.competency_plans):
        assert all(q.startswith(f"Q{i + 2}.") for q in p.question_ids)
    total = sum(p.time_budget_minutes for p in sized.competency_plans) + sized.opening_minutes + sized.closing_minutes
    assert abs(total - sized.evaluation_spec.duration_minutes) < 0.1
    InterviewBlueprint.model_validate(sized.model_dump(mode="json"))


def test_a_plan_that_fits_is_returned_unchanged():
    assert size_to_booking(BP, 40) is BP


async def test_an_hr_probed_area_is_kept_ahead_of_a_heavier_one_and_its_ids_follow():
    lowest = min(BP.evaluation_spec.competencies, key=lambda c: c.weight).id

    async def request(**kw):
        if "findings" in kw["response_model"].model_fields:
            out = [{"index": 0, "outcome": "probed", "area_id": lowest,
                    "probe": "Which number did you watch most closely, and why that one?"}]
            return SimpleNamespace(payload={"findings": out}, record=None)
        qids = [line.split(":")[0] for line in kw["messages"][1]["content"].split("\n") if line[:1] == "Q"]
        return SimpleNamespace(payload={"goals": [{"id": q, "goal": f"Learn the story behind item {q}"} for q in qids]},
                               record=None)

    findings = HRFindingsForTechnical(candidate_id="c", findings_for_technical=["Vague about which metric they owned."])
    report = {}
    plan = await refine_for_booking(BP, findings, booked_minutes=20, request=request, report=report)
    kept = [p.competency_id for p in plan.competency_plans]
    assert lowest in kept, "the HR-probed area is kept"
    [use] = plan.hr_findings.findings
    area = next(i for i, p in enumerate(plan.competency_plans) if p.competency_id == lowest)
    assert use.question_id in plan.competency_plans[area].question_ids, "the HR record's id follows the renumbering"
    assert all(len(p.question_goals) == len(p.seed_questions) for p in plan.competency_plans), "goals after sizing"
    assert report["sizing"]["dropped"] and report["goals"]["written"] > 0


async def test_every_plan_gets_goals_at_booking_with_or_without_hr_findings():
    async def request(**kw):
        qids = [line.split(":")[0] for line in kw["messages"][1]["content"].split("\n") if line[:1] == "Q"]
        return SimpleNamespace(payload={"goals": [{"id": q, "goal": f"Learn the story behind item {q}"} for q in qids]},
                               record=None)

    plan = await refine_for_booking(BP, None, booked_minutes=40, request=request)
    assert plan.not_planned == [] and all(p.goal_of(0) for p in plan.competency_plans)
