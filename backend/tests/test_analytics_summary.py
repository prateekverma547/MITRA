"""The phase 4 summary (Session D): reconciliation, edge cases, who decided, no secrets,
the routes, and scale. CONSTRUCTED records in a local test database (the real ones are
hand-checked in track_i_notes/D_checks/d16_handcheck.py)."""

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aftercall import settings as ac_settings, store as ac_store
from analytics.summary import build
from analytics.summary.categories import CATEGORIES, END_REASON_CATEGORY
from analytics.summary.shapes import InterviewSummary, JobSummary
from shared.interview_contracts import (
    AfterCallCheck,
    AfterCallCheckName,
    AfterCallState,
    AfterCallStatus,
    SecondVoiceSignal,
    TimeRange,
)

PASSWORD = "pw-canary-9f3k"
SAID = "zebra-quokka-candidate-words"
MITRA_SAID = "lighthouse-mitra-question-words"
NOW = datetime.now(UTC)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/a.db")
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "rec"))
    monkeypatch.delenv(ac_settings.ENABLED_ENV, raising=False)
    from app import db as appdb

    await appdb.reset_engine()
    await appdb.create_all()
    yield appdb
    await appdb.reset_engine()


def metrics(*, usd=0.1, auditor=0.002, scoring=0.01, ended="interview_complete", ending="model_spoke",
            coordinator=None, connection=None, labels=None):
    m = {"ended_because": ended, "ending": {"ending": ending, "hang_up_by": "bot_stopped_speaking"},
         "coordinator_events": coordinator or [{"kind": "note", "at_seconds": 5.0, "text": MITRA_SAID}],
         "auditor": {"stats": {"usd": auditor} if auditor is not None else {}, "coverage": {"2": "partial"},
                     "labels": labels or {"3": {"label": "plan", "question_id": "Q2.1", "area": 2, "text": MITRA_SAID}}}}
    if usd is not None:
        m["usage"] = {"estimated_usd": usd, "minutes": 3.0}
    if scoring is not None:
        m["text_model_calls"] = [{"job": "feedback", "cost_usd": scoring}]
    if connection is not None:
        m["connection"] = connection
    return m


BLUEPRINT = {"competency_plans": [{"competency_id": "strategy", "question_ids": ["Q2.1", "Q2.2"]},
                                  {"competency_id": "metrics", "question_ids": ["Q3.1"]}],
             "hr_findings": {"findings": [{"finding": "x", "outcome": "probed", "area_id": "strategy",
                                           "question_id": "Q2.2"}]}}


async def seed(appdb, job="job_a", interviews=(), candidates=None, events=(), refusals=()):
    from live.models import CapacityEventRecord, InterviewEventRecord

    async with appdb.get_sessionmaker()() as s:
        s.add(appdb.Job(id=job, title="Senior PM", jd_text="jd", spec_status=appdb.SpecStatus.READY))
        await s.commit()
    async with appdb.get_sessionmaker()() as s:
        for cid, plan_calls in (candidates or {"cand_1": [{"job": "blueprint", "cost_usd": 0.05}]}).items():
            s.add(appdb.Candidate(id=cid, job_id=job, name=f"Name {cid}", cv_text=f"cv {SAID}",
                                  blueprint=BLUEPRINT, blueprint_status=appdb.BlueprintStatus.READY,
                                  text_model_calls=plan_calls))
        await s.commit()
    async with appdb.get_sessionmaker()() as s:
        for k, iv in enumerate(interviews):
            s.add(appdb.Interview(
                id=iv["id"], candidate_id=iv.get("cand", "cand_1"), meeting_id=f"m-{iv['id']}", password=PASSWORD,
                daily_room_name="r", daily_room_url="https://example.daily.co/r",
                room_expires_at=NOW + timedelta(hours=1), status=iv.get("status", "completed"),
                created_at=NOW - timedelta(minutes=100 - k),
                started_at=iv.get("started_at", NOW - timedelta(minutes=10)),
                ended_at=iv.get("ended_at", NOW - timedelta(minutes=5)),
                session_metrics=iv.get("metrics"), cleaned_transcript=iv.get("cleaned"),
                transcript={"turns": [{"speaker": "candidate", "text": SAID, "at_seconds": 1}]}))
        await s.commit()
    async with appdb.get_sessionmaker()() as s:
        for n, (iid, kind, at, payload) in enumerate(events):
            s.add(InterviewEventRecord(event_id=f"{iid}:{n}", interview_id=iid, at_seconds=at, wall_time=NOW,
                                       source="bot", kind=kind,
                                       payload={"event_id": f"{iid}:{n}", "payload": {"kind": kind, **payload}}))
        for iid in refusals:
            s.add(CapacityEventRecord(kind="refused", interview_id=iid, live_bots=6, limit=6))
        await s.commit()


