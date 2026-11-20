"""The booking prepares every plan (C-A23, C-176), through Session A's REAL
refine_for_booking with only its text-model call stood in (tests/booking_stand_in.py):

- every booking calls it, with or without HR findings, with the minutes the interview runs;
- HR findings are used when present (uncapped; one 10,000-character guard);
- the plan is sized to the booking (dropped areas in `not_planned`) and gets goals;
- any failure or time-out keeps the ORIGINAL plan, the booking still succeeds, and both
  notes say so plainly;
- costs are stored with the candidate: HR refinement as `blueprint_refine`, goal writing as
  A's `blueprint_goals`.
"""

import json
from pathlib import Path

import pytest

from live import hr_hook
from shared.contracts import InterviewBlueprint
from tests.booking_stand_in import booking_function, stand_in
from tests.test_interviews import client, make_candidate  # noqa: F401  (fixture)

FINDINGS = ["Ownership of the 2024 checkout migration: how much was theirs.",
            "Prefers written communication; was brief in spoken answers."]
PROBE = {0: (None, "How much of the checkout migration did you own yourself?")}


@pytest.fixture
def findings_file(tmp_path, monkeypatch):
    def write(mapping):
        path = tmp_path / "hr_findings.json"
        path.write_text(json.dumps(mapping))
        monkeypatch.setenv(hr_hook.FIXTURE_ENV, str(path))
    return write


async def _plan(candidate_id):
    from app import db

    async with db.get_sessionmaker()() as s:
        return (await s.get(db.Candidate, candidate_id)).blueprint


def _use(monkeypatch, request):
    monkeypatch.setattr(hr_hook, "refinement_function", lambda: booking_function(request))
    return request


def _probe_first_area(plan_json):
    first = plan_json["competency_plans"][0]["competency_id"]
    return {0: (first, PROBE[0][1])}


async def test_a_plan_without_findings_is_still_prepared(client, monkeypatch):
    monkeypatch.delenv(hr_hook.FIXTURE_ENV, raising=False)
    model = _use(monkeypatch, stand_in())
    cid = await make_candidate()
    r = client.post(f"/candidates/{cid}/interviews", json={"duration_minutes": 20})
    assert r.status_code == 200 and r.json()["hr_findings_note"] == "No HR findings."
    assert model.calls == ["_GoalsResponse"]  # no findings call, but the goals are written
    plan = InterviewBlueprint.model_validate(await _plan(cid))
    assert all(g for p in plan.competency_plans for g in p.question_goals)
    assert r.json()["plan_note"].startswith("Sized to 20 minutes: ")
    assert r.json()["plan_note"].endswith(f"goals written for {sum(len(p.seed_questions) for p in plan.competency_plans)} "
                                          f"of {sum(len(p.seed_questions) for p in plan.competency_plans)} questions.")


async def test_findings_refine_the_plan_and_the_rest_still_runs(client, monkeypatch, findings_file):
    cid = await make_candidate()
    findings_file({cid: FINDINGS})
    model = _use(monkeypatch, stand_in(_probe_first_area(await _plan(cid))))
    r = client.post(f"/candidates/{cid}/interviews", json={"duration_minutes": 30})
    assert r.status_code == 200 and r.json()["hr_findings_note"] == "Plan refined with HR findings: 1 of 2 probed."
    assert model.calls == ["_FindingsResponse", "_GoalsResponse"]
    plan = InterviewBlueprint.model_validate(await _plan(cid))
    assert [f.outcome for f in plan.hr_findings.findings] == ["probed", "not_technical"]
    assert all(g for p in plan.competency_plans for g in p.question_goals)


async def test_the_minutes_passed_are_the_minutes_the_interview_runs(client, monkeypatch):
    """60 booked runs as 56 planned on the realtime engine (C-170): the plan is sized to 56."""
    seen = []
    from blueprint.refine import refine_for_booking

    async def spy(plan, findings=None, **kw):
        seen.append(kw.get("booked_minutes"))
        return await refine_for_booking(plan, findings, request=stand_in(), **kw)

    monkeypatch.setattr(hr_hook, "refinement_function", lambda: spy)
    cid = await make_candidate()
    body = client.post(f"/candidates/{cid}/interviews", json={"duration_minutes": 60}).json()
    assert body["planned_minutes"] == 56 and seen == [56]


