"""Phase 3 wave 1: the plan refined at booking with Track H's findings for the technical
round (C-124). Deterministic: a stand-in for the text-model call (request=, C-132).

The fixture: 4 findings, 2 technical, 1 not technical, 1 vague. What is checked is what
Karandeep decided: each finding maps to an area with a neutral probe or is left out; the
raw text never reaches anything Mitra reads; nothing says HR; no findings, no change.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from blueprint.refine import RefinementError, _probe_problems, refine_with_hr_findings
from bot.realtime_prompt import as_goal, area_count, build_area_block, build_area_instruction, build_base_instruction
from shared.contracts import MAX_HR_FINDINGS, HRFindingsForTechnical, InterviewBlueprint

BP = InterviewBlueprint.model_validate(json.loads(
    (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text()))

FINDINGS = [
    # technical
    "Candidate was unclear on how they would size an A/B test or decide when it had run long enough.",
    # technical
    "Described prioritisation only in terms of stakeholder pressure, never a framework or trade-off reasoning.",
    # not technical
    "Notice period is 90 days and expects a 30 percent raise over current salary.",
    # vague
    "Seemed a bit off in places.",
]
PROBES = {
    0: ("metrics", "How do you decide how many users an experiment needs, and when to stop it?"),
    1: ("prioritization", "Can you walk me through a roadmap trade-off where you had to say no to something valuable?"),
}


def stand_in(mapping=None, calls=None):
    """Answers like the model: probes for 0 and 1, the other two left out."""
    mapping = PROBES if mapping is None else mapping

    async def request(**kw):
        if calls is not None:
            calls.append(kw)
        n = int(re.findall(r"^(\d+): ", kw["messages"][1]["content"], re.M)[-1]) + 1
        out = []
        for i in range(n):
            if i in mapping:
                area, probe = mapping[i] if not callable(mapping[i]) else mapping[i](len(calls or []))
                out.append({"index": i, "outcome": "probed", "area_id": area, "probe": probe})
            else:
                out.append({"index": i, "outcome": "not_technical"})
        return SimpleNamespace(payload={"findings": out},
                               record=SimpleNamespace(to_dict=lambda: {"job": "blueprint", "cost_usd": 0.0}))
    return request


def findings(texts=FINDINGS):
    return HRFindingsForTechnical(candidate_id="cand_1", findings_for_technical=texts)


def everything_mitra_reads(plan: InterviewBlueprint) -> str:
    """Every instruction the realtime model can be given: the base, each area's full
    instruction, and each area block sent as a mid-call update."""
    n = area_count(plan)
    parts = [build_base_instruction(plan, first_name="Asha", time_of_day="afternoon")]
    for area in range(1, n + 1):
        parts.append(build_area_instruction(plan, first_name="Asha", time_of_day="afternoon", area=area))
        parts.append(build_area_block(plan, area))
    return "\n".join(parts)


async def test_two_technical_findings_are_probed_and_two_are_left_out():
    usage = []
    refined = await refine_with_hr_findings(BP, findings(), request=stand_in(), usage=usage)
    uses = refined.hr_findings.findings
    assert [(u.outcome, u.area_id) for u in uses] == [
        ("probed", "metrics"), ("probed", "prioritization"), ("not_technical", None), ("not_technical", None)]
    assert [u.finding for u in uses] == FINDINGS, "the record keeps each finding verbatim"
    by_id = {p.competency_id: p for p in refined.competency_plans}
    for use, (area, probe) in zip(uses, PROBES.values()):
        p = by_id[area]
        assert p.seed_questions[1] == probe, "inserted after the area's first question"
        assert dict(p.numbered_questions())[use.question_id] == probe, "the record's id is the probe's"
        assert len(p.question_ids) == len(p.seed_questions)
    untouched = [p for p in refined.competency_plans if p.competency_id not in ("metrics", "prioritization")]
    original = {p.competency_id: p for p in BP.competency_plans}
    assert all(p == original[p.competency_id] for p in untouched)
    assert usage == [{"job": "blueprint", "cost_usd": 0.0}], "the call's cost is handed back (C-132)"


async def test_the_raw_text_and_the_word_hr_reach_nothing_mitra_reads():
    refined = await refine_with_hr_findings(BP, findings(), request=stand_in())
    text = everything_mitra_reads(refined)
    # Positive control: the probes do reach Mitra, and so would a raw finding put there.
    assert all(as_goal(probe) in text for _, probe in PROBES.values())  # round 15: shown as goals
    leaked = refined.model_copy(deep=True)
    leaked.competency_plans[0].seed_questions[0] = FINDINGS[2]
    # Round 15: a plan question is shown as a goal (lower-case first letter, no "?"),
    # so the control proves the case-blind 5-word search below catches a planted leak.
    leaked_words = " ".join(re.findall(r"[a-z0-9']+", everything_mitra_reads(leaked).lower()))
    planted = re.findall(r"[a-z0-9']+", FINDINGS[2].lower())
    assert " ".join(planted[:5]) in leaked_words, "the search can find a leak"
    for finding in FINDINGS:
        assert finding not in text
        words = re.findall(r"[a-z0-9']+", finding.lower())
        for i in range(len(words) - 4):
            assert " ".join(words[i:i + 5]) not in " ".join(re.findall(r"[a-z0-9']+", text.lower())), finding
    assert not re.search(r"\bHR\b|human resources|hr_findings", text, re.IGNORECASE)
    assert not re.search(r"\bHR\b|human resources", everything_mitra_reads(BP), re.IGNORECASE), "baseline"


async def test_no_findings_leaves_the_plan_unchanged_byte_for_byte():
    called = []
    for empty in (findings([]), [], None, ["  "]):
        out = await refine_with_hr_findings(BP, empty, request=stand_in(calls=called))
        assert out.model_dump_json() == BP.model_dump_json()
    assert called == [], "no model call when there is nothing to refine with"


async def test_a_probe_that_is_not_neutral_is_retried_then_left_out_never_weakened():
    attempts = {"n": 0}
    bad = "Earlier you mentioned trouble sizing tests; how would you size an A/B test?"

    def first_bad_then_good(n):
        return ("metrics", bad if n == 1 else PROBES[0][1])

    calls = []
    refined = await refine_with_hr_findings(BP, findings(), request=stand_in({0: first_bad_then_good, 1: PROBES[1]},
                                                                            calls=calls))
    assert len(calls) == 2 and "not neutral" in calls[1]["messages"][-1]["content"]
    assert refined.hr_findings.findings[0].outcome == "probed"
    assert bad not in everything_mitra_reads(refined)

    always_bad = {0: ("metrics", "The HR round flagged A/B sizing; how do you size a test?"), 1: PROBES[1]}
    calls = []
    refined = await refine_with_hr_findings(BP, findings(), request=stand_in(always_bad, calls=calls))
    assert len(calls) == 2
    assert refined.hr_findings.findings[0].outcome == "not_technical", "left out, not used as it was"
    assert "HR round" not in everything_mitra_reads(refined)
    attempts["n"] += 1


@pytest.mark.parametrize("probe, problem", [
    ("You said you struggle with tests. How do you size one?", "not neutral"),
    ("Did we hear right that you avoid metrics?", "not neutral"),
    ("How would you size an A/B test or decide when it had run long enough?", "copies the note's wording"),
    ("Tell me about sizing a test.", "not exactly one question"),
    ("What? And why?", "not exactly one question"),
    ("How " + "really " * 30 + "do you size a test?", "words"),
])
def test_the_neutrality_check_catches_each_kind(probe, problem):
    assert any(problem in p for p in _probe_problems(probe, FINDINGS[0]))


def test_the_neutrality_check_passes_the_fixture_probes():
    for i, (_, probe) in PROBES.items():
        assert _probe_problems(probe, FINDINGS[i]) == []


@pytest.mark.parametrize("probe", [
    "Can you walk me through a trade-off where you said no to something valuable?",
    "What is a decision you shared ownership of, and how did it go?",
])
def test_ordinary_phrasing_is_not_mistaken_for_pointing_at_an_earlier_conversation(probe):
    """Found by the smoke stage: "you said no" was rejected as "you said"."""
    assert _probe_problems(probe, FINDINGS[1]) == []


@pytest.mark.parametrize("probe", ["As you said, sizing is hard; how do you do it?",
                                   "You said earlier you avoid tests; why is that?"])
def test_pointing_at_an_earlier_conversation_is_still_caught(probe):
    assert any("not neutral" in p for p in _probe_problems(probe, FINDINGS[0]))


async def test_an_unknown_area_is_left_out():
    refined = await refine_with_hr_findings(BP, findings(), request=stand_in({0: ("cooking", PROBES[0][1])}))
    assert [u.outcome for u in refined.hr_findings.findings] == ["not_technical"] * 4
    assert refined.competency_plans == BP.competency_plans


async def test_two_probes_in_one_area_both_keep_their_own_ids():
    second = "What is the smallest effect size you would trust from an experiment, and why?"
    refined = await refine_with_hr_findings(BP, findings(),
                                            request=stand_in({0: PROBES[0], 1: ("metrics", second)}))
    p = next(p for p in refined.competency_plans if p.competency_id == "metrics")
    ids = dict(p.numbered_questions())
    assert [ids[u.question_id] for u in refined.hr_findings.findings[:2]] == [PROBES[0][1], second]


async def test_more_findings_than_the_contract_cap_are_all_considered():
    """Track H does not cap them (Karandeep). The function has no cap of its own; while
    the contract's MAX_HR_FINDINGS is 4, the result does not fit and the booking keeps the
    plan (C-124). Once Session C lifts the cap, all six are recorded."""
    six = FINDINGS + ["Could not explain how a retention metric was defined.", "Asked about remote work."]
    calls = []
    try:
        refined = await refine_with_hr_findings(BP, six, request=stand_in(calls=calls))
    except RefinementError as exc:
        assert MAX_HR_FINDINGS < 6 and f"contract cap {MAX_HR_FINDINGS}" in str(exc)
    else:
        assert len(refined.hr_findings.findings) == 6
    assert "5: Asked about remote work." in calls[0]["messages"][1]["content"], "all six reached the mapping"


async def test_a_model_failure_raises_refinement_error():
    from shared.text_models import TextModelError

    async def failing(**kw):
        raise TextModelError("down")

    with pytest.raises(RefinementError):
        await refine_with_hr_findings(BP, findings(), request=failing)


async def test_the_hr_record_never_reaches_the_live_engine_prompt_or_the_auditor_prompt():
    """The record (with the raw text) is on the plan; the only readers that format the
    plan for a model are the realtime prompt (above) and the auditor. Neither prints it."""
    from bot.auditor import RealtimeAuditor

    refined = await refine_with_hr_findings(BP, findings(), request=stand_in())
    seen = []

    async def request(**kw):
        seen.append(json.dumps(kw["messages"]))
        return SimpleNamespace(payload={"coverage": "partial", "label": "off_plan", "question_id": None,
                                        "new_claims": [], "contradictions": []},
                               record=SimpleNamespace(cost_usd=0.0))

    a = RealtimeAuditor(blueprint=refined, area_now=lambda: 2, live=None, request=request)
    a.start()
    await a.label_now(0, PROBES[0][1])
    await a.finish(timeout=5)
    assert seen and all(f not in s for s in seen for f in FINDINGS)
    assert not any(re.search(r"\bHR\b", s) for s in seen)