async def job(appdb, job_id="job_a") -> JobSummary:
    async with appdb.get_sessionmaker()() as s:
        return await build.job_summary(s, job_id)


def row(summary, iid) -> InterviewSummary:
    return next(r for r in summary.interview_rows if r.interview_id == iid)


def part(r, name):
    return next(p for p in r.cost.parts if p.part == name)


# -- reconciliation -------------------------------------------------------------------------------------


async def test_job_total_is_the_sum_of_the_interviews_and_the_counts_add_up(db):
    await seed(db, interviews=[
        {"id": "i1", "metrics": metrics(usd=0.10), "cleaned": {"usd": 0.003, "text": SAID}},
        {"id": "i2", "metrics": metrics(usd=0.20, ended="candidate_withdrew")},
        {"id": "i3", "status": "no_show", "metrics": None},
        {"id": "i4", "status": "in_progress", "metrics": None, "ended_at": None},
    ], events=[("i1", "interview_ended", 300.0, {"ended_because": "interview_complete", "duration_seconds": 300.0})],
        refusals=["i3", "i3"])
    s = await job(db)
    per = sum(r.cost.recorded_usd for r in s.interview_rows)
    assert s.total_cost_usd.value == pytest.approx(per) and per > 0
    assert sum(p.usd for p in s.cost_by_part) == pytest.approx(per)
    assert sum(s.by_category.values()) == s.interviews == len(s.interview_rows) == 4
    assert sum(s.by_status.values()) == 4
    assert set(s.by_category) == set(CATEGORIES)
    assert s.capacity_refusals.value == 2
    assert s.cost_per_interview_usd.value == pytest.approx(per / s.cost_per_interview_usd.basis.counted)


# -- missing is not zero ------------------------------------------------------------------------------------


async def test_a_missing_cost_is_not_recorded_never_zero(db):
    await seed(db, interviews=[{"id": "i1", "metrics": metrics(usd=None, auditor=None, scoring=None)},
                               {"id": "i2", "metrics": metrics(usd=0.25)}])
    s = await job(db)
    r1 = row(s, "i1")
    for name in ("realtime", "auditor", "cleaner", "scoring", "daily"):
        assert part(r1, name).status == "not_recorded" and part(r1, name).usd is None
    assert r1.cost.complete is False and "realtime" in r1.cost.missing_parts
    rt = next(p for p in s.cost_by_part if p.part == "realtime")
    assert rt.interviews_missing == 1 and rt.interviews_recorded == 1 and rt.usd == pytest.approx(0.25)


async def test_an_unpriced_call_makes_the_part_incomplete(db):
    m = metrics()
    m["text_model_calls"] = [{"job": "feedback", "cost_usd": 0.01}, {"job": "feedback", "cost_usd": None}]
    await seed(db, interviews=[{"id": "i1", "metrics": m}])
    p = part(row(await job(db), "i1"), "scoring")
    assert p.status == "incomplete" and p.usd == pytest.approx(0.01) and p.calls_without_price == 1


# -- edge cases ---------------------------------------------------------------------------------------------


async def test_a_running_interview_is_out_of_the_average_even_with_an_ended_event(db):
    await seed(db, interviews=[{"id": "done", "metrics": metrics()},
                               {"id": "live", "status": "in_progress", "metrics": None, "ended_at": None}],
               events=[("done", "interview_ended", 300.0, {"ended_because": "interview_complete", "duration_seconds": 300.0}),
                       ("live", "interview_ended", 999.0, {"ended_because": "interview_complete", "duration_seconds": 999.0})])
    s = await job(db)
    assert row(s, "live").duration_seconds is None and row(s, "live").ending.category == "in_progress"
    assert s.average_length_seconds.value == 300.0
    assert s.average_length_seconds.basis.counted == 1 and s.average_length_seconds.basis.excluded == {"status_in_progress": 1}