@pytest.mark.parametrize("how,hr_note,plan_note", [
    ("missing", "HR findings were not used: plan preparation is not available.",
     "Plan preparation is not available; the original plan is kept."),
    ("not_a_plan", "Plan refinement with HR findings failed (ValueError); the HR findings were not used.",
     "The plan could not be prepared for this booking, with or without the HR findings (ValueError); the "
     "original plan is kept."),
    ("too_slow", "Plan refinement with HR findings failed (it took too long); the HR findings were not used.",
     "The plan could not be prepared for this booking, with or without the HR findings (it took too long); "
     "the original plan is kept."),
])
async def test_when_nothing_can_prepare_the_plan_the_original_is_kept_and_the_booking_succeeds(
        client, monkeypatch, findings_file, how, hr_note, plan_note):
    """Round 18 (Session A, for C-176): with HR findings, a failure is retried once without
    them; only when that fails too (or no preparation exists) is the original plan kept."""
    cid = await make_candidate()
    findings_file({cid: FINDINGS})
    if how == "missing":
        monkeypatch.setattr(hr_hook, "refinement_function", lambda: None)
    elif how == "not_a_plan":
        async def not_a_plan(plan, findings=None, **kw):
            return {"not": "a plan"}
        monkeypatch.setattr(hr_hook, "refinement_function", lambda: not_a_plan)
    else:
        monkeypatch.setattr(hr_hook, "BOOKING_REFINE_TIMEOUT_SECONDS", 0.2)
        monkeypatch.setattr(hr_hook, "BOOKING_RETRY_TIMEOUT_SECONDS", 0.2)
        _use(monkeypatch, stand_in(delay=2.0))
    before = await _plan(cid)
    r = client.post(f"/candidates/{cid}/interviews", json={"duration_minutes": 20})
    assert r.status_code == 200 and r.json()["hr_findings_note"] == hr_note
    assert r.json()["plan_note"] == plan_note
    assert await _plan(cid) == before


async def test_a_failed_hr_refinement_still_sizes_the_plan_and_writes_goals(client, monkeypatch, findings_file):
    """Round 18 (Session A, for C-176): the HR refinement fails, so the plan is prepared again
    WITHOUT the findings: sized to the booking, goals written; only the HR probes are lost,
    and the note says so plainly. The failed call's cost is kept with the retry's."""
    _use(monkeypatch, stand_in(fail="findings"))
    cid = await make_candidate()
    findings_file({cid: FINDINGS})
    before = InterviewBlueprint.model_validate(await _plan(cid))
    r = client.post(f"/candidates/{cid}/interviews", json={"duration_minutes": 20})
    assert r.status_code == 200
    assert r.json()["hr_findings_note"] == ("Plan refinement with HR findings failed (RefinementError), so the HR "
                                            "findings were not used; the plan was still prepared without them.")
    assert r.json()["plan_note"].startswith("Sized to 20 minutes")
    plan = InterviewBlueprint.model_validate(await _plan(cid))
    assert plan.hr_findings is None or not plan.hr_findings.findings, "no HR probes"
    assert all(g for p in plan.competency_plans for g in p.question_goals), "goals written"
    assert plan.evaluation_spec.duration_minutes == 20 != before.evaluation_spec.duration_minutes \
        or plan.model_dump() != before.model_dump(), "the stored plan is the prepared one"


async def test_failed_goal_writing_keeps_the_sized_plan_and_says_so(client, monkeypatch):
    """A's goal writing never raises: the plan is still sized; Mitra gets the plan's questions."""
    _use(monkeypatch, stand_in(fail="goals"))
    cid = await make_candidate()
    r = client.post(f"/candidates/{cid}/interviews", json={"duration_minutes": 20})
    assert r.status_code == 200
    assert r.json()["plan_note"].endswith("goal writing failed, so Mitra is shown the plan's own questions.")
    plan = InterviewBlueprint.model_validate(await _plan(cid))
    assert all(g is None for p in plan.competency_plans for g in p.question_goals)


def test_the_real_function_is_looked_up_by_its_name():
    """Loaded as a separate copy: the suite's fixture replaces the shared module's lookup."""
    import importlib.util

    from blueprint import refine

    spec = importlib.util.spec_from_file_location("hr_hook_copy", hr_hook.__file__)
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    assert copy.refinement_function() is refine.refine_for_booking


# -- C-A13 (no cap, one guard) and C-A14 (costs stored) -------------------------------------------


@pytest.mark.parametrize("count", [5, 12])
async def test_five_and_twelve_findings_are_all_used_and_the_costs_stored(client, monkeypatch, findings_file, count):
    cid = await make_candidate()
    findings_file({cid: [f"Topic {i}: how they handled the migration." for i in range(count)]})
    _use(monkeypatch, stand_in(_probe_first_area(await _plan(cid))))
    r = client.post(f"/candidates/{cid}/interviews")
    assert r.status_code == 200 and r.json()["hr_findings_note"] == f"Plan refined with HR findings: 1 of {count} probed."
    assert len(InterviewBlueprint.model_validate(await _plan(cid)).hr_findings.findings) == count
    assert [c["job"] for c in await _costs(cid)] == ["blueprint_refine", "blueprint_goals"]


