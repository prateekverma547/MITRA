"""The plan generator's "one ask" rule (A-34), and the shared sample plan.

The counter in blueprint/generate.py is a copy of Session A's deterministic one
(tests/test_realtime_ending.py `asks`). This file does not import Session A's test
file (it failed to load whenever A edited it): it holds the copy to its own table
of expected counts, `EXPECTED_ASKS`, written from the counter's rules and checked
equal to A's counter on 2026-11-19 (C-149). Then the prompt rule, the code
backstop, and the sample plan.
"""

import json
from pathlib import Path

import pytest

from blueprint import generate, refine
from blueprint.generate import asks, build_blueprint, first_ask
from shared.contracts import InterviewBlueprint

BACKEND = Path(__file__).resolve().parents[1]
SAMPLE = BACKEND / "shared" / "fixtures" / "interview_blueprint.json"
PM = BACKEND / "bot" / "fixtures" / "pm_senior.json"

TWO_PART = [
    "Tell me about a product strategy you personally set or substantially shaped. What was the bet?",
    "Who have you mentored, and what changed for them?",
    "What went wrong during that launch, and what did you personally do about it?",
    "Walk me through the hardest prioritization call you have made. What were the options?",
]
ONE_ASK = [
    "What was the bet behind a strategy you set?",
    "What was the single metric you held yourself to, and why that one?",
    "Which Meridian Pay initiative best shows you leading people who did not report to you?",
]


#: The counter's rules, one case each or more: a sentence ending "?" is an ask; a
#: sentence opening "tell me / walk me / describe / ..." is an ask; ", and what/how/
#: who..." joins a second ask; ", and why (that one)?" at the end is the same ask's
#: reason (A-17). Checked equal to Session A's counter when written (C-149).
EXPECTED_ASKS = {
    **{q: 2 for q in TWO_PART},
    **{q: 1 for q in ONE_ASK},
    "Describe a launch you led.": 1,
    "Tell me about it. Describe the result. What changed?": 3,
    "How did you decide, and why?": 1,
    "What did you ship, and how, and who approved it?": 3,
    "I led the team.": 0,
}


@pytest.mark.parametrize("q", list(EXPECTED_ASKS))
def test_the_copy_counts_as_the_table_says(q):
    assert asks(q) == EXPECTED_ASKS[q]


def test_every_fixture_question_is_one_ask_by_the_copy():
    for path in (BACKEND / "bot" / "fixtures").glob("*.json"):
        for p in json.loads(path.read_text())["competency_plans"]:
            for q in p["seed_questions"]:
                assert asks(q) == 1, q


def test_first_ask_keeps_the_first_ask_verbatim_and_leaves_one_ask_alone():
    assert first_ask("Who have you mentored, and what changed for them?") == "Who have you mentored?"
    assert first_ask(TWO_PART[0]) == "Tell me about a product strategy you personally set or substantially shaped."
    for q in ONE_ASK:
        assert first_ask(q) == q
    for q in TWO_PART:
        assert asks(first_ask(q)) == 1


def test_the_prompts_ask_for_one_ask_not_one_question_mark():
    for system in (generate.SYSTEM, refine.SYSTEM):
        flat = " ".join(system.split())  # the prompts wrap lines
        assert "exactly one question mark" not in flat
        assert "ONE thing" in flat
        assert "Who disagreed, and what did it cost?" in flat


def test_build_blueprint_cuts_a_two_part_question_and_reports_it():
    bp = InterviewBlueprint.model_validate(json.loads(PM.read_text()))
    payload = {"competency_plans": [
        {"competency_id": p.competency_id, "name": p.name, "target_depth": p.target_depth,
         "seed_questions": list(p.seed_questions), "emphasis": 1.0} for p in bp.competency_plans],
        "suggested_opening": "Hello."}
    payload["competency_plans"][0]["seed_questions"][0] = TWO_PART[1]
    report: dict = {}
    built = build_blueprint(blueprint_id="b", spec=bp.evaluation_spec, payload=payload, report=report)
    assert report["split"] == 1 and TWO_PART[1] in report["raw_questions"]
    assert built.competency_plans[0].seed_questions[0] == "Who have you mentored?"
    assert all(asks(q) == 1 for p in built.competency_plans for q in p.seed_questions)