async def test_a_no_show(db):
    await seed(db, interviews=[{"id": "ns", "status": "no_show", "metrics": None, "started_at": None}])
    s = await job(db)
    r = row(s, "ns")
    assert r.ending.category == "no_show" and r.duration_seconds is None and r.decisions[-1].phase != "ending"
    assert {p.part: p.status for p in r.cost.parts}["realtime"] == "not_applicable"
    assert s.average_length_seconds.value is None and s.average_length_seconds.basis.excluded == {"status_no_show": 1}
    assert s.total_cost_usd.value == pytest.approx(0.05)  # its candidate's plan, counted on its only interview


async def test_failed_to_start(db):
    await seed(db, interviews=[{"id": "f", "status": "failed", "metrics": {"ended_because": "failed_to_start"}}])
    r = row(await job(db), "f")
    assert r.ending.category == "technical_problem" and r.ending.category_from == "end_reason"
    assert part(r, "realtime").status == "not_applicable"
    assert r.decisions[-1].owner == "code" and r.decisions[-1].reason == "failed_to_start"


async def test_an_unknown_end_reason_is_kept_and_counted_as_unknown(db):
    await seed(db, interviews=[{"id": "u", "metrics": metrics(ended="something_new_from_a")}])
    s = await job(db)
    r = row(s, "u")
    assert r.ending.end_reason == "something_new_from_a" and r.ending.category == "unknown"
    assert r.decisions[-1].owner == "unknown" and s.by_category["unknown"] == 1


async def test_after_call_off_running_and_failed(db, monkeypatch):
    await seed(db, interviews=[{"id": "off", "metrics": metrics()}, {"id": "run", "metrics": metrics()},
                               {"id": "bad", "metrics": metrics()}, {"id": "sig", "metrics": metrics()}])
    t = datetime.now(UTC)
    for iid, state in (("run", AfterCallState.RUNNING), ("bad", AfterCallState.FAILED), ("sig", AfterCallState.DONE)):
        ac_store.write_status(AfterCallStatus(interview_id=iid, updated_at=t, checks=[AfterCallCheck(
            check=AfterCallCheckName.SECOND_VOICE, state=state, reason="x" if state == AfterCallState.FAILED else None)]))
    ac_store.write_signal(SecondVoiceSignal(interview_id="sig", ranges=[TimeRange(start_seconds=1, end_seconds=2)],
                                            confidence=0.2, method="m", method_version="1", wording="A voice may be audible."))
    monkeypatch.setenv(ac_settings.ENABLED_ENV, "1")
    s = await job(db)
    assert row(s, "off").after_call.checks is None and row(s, "off").after_call.enabled_now is True
    assert row(s, "run").after_call.checks == {"second_voice": "running"}
    assert row(s, "bad").after_call.checks == {"second_voice": "failed"}
    assert row(s, "sig").after_call.second_voice_ranges == 1
    assert s.after_call_signals.value == 1 and s.after_call_signals.basis.counted == 3
    monkeypatch.delenv(ac_settings.ENABLED_ENV)
    assert row(await job(db), "sig").after_call.enabled_now is False


async def test_a_job_with_no_interviews(db):
    await seed(db, interviews=[], candidates={"lonely": [{"job": "blueprint", "cost_usd": 0.07}]})
    s = await job(db)
    assert s.interviews == 0 and sum(s.by_category.values()) == 0
    assert s.average_length_seconds.value is None and s.total_cost_usd.value is None
    assert s.plan_cost_without_interview_usd.value == pytest.approx(0.07)


async def test_a_plan_cost_is_counted_once_per_candidate(db):
    await seed(db, interviews=[{"id": "first", "metrics": metrics()}, {"id": "second", "metrics": metrics()}])
    s = await job(db)
    assert part(row(s, "first"), "plan_generation").status == "recorded"
    assert part(row(s, "first"), "plan_generation").usd == 0.05
    for name in ("plan_generation", "plan_refinement"):
        assert part(row(s, "second"), name).status == "counted_elsewhere"
    assert next(p for p in s.cost_by_part if p.part == "plan_generation").usd == pytest.approx(0.05)


