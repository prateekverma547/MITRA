"""Round 16, part 1: a GOAL per plan question, written at plan time (blueprint/refine.py
write_goals), stored in C-168's question_goals, and the only thing Mitra is shown.
Deterministic: a stand-in for the model call."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from blueprint.refine import GOALS_JOB, goal_problems, write_goals
from bot.realtime_prompt import area_count, build_area_block, build_area_instruction
from shared.contracts import InterviewBlueprint
from shared.text_models import TextModelError

BP = InterviewBlueprint.model_validate(json.loads(
    (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text()))
QUESTIONS = {qid: q for p in BP.competency_plans for qid, q in p.numbered_questions()}
GOOD = {"Q2.1": "Learn how they set a product strategy and the main bet it rested on"}


def words(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def stand_in(goals_by_attempt, calls=None):
    """goals_by_attempt[n]: {qid: goal} for attempt n (1-based); unknown ids get a good goal."""
    async def request(**kw):
        if calls is not None:
            calls.append(kw)
        n = len(calls) if calls is not None else 1
        chosen = goals_by_attempt.get(n, goals_by_attempt.get(1, {}))
        out = [{"id": qid, "goal": chosen.get(qid, f"Learn what lies behind topic {i} in their own experience")}
               for i, qid in enumerate(QUESTIONS)]
        return SimpleNamespace(payload={"goals": out},
                               record=SimpleNamespace(to_dict=lambda: {"job": "blueprint", "cost_usd": 0.0}))
    return request


async def test_every_question_gets_a_goal_and_nothing_else_changes():
    usage, report = [], {}
    plan = await write_goals(BP, request=stand_in({1: GOOD}), usage=usage, report=report)
    assert report["written"] == len(QUESTIONS) and report["fallbacks"] == {}
    for before, after in zip(BP.competency_plans, plan.competency_plans):
        assert after.seed_questions == before.seed_questions and after.question_ids == before.question_ids
        assert after.competency_id == before.competency_id and len(after.question_goals) == len(after.seed_questions)
    assert plan.competency_plans[0].goal_of(0) == GOOD["Q2.1"]
    assert usage == [{"job": GOALS_JOB, "cost_usd": 0.0}], "labelled apart from generation (C-158)"


async def test_a_goal_that_copies_the_question_is_retried_then_falls_back():
    copied = {"Q2.1": "Learn " + QUESTIONS["Q2.1"].rstrip("?").lower()}
    calls, report = [], {}
    plan = await write_goals(BP, request=stand_in({1: copied, 2: copied}, calls), report=report)
    assert len(calls) == 2 and "Q2.1" in calls[1]["messages"][-1]["content"]
    assert plan.competency_plans[0].goal_of(0) is None, "never a goal that can be read out as the question"
    assert "Q2.1" in report["fallbacks"] and any("copies" in p or "close" in p for p in report["fallbacks"]["Q2.1"])


async def test_a_retry_that_fixes_the_goal_is_accepted():
    calls = []
    plan = await write_goals(BP, request=stand_in({1: {"Q2.1": "What was the bet?"}, 2: GOOD}, calls))
    assert len(calls) == 2 and plan.competency_plans[0].goal_of(0) == GOOD["Q2.1"]


def test_the_goal_checks_catch_each_kind():
    q = QUESTIONS["Q2.1"]
    assert "is a question" in goal_problems("Learn what the bet was?", q)
    assert any("copies" in p for p in goal_problems("Learn the bet behind a product strategy you personally set", q))
    assert any("not neutral" in p for p in goal_problems("Learn what HR flagged about their strategy", q))
    assert any("words" in p for p in goal_problems("Learn " + "really " * 30 + "much", q))
    assert goal_problems(GOOD["Q2.1"], q) == []


async def test_a_failed_model_call_leaves_every_goal_empty_and_never_raises():
    async def failing(**kw):
        raise TextModelError("down")

    report = {}
    plan = await write_goals(BP, request=failing, report=report)
    assert all(g is None for p in plan.competency_plans for g in p.question_goals)
    assert "goal writing failed" in report["error"] and len(report["fallbacks"]) == len(QUESTIONS)


async def test_mitra_is_shown_the_goal_only_and_never_the_plans_wording():
    """Round 17 (Karandeep, v1): each line is the goal and nothing else. No run of five
    words from any plan question appears anywhere in her area block, and no reference label
    (round 16's v3, which she read out: A-104)."""
    plan = await write_goals(BP, request=stand_in({1: GOOD}))
    for area in range(2, 2 + len(plan.competency_plans)):
        block = build_area_block(plan, area)
        flat = " ".join(words(block))
        assert "reference for the meaning" not in block and "never to say" not in block
        for q in QUESTIONS.values():
            w = words(q)
            assert not any(" ".join(w[k:k + 5]) in flat for k in range(len(w) - 4)), (area, q)
    line = next(line for line in build_area_block(plan, 2).splitlines() if line.strip().startswith("Q2.1 "))
    assert line.strip() == f"Q2.1 {GOOD['Q2.1']}", "the goal, exactly, and nothing after it"


async def test_a_question_without_a_goal_falls_back_to_round_15s_form():
    plan = await write_goals(BP, request=stand_in({1: {"Q2.1": "What was the bet?"}, 2: {"Q2.1": "What was the bet?"}}))
    block = build_area_block(plan, 2)
    assert "Q2.1 Find out: what was the bet behind" in block
    assert "Q2.2 Learn" in block


async def test_the_auditor_reads_the_original_wording_and_the_goal():
    """Round 18 (A-115): Mitra is shown only goals, so the auditor matches her turns against
    the plan's wording AND each question's goal."""
    from bot.auditor import RealtimeAuditor

    plan = await write_goals(BP, request=stand_in({1: GOOD}))
    seen = []

    async def request(**kw):
        seen.append(kw["messages"][0]["content"] + kw["messages"][1]["content"])
        return SimpleNamespace(payload={"coverage": "partial", "label": "plan", "question_id": "Q2.1",
                                        "new_claims": [], "contradictions": []}, record=SimpleNamespace(cost_usd=0.0))

    a = RealtimeAuditor(blueprint=plan, area_now=lambda: 2, live=None, request=request)
    a.start()
    await a.label_now(0, "So what bet was your strategy really resting on?")
    await a.finish(timeout=5)
    assert QUESTIONS["Q2.1"] in seen[0] and GOOD["Q2.1"] in seen[0]