# -- the shared sample plan ----------------------------------------------------------------


def test_the_sample_plan_has_pm_seniors_one_ask_questions_and_unchanged_structure():
    sample = json.loads(SAMPLE.read_text())
    pm = json.loads(PM.read_text())
    assert [p["seed_questions"] for p in sample["competency_plans"]] == \
        [p["seed_questions"] for p in pm["competency_plans"]]
    assert all(asks(q) == 1 for p in sample["competency_plans"] for q in p["seed_questions"])
    loaded = InterviewBlueprint.model_validate(sample)
    assert [p.question_ids for p in loaded.competency_plans] == \
        [p["question_ids"] for p in sample["competency_plans"]]  # ids unchanged, still valid
    assert loaded.model_dump(mode="json") == sample  # the contract round trip


# -- every cut is recorded: a log line and a note on the plan (round 2) --------------------


def _payload_with(question: str, position: int = 1) -> tuple[InterviewBlueprint, dict]:
    bp = InterviewBlueprint.model_validate(json.loads(PM.read_text()))
    payload = {"competency_plans": [
        {"competency_id": p.competency_id, "name": p.name, "target_depth": p.target_depth,
         "seed_questions": list(p.seed_questions), "emphasis": 1.0} for p in bp.competency_plans],
        "suggested_opening": "Hello."}
    payload["competency_plans"][1]["seed_questions"][position] = question
    return bp, payload


def test_every_cut_is_recorded_on_the_plan_and_in_the_log():
    from loguru import logger

    bp, payload = _payload_with(TWO_PART[2], position=1)
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        built = build_blueprint(blueprint_id="plan_x", spec=bp.evaluation_spec, payload=payload)
    finally:
        logger.remove(sink)
    plan = built.competency_plans[1]
    [cut] = built.question_cuts
    assert cut.competency_id == plan.competency_id
    assert cut.question_id == plan.question_ids[1] == "Q3.2"  # the second plan's second question
    assert cut.original == TWO_PART[2]
    assert cut.cut_to == plan.seed_questions[1] == first_ask(TWO_PART[2])
    [line] = [l for l in lines if "first ask" in l]
    assert "plan_x" in line and "Q3.2" in line and TWO_PART[2] in line and cut.cut_to in line
    # The record survives storage: the plan as JSON, loaded back.
    assert InterviewBlueprint.model_validate(built.model_dump(mode="json")).question_cuts == [cut]


def test_a_plan_with_nothing_cut_records_nothing():
    bp, payload = _payload_with(ONE_ASK[0])
    assert build_blueprint(blueprint_id="p", spec=bp.evaluation_spec, payload=payload).question_cuts == []


async def test_a_refinement_keeps_the_cuts_made_when_the_plan_was_generated(monkeypatch):
    from types import SimpleNamespace

    from blueprint import refine as refine_module
    from shared.contracts import QuestionCut

    bp, payload = _payload_with(ONE_ASK[0])
    earlier = QuestionCut(competency_id=bp.competency_plans[0].competency_id, question_id="Q2.1",
                          original=TWO_PART[1], cut_to=first_ask(TWO_PART[1]))
    original = bp.model_copy(update={"question_cuts": [earlier]})
    payload["competency_plans"][2]["seed_questions"][0] = TWO_PART[2]  # the refinement adds one

    async def model(**kwargs):
        return SimpleNamespace(payload={**payload, "reply": "Done."}, record=None)

    monkeypatch.setattr(refine_module, "request_structured", model)
    refiner = refine_module.BlueprintRefiner(api_key="test", model="test-model")
    revised = (await refiner.refine(blueprint=original, cv_text="cv", message="x")).blueprint
    assert revised.question_cuts[0] == earlier
    assert [c.question_id for c in revised.question_cuts] == ["Q2.1", "Q4.1"]