async def test_generation_and_refinement_are_separate_parts(db):
    """C-158: generation stored as "blueprint", refinement as "blueprint_refine"."""
    await seed(db, interviews=[{"id": "new", "metrics": metrics(), "cand": "c_new"},
                               {"id": "old", "metrics": metrics(), "cand": "c_old"},
                               {"id": "plain", "metrics": metrics(), "cand": "c_plain"}],
               candidates={"c_new": [{"job": "blueprint", "cost_usd": 0.05}, {"job": "blueprint_refine", "cost_usd": 0.02}],
                           "c_old": [{"job": "blueprint", "cost_usd": 0.06}],
                           "c_plain": [{"job": "blueprint", "cost_usd": 0.04}],
                           "c_lone": [{"job": "blueprint", "cost_usd": 0.03}, {"job": "blueprint_refine", "cost_usd": 0.01}]})
    from app.db import Candidate

    async with db.get_sessionmaker()() as ses:  # c_plain's plan was never refined with HR findings
        c = await ses.get(Candidate, "c_plain")
        c.blueprint = {**BLUEPRINT, "hr_findings": None}
        await ses.commit()
    s = await job(db)
    new, old, plain = row(s, "new"), row(s, "old"), row(s, "plain")
    assert (part(new, "plan_generation").usd, part(new, "plan_refinement").usd) == (0.05, 0.02)
    assert part(new, "plan_refinement").status == "recorded"
    # an old row: everything stored as "blueprint", refinement not recorded separately
    assert part(old, "plan_generation").usd == 0.06 and part(old, "plan_refinement").status == "not_recorded"
    assert "plan_refinement" in old.cost.missing_parts
    assert part(plain, "plan_refinement").status == "not_applicable"
    totals = {p.part: p for p in s.cost_by_part}
    assert totals["plan_generation"].usd == pytest.approx(0.15)
    assert totals["plan_refinement"].usd == pytest.approx(0.02)
    assert (totals["plan_refinement"].interviews_recorded, totals["plan_refinement"].interviews_missing) == (1, 1)
    assert s.total_cost_usd.value == pytest.approx(sum(r.cost.recorded_usd for r in s.interview_rows))
    assert s.plan_cost_without_interview_usd.value == pytest.approx(0.04)  # c_lone: 0.03 + 0.01


async def test_unknown_job_and_interview(db):
    async with db.get_sessionmaker()() as s:
        assert await build.job_summary(s, "nope") is None
        assert await build.interview_summary(s, "nope") is None


# -- areas and connection -------------------------------------------------------------------------------------


async def test_areas_from_events_and_from_stored_labels(db):
    labels = {"3": {"label": "plan", "question_id": "Q2.1", "area": 2, "text": MITRA_SAID},
              "5": {"label": "follow_up", "question_id": "Q2.1", "area": 2, "text": MITRA_SAID},
              "7": {"label": "off_plan", "question_id": None, "area": 3, "text": MITRA_SAID}}
    conn = {"drops": [{"gap_seconds": 12.0}, {"gap_seconds": 40.5}], "rejoin_count": 2, "limit_reached": False}
    await seed(db, interviews=[{"id": "ev", "metrics": metrics()}, {"id": "st", "metrics": metrics(labels=labels, connection=conn)}],
               events=[("ev", "question_labelled", 10.0, {"label": "plan", "question_id": "Q3.1", "area_id": "metrics"}),
                       ("ev", "question_labelled", 20.0, {"label": "clarification", "question_id": "Q3.1", "area_id": "metrics"})])
    s = await job(db)
    ev, st = row(s, "ev").areas, row(s, "st").areas
    assert ev.labels_from == "events" and {r.area_id: r.plan_questions_asked for r in ev.rows} == {"strategy": 0, "metrics": 1}
    assert next(r for r in ev.rows if r.area_id == "metrics").clarifications == 1
    assert st.labels_from == "session_metrics" and st.off_plan_questions == 1
    assert next(r for r in st.rows if r.area_id == "strategy").follow_ups == 1
    assert next(r for r in st.rows if r.area_id == "strategy").coverage == "partial"
    assert st.plan_areas == 2 and st.plan_areas_with_a_plan_question == 1
    c = row(s, "st").connection
    assert (c.recorded, c.drops, c.counted_rejoins, c.longest_gap_seconds) == (True, 2, 2, 40.5)
    assert row(s, "ev").connection.recorded is False