async def test_findings_over_the_length_guard_are_not_used_but_the_plan_is_still_prepared(
        client, monkeypatch, findings_file):
    cid = await make_candidate()
    findings_file({cid: ["x" * 6000, "y" * 6000]})  # 12,000 characters
    model = _use(monkeypatch, stand_in())
    r = client.post(f"/candidates/{cid}/interviews")
    assert r.status_code == 200
    assert r.json()["hr_findings_note"] == ("HR findings are too long to use (12000 characters; the limit is 10000); "
                                            "they were not used.")
    assert model.calls == ["_GoalsResponse"]
    assert InterviewBlueprint.model_validate(await _plan(cid)).hr_findings is None


async def test_a_failed_refinement_still_books_and_its_cost_is_stored(client, monkeypatch, findings_file):
    cid = await make_candidate()
    findings_file({cid: FINDINGS})

    async def fails(plan, findings=None, *, usage=None, **kw):
        usage.append({"job": "blueprint", "cost_usd": 0.0007, "outcome": "error"})
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(hr_hook, "refinement_function", lambda: fails)
    r = client.post(f"/candidates/{cid}/interviews")
    assert r.status_code == 200 and "the original plan is kept" in r.json()["plan_note"]
    # Round 18: the retry without the findings failed too; both failed calls are billed (C-A14).
    assert await _costs(cid) == [{"job": "blueprint_refine", "cost_usd": 0.0007, "outcome": "error"}] * 2


async def _costs(candidate_id):
    from app import db

    async with db.get_sessionmaker()() as s:
        return (await s.get(db.Candidate, candidate_id)).text_model_calls


async def test_plan_generation_stores_its_cost_also_when_it_fails(tmp_path, monkeypatch):
    """C-A14: generate_blueprint_task appends generator.last_usage.to_dict()."""
    from types import SimpleNamespace

    from app import main
    from tests.live.support import fresh_db

    db = await fresh_db(tmp_path, monkeypatch, "gen_cost.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    bp = json.loads((Path(__file__).resolve().parents[2] / "bot" / "fixtures" / "pm_senior.json").read_text())
    async with db.get_sessionmaker()() as s:
        s.add(db.Job(id="job_g", jd_text="jd", spec_status=db.SpecStatus.READY, evaluation_spec=bp["evaluation_spec"]))
        await s.flush()
        for cid in ("cand_gen_ok", "cand_gen_fail"):
            s.add(db.Candidate(id=cid, job_id="job_g", cv_text="cv", blueprint_status=db.BlueprintStatus.PENDING))
        await s.commit()
    record = {"job": "blueprint", "model": "gpt-x", "cost_usd": 0.03, "outcome": "ok"}

    class Generator:
        def __init__(self, **kw):
            self.last_usage = SimpleNamespace(to_dict=lambda: dict(record))

        async def generate(self, *, blueprint_id, **kw):
            if blueprint_id == "cand_gen_fail":
                raise RuntimeError("schema error")
            return InterviewBlueprint.model_validate({**bp, "blueprint_id": blueprint_id})

    monkeypatch.setattr(main, "BlueprintGenerator", Generator)
    await main.generate_blueprint_task("cand_gen_ok")
    await main.generate_blueprint_task("cand_gen_fail")
    async with db.get_sessionmaker()() as s:
        ok, fail = await s.get(db.Candidate, "cand_gen_ok"), await s.get(db.Candidate, "cand_gen_fail")
        assert ok.blueprint_status == "ready" and ok.text_model_calls == [record]
        assert fail.blueprint_status == "failed" and fail.text_model_calls == [record]
    await db.reset_engine()


async def test_generation_refinement_and_goal_costs_are_told_apart_and_old_rows_keep_their_label(
        client, monkeypatch, findings_file):
    """C-158 and C-176: generation stays "blueprint"; the HR refinement is "blueprint_refine";
    goal writing keeps A's "blueprint_goals"; a row stored earlier is left as it was."""
    from app import db

    cid = await make_candidate()
    old = {"job": "blueprint", "model": "gpt-x", "cost_usd": 0.03, "outcome": "ok"}  # generation, stored earlier

    async def put_old():
        async with db.get_sessionmaker()() as s:
            (await s.get(db.Candidate, cid)).text_model_calls = [old]
            await s.commit()

    await put_old()
    findings_file({cid: FINDINGS})
    _use(monkeypatch, stand_in(_probe_first_area(await _plan(cid))))
    assert client.post(f"/candidates/{cid}/interviews").status_code == 200
    costs = await _costs(cid)
    assert costs[0] == old
    assert [c["job"] for c in costs] == ["blueprint", hr_hook.REFINE_JOB, "blueprint_goals"]