# -- who decided -------------------------------------------------------------------------------------------------


async def test_who_decided(db):
    await seed(db, interviews=[
        {"id": "tool", "metrics": metrics(coordinator=[{"kind": "move_on", "at_seconds": 50.0, "area": 3},
                                                       {"kind": "end_interview_completed", "at_seconds": 90.0}])},
        {"id": "wmodel", "metrics": metrics(ended="candidate_withdrew", coordinator=[])},
        {"id": "wcode", "metrics": metrics(ended="candidate_withdrew", coordinator=[])},
        {"id": "stop", "metrics": metrics(ending="forced_at_hard_stop",
                                          coordinator=[{"kind": "hard_stop_reached", "at_seconds": 1200.0}])},
        {"id": "button", "metrics": metrics(ended="candidate_withdrew", ending="candidate_ended_button",
                                            coordinator=[{"kind": "end_button", "at_seconds": 70.0}])},
        {"id": "limit", "metrics": metrics(ended="rejoin_limit_reached", coordinator=[])},
    ], events=[("wmodel", "withdrawal", 30.0, {"stage": "offered", "by": "model"}),
               ("wmodel", "withdrawal", 40.0, {"stage": "confirmed", "by": "model"}),
               ("wcode", "withdrawal", 41.0, {"stage": "confirmed", "by": "patterns"}),
               ("tool", "area_changed", 50.0, {"area_id": "metrics", "area_name": "Metrics"}),
               ("stop", "area_changed", 300.0, {"area_id": "metrics", "area_name": "Metrics"}),
               ("limit", "rejoin_limit_reached", 88.0, {"rejoins": 4, "limit": 3})])
    s = await job(db)

    def end(iid):
        return next(d for d in row(s, iid).decisions if d.phase == "ending")

    assert (end("tool").owner, end("tool").reason, end("tool").at_seconds) == ("mitra", "completed", 90.0)
    assert (end("wmodel").owner, end("wmodel").reason) == ("mitra", "candidate_withdrew_confirmed")
    assert (end("wcode").owner, end("wcode").reason) == ("code", "candidate_withdrew_confirmed")
    assert (end("stop").owner, end("stop").reason) == ("code", "time_limit")
    assert (end("button").owner, end("button").reason, end("button").at_seconds) == ("code", "candidate_pressed_end_confirmed", 70.0)
    assert (end("limit").owner, end("limit").reason) == ("code", "rejoin_limit")
    tool = row(s, "tool").decisions
    assert [d.owner for d in tool if d.action == "move_on"] == ["mitra"]
    assert not [d for d in tool if d.action == "area_changed"], "Mitra's move is not also counted as code's"
    assert [d.owner for d in row(s, "stop").decisions if d.action == "area_changed"] == ["code"]
    assert [d.owner for d in row(s, "wmodel").decisions if d.action == "withdrawal_offered"] == ["mitra"]
    assert [d.owner for d in tool if d.phase == "before_interview"] == ["plan_refinement"]
    assert all(d.source for r in s.interview_rows for d in r.decisions)
    assert sum(1 for d in row(s, "tool").decisions if d.phase == "ending") == 1


# -- no secrets, no words ------------------------------------------------------------------------------------------


async def test_no_password_transcript_or_words_in_any_output(db):
    await seed(db, interviews=[{"id": "i1", "metrics": metrics(), "cleaned": {"usd": 0.003, "text": SAID}}])
    async with db.get_sessionmaker()() as s:
        out = (await build.job_summary(s, "job_a")).model_dump_json() + \
              (await build.interview_summary(s, "i1")).model_dump_json() + \
              (await build.operations_summary(s)).model_dump_json()
    for canary in (PASSWORD, SAID, MITRA_SAID, "example.daily.co"):
        assert canary not in out, f"{canary} reached the output"
    assert "Name cand_1" in out, "the candidate's name is the one personal field (HR's page)"


# -- the routes -----------------------------------------------------------------------------------------------------


async def test_routes_are_admin_only_and_answer(db, monkeypatch, tmp_path):
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    await seed(db, interviews=[{"id": "i1", "metrics": metrics()}])
    from analytics.summary.routes import router
    from app import main

    if not any(getattr(r, "path", "").startswith("/api/analytics/summary") for r in main.app.routes):
        main.app.include_router(router)  # this test process only; mounting is Session C's (request)
    with TestClient(main.app) as c:
        assert c.get("/api/analytics/summary/jobs/job_a").status_code == 401
        c.post("/admin/login", json={"password": "test-admin-pw"})
        r = c.get("/api/analytics/summary/jobs/job_a")
        assert r.status_code == 200 and JobSummary.model_validate(r.json()).interviews == 1
        assert c.get("/api/analytics/summary/jobs/job_a?rows=false").json()["interview_rows"] is None
        assert c.get("/api/analytics/summary/interviews/i1").status_code == 200
        assert c.get("/api/analytics/summary/interviews/nope").status_code == 404
        assert c.get("/api/analytics/summary/jobs/nope").status_code == 404
        assert c.get("/api/analytics/summary/operations").status_code == 200
        assert PASSWORD not in r.text


async def test_operations_figures_are_system_wide(db):
    await seed(db, interviews=[{"id": "i1", "metrics": metrics()}], refusals=["i1", None])
    from live.models import CapacityEventRecord

    async with db.get_sessionmaker()() as s:
        s.add(CapacityEventRecord(kind="started", interview_id="i1", live_bots=4, limit=6))
        await s.commit()
    async with db.get_sessionmaker()() as s:
        o = await build.operations_summary(s)
    assert (o.capacity_refusals_total, o.capacity_refusals_without_interview, o.peak_live_bots, o.bot_limit_seen) == (2, 1, 6, 6)
    assert sum(o.interviews_by_category.values()) == 1


def test_every_end_reason_has_a_category():
    from shared.interview_contracts import EndReason

    assert {r.value for r in EndReason} <= set(END_REASON_CATEGORY), "a new end reason needs a category (D-78)"


# -- scale -------------------------------------------------------------------------------------------------------


async def test_a_job_of_1000_interviews_takes_a_fixed_number_of_queries(db):
    statuses = ["completed"] * 7 + ["no_show", "in_progress", "failed"]
    ivs = [{"id": f"i{n:04d}", "status": statuses[n % 10], "metrics": metrics(usd=0.1 + n / 10000)
            if statuses[n % 10] == "completed" else None} for n in range(1000)]
    events = [(f"i{n:04d}", "interview_ended", 600.0, {"ended_because": "interview_complete", "duration_seconds": 600.0})
              for n in range(0, 1000, 10)]
    await seed(db, interviews=ivs, events=events, refusals=[f"i{n:04d}" for n in range(0, 1000, 50)])
    from sqlalchemy import event

    engine = db.get_engine()
    queries = []

    def count(*_a):
        queries.append(1)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    t0 = time.perf_counter()
    s = await job(db)
    elapsed = time.perf_counter() - t0
    event.remove(engine.sync_engine, "before_cursor_execute", count)
    n_queries = len(queries)
    assert n_queries > 0, "the counter must see the queries, or this proves nothing"
    assert s.interviews == 1000 and sum(s.by_category.values()) == 1000
    assert n_queries <= 6, f"{n_queries} queries for 1,000 interviews"
    assert s.total_cost_usd.value == pytest.approx(sum(r.cost.recorded_usd for r in s.interview_rows))
    print(json.dumps({"interviews": 1000, "queries": n_queries, "seconds": round(elapsed, 3)}))
    assert elapsed < 30


# -- the console home's job list ------------------------------------------------------------------------


async def test_job_list_jobs_with_interviews_newest_first_in_one_query(db):
    await seed(db, job="job_old", interviews=[{"id": "o1", "metrics": metrics(), "started_at": NOW - timedelta(days=3)},
                                              {"id": "o2", "metrics": metrics(), "started_at": NOW - timedelta(days=2)}])
    await seed(db, job="job_new", interviews=[{"id": "n1", "cand": "cand_n", "metrics": metrics(),
                                               "started_at": NOW - timedelta(hours=1)}],
               candidates={"cand_n": []})
    await seed(db, job="job_booked", interviews=[{"id": "b1", "cand": "cand_b", "status": "scheduled", "metrics": None,
                                                  "started_at": None, "ended_at": None}], candidates={"cand_b": []})
    await seed(db, job="job_empty", interviews=[], candidates={"cand_e": []})
    from sqlalchemy import event

    engine = db.get_engine()
    queries = []

    def count(*_a):
        queries.append(1)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    async with db.get_sessionmaker()() as s:
        out = await build.job_list(s)
    event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert len(queries) == 1 and out.recent_interviews is None
    # newest first: started 1 h ago; the scheduled one by its booking time (seeded 100 min ago);
    # then 2 days ago
    assert [j.job_id for j in out.jobs] == ["job_new", "job_booked", "job_old"]
    old = next(j for j in out.jobs if j.job_id == "job_old")
    assert old.interviews == 2 and old.title == "Senior PM"
    assert abs((old.latest_interview_at.replace(tzinfo=UTC) - (NOW - timedelta(days=2))).total_seconds()) < 1
    assert "job_empty" not in [j.job_id for j in out.jobs], "a job with no interview is not listed"


async def test_job_list_recent_interviews(db):
    await seed(db, interviews=[{"id": f"i{n}", "metrics": metrics(ended="candidate_withdrew") if n == 3 else metrics(),
                                "started_at": NOW - timedelta(hours=10 - n)} for n in range(5)])
    from sqlalchemy import event

    engine = db.get_engine()
    queries = []

    def count(*_a):
        queries.append(1)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    async with db.get_sessionmaker()() as s:
        out = await build.job_list(s, recent=3)
    event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert len(queries) == 2
    assert [r.interview_id for r in out.recent_interviews] == ["i4", "i3", "i2"]
    assert (out.recent_interviews[1].end_reason, out.recent_interviews[1].category) == ("candidate_withdrew", "candidate_ended")
    async with db.get_sessionmaker()() as s:
        assert len((await build.job_list(s, recent=500)).recent_interviews) == 5  # capped at RECENT_MAX by the route
    text = out.model_dump_json()
    for canary in (PASSWORD, SAID, MITRA_SAID, "example.daily.co"):
        assert canary not in text


async def test_job_list_route_is_admin_only(db, monkeypatch):
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    await seed(db, interviews=[{"id": "i1", "metrics": metrics()}])
    from app import main

    with TestClient(main.app) as c:
        signed_out = c.get("/api/analytics/summary/jobs")
        assert signed_out.status_code == 401 and "job_a" not in signed_out.text
        assert c.get("/api/analytics/summary/jobs?recent=5").status_code == 401
        c.post("/admin/login", json={"password": "test-admin-pw"})
        r = c.get("/api/analytics/summary/jobs?recent=5")
        assert r.status_code == 200 and r.json()["jobs"][0]["job_id"] == "job_a"
        assert len(r.json()["recent_interviews"]) == 1
        assert c.get("/api/analytics/summary/jobs?recent=51").status_code == 422
        assert c.get("/api/analytics/summary/jobs?recent=-1").status_code == 422
        assert PASSWORD not in r.text


async def test_only_accepted_end_interview_calls_are_endings(db):
    """A-95: end_interview's outcomes accepted_withdrew / accepted_completed end the interview;
    refused and after_end are decisions during it, never endings."""
    await seed(db, interviews=[
        {"id": "acc", "metrics": metrics(coordinator=[])},
        {"id": "ref", "metrics": metrics(ending="forced_at_hard_stop", coordinator=[])},
        {"id": "after", "metrics": metrics(ended="candidate_withdrew", coordinator=[])},
        {"id": "dup", "metrics": metrics(ending="forced_at_hard_stop",
                                         coordinator=[{"kind": "end_interview_refused", "at_seconds": 60.0}])},
        {"id": "only_ref", "metrics": metrics(coordinator=[])},
        {"id": "legacy", "metrics": metrics(coordinator=[])},
    ], events=[("acc", "tool_called", 90.0, {"tool": "end_interview", "outcome": "accepted_completed"}),
               ("ref", "tool_called", 60.0, {"tool": "end_interview", "outcome": "refused"}),
               ("after", "tool_called", 80.0, {"tool": "end_interview", "outcome": "accepted_withdrew"}),
               ("after", "tool_called", 85.0, {"tool": "end_interview", "outcome": "after_end"}),
               ("dup", "tool_called", 60.2, {"tool": "end_interview", "outcome": "refused"}),
               ("only_ref", "tool_called", 50.0, {"tool": "end_interview", "outcome": "refused"}),
               ("legacy", "tool_called", 70.0, {"tool": "finish_interview", "outcome": "ended"})])
    s = await job(db)

    def end(iid):
        ends = [d for d in row(s, iid).decisions if d.phase == "ending"]
        assert len(ends) == 1, f"{iid}: exactly one ending ({ends})"
        return ends[0]

    def during(iid, action):
        return [d for d in row(s, iid).decisions if d.phase == "during_interview" and d.action == action]

    assert (end("acc").owner, end("acc").reason, end("acc").at_seconds) == ("mitra", "completed", 90.0)
    assert (end("ref").owner, end("ref").reason) == ("code", "time_limit"), "a refused call is not the ending"
    assert [(d.owner, d.at_seconds) for d in during("ref", "end_interview_refused")] == [("code", 60.0)]
    assert (end("after").owner, end("after").reason, end("after").at_seconds) == ("mitra", "withdrew", 80.0)
    assert [(d.owner, d.reason) for d in during("after", "end_interview_after_end")] == [("code", "already_over")]
    assert len(during("dup", "end_interview_refused")) == 1, "the event and the coordinator record are one decision"
    assert end("only_ref").owner == "code" and end("only_ref").reason == "plan_closed"
    assert (end("legacy").owner, end("legacy").action) == ("mitra", "finish_interview")


async def test_goal_writing_is_its_own_plan_part_beside_generation_and_refinement(db):
    """Round 18 (Session A, C-176): goal writing at booking is stored as "blueprint_goals";
    it is counted in the plan's cost as plan_goals, shown with generation and refinement.
    Expected only when the plan carries written goals; never read as $0 when missing."""
    goals_plan = {**BLUEPRINT, "competency_plans": [{**p, "question_goals": ["Learn x"] * len(p["question_ids"])}
                                                    for p in BLUEPRINT["competency_plans"]]}
    await seed(db, interviews=[{"id": "g", "metrics": metrics(), "cand": "c_g"},
                               {"id": "g2", "metrics": metrics(), "cand": "c_g"},
                               {"id": "nog", "metrics": metrics(), "cand": "c_nog"},
                               {"id": "old", "metrics": metrics(), "cand": "c_old"}],
               candidates={"c_g": [{"job": "blueprint", "cost_usd": 0.05}, {"job": "blueprint_goals", "cost_usd": 0.014}],
                           "c_nog": [{"job": "blueprint", "cost_usd": 0.05}],
                           "c_old": [{"job": "blueprint", "cost_usd": 0.05}]})
    from app.db import Candidate

    async with db.get_sessionmaker()() as ses:
        for cid in ("c_g", "c_nog"):  # both plans have goals; only c_g's cost was stored
            (await ses.get(Candidate, cid)).blueprint = goals_plan
        await ses.commit()
    s = await job(db)
    assert part(row(s, "g"), "plan_goals").status == "recorded" and part(row(s, "g"), "plan_goals").usd == 0.014
    assert part(row(s, "g2"), "plan_goals").status == "counted_elsewhere"
    assert part(row(s, "nog"), "plan_goals").status == "not_recorded" and "plan_goals" in row(s, "nog").cost.missing_parts
    assert part(row(s, "old"), "plan_goals").status == "not_applicable", "a plan made before goals existed"
    totals = {p.part: p for p in s.cost_by_part}
    assert totals["plan_goals"].usd == pytest.approx(0.014)
    assert row(s, "g").cost.recorded_usd == pytest.approx(sum(p.usd for p in row(s, "g").cost.parts if p.usd))
