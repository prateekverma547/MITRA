"""Track H integration (Session D, D-92, D-93): their room posts integrity events to our intake,
and their console reads the STORED integrity report. Through the real app and its routes."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

PASSWORD = "room-pw-canary-77"
ROOM_URL = "https://example.daily.co/canary-room"
SAID = "transcript-canary-words"
FIVE = ["looking_away", "face_missing", "second_face", "share_stopped", "second_monitor"]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/th.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.setenv("SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "rec"))
    monkeypatch.delenv("AFTERCALL_ENABLED", raising=False)
    from aftercall import runner
    from proctoring import intake, status

    async def idle():  # the start-up loop is replaced: the tests call the writer themselves
        return None

    monkeypatch.setattr(runner, "run_forever", idle)

    async def keep_running(*_a, **_k):  # C-130's start-up clean-up would mark a bot-less
        return {}                       # running interview failed; here it stays running
    import app.startup

    monkeypatch.setattr(app.startup, "reconcile_running_interviews", keep_running)
    intake.limiter.reset()
    status.status_limiter.reset() if hasattr(status.status_limiter, "reset") else None
    return tmp_path


async def seed(*rows):
    from app import db

    await db.reset_engine()
    await db.create_all()
    async with db.get_sessionmaker()() as s:
        s.add(db.Job(id="job_th", jd_text="jd", spec_status=db.SpecStatus.READY))
        await s.commit()
    async with db.get_sessionmaker()() as s:
        s.add(db.Candidate(id="cand_th", job_id="job_th", name="Constructed", cv_text="cv",
                           blueprint_status=db.BlueprintStatus.READY))
        await s.commit()
    now = datetime.now(UTC)
    async with db.get_sessionmaker()() as s:
        for iid, status in rows:
            s.add(db.Interview(id=iid, candidate_id="cand_th", meeting_id=f"m-{iid}", password=PASSWORD,
                               daily_room_name="canary-room", daily_room_url=ROOM_URL,
                               room_expires_at=now + timedelta(hours=1), status=status,
                               started_at=now - timedelta(minutes=10),
                               transcript={"turns": [{"speaker": "candidate", "text": SAID, "at_seconds": 1}]}))
        await s.commit()


async def end(iid, ago_seconds=0):
    from app import db

    async with db.get_sessionmaker()() as s:
        row = await s.get(db.Interview, iid)
        row.status = db.InterviewStatus.COMPLETED
        row.ended_at = datetime.now(UTC) - timedelta(seconds=ago_seconds)
        await s.commit()


def ev(event_id, kind, start, end=None, **extra):
    return {"event_id": event_id, "kind": kind, "source": "browser", "start_seconds": start, "end_seconds": end,
            "reminder": "recorded_only", **extra}


def post(c, iid, token, events, raw=None):
    return c.post(f"/api/proctoring/interviews/{iid}/events", headers={"Authorization": f"Bearer {token}"},
                  content=raw if raw is not None else json.dumps({"events": events}))


def client():
    from app import main

    return TestClient(main.app)


# -- 1. the endpoint -----------------------------------------------------------------------------------


async def test_all_five_types_are_stored_with_confidence_and_the_servers_receive_time(app_env):
    from proctoring import intake
    from proctoring.intake import stored_events

    await seed(("int_live", "in_progress"))
    tok = intake.issue_token("int_live")
    with client() as c:
        events = [ev(f"th-{k}", k, 10.0 + i, 12.0 + i, confidence=0.5 + i / 10) for i, k in enumerate(FIVE)]
        events[0]["received_at"] = "2000-01-01T00:00:00Z"  # a client cannot set the server's fields
        r = post(c, "int_live", tok, events)
        assert r.status_code == 200 and r.json()["stored"] == 5 and r.json()["refused"] == []
    stored = await stored_events("int_live")
    assert sorted(e.kind.value for e in stored) == sorted(FIVE)
    for e in stored:
        assert e.confidence is not None and e.received_at is not None and e.received_at.year > 2000
        assert e.received_interview_seconds is not None and e.received_interview_seconds > 500


async def test_wrong_token_another_interviews_token_and_no_token_are_refused(app_env):
    from proctoring import intake

    await seed(("int_a", "in_progress"), ("int_b", "in_progress"))
    with client() as c:
        for tok in ("not-a-token", intake.issue_token("int_b"), ""):
            r = post(c, "int_a", tok, [ev("x1", "face_missing", 5.0)])
            assert r.status_code == 401 and "x1" not in r.text


async def test_only_while_live_plus_a_short_grace(app_env):
    from proctoring import intake

    await seed(("int_g", "in_progress"), ("int_old", "in_progress"), ("int_sched", "scheduled"))
    await end("int_g", ago_seconds=30)
    await end("int_old", ago_seconds=intake.LATE_GRACE_SECONDS + 60)
    with client() as c:
        assert post(c, "int_g", intake.issue_token("int_g"), [ev("g1", "face_missing", 5.0, 6.0)]).json()["stored"] == 1
        late = post(c, "int_old", intake.issue_token("int_old"), [ev("o1", "face_missing", 5.0, 6.0)])
        assert late.status_code == 409
    from app import db

    async with db.get_sessionmaker()() as s:
        row = await s.get(db.Interview, "int_sched")
        row.started_at = None
        await s.commit()
    with client() as c:
        assert post(c, "int_sched", intake.issue_token("int_sched"), [ev("s1", "face_missing", 1.0)]).status_code == 409


async def test_unknown_types_bad_ids_and_oversized_bodies_are_refused_and_nothing_is_echoed(app_env):
    from proctoring import intake
    from proctoring.intake import stored_events

    await seed(("int_u", "in_progress"))
    tok = intake.issue_token("int_u")
    with client() as c:
        r = post(c, "int_u", tok, [ev("u1", "gaze_away_typo", 5.0), ev("<script>echo-me</script>", "face_missing", 5.0),
                                   ev("u3", "face_missing", 5.0, detail="looked cheat sheet"),
                                   ev("u4", "second_voice", 5.0)])
        body = r.json()
        assert r.status_code == 200 and body["stored"] == 0
        assert [(x["index"], x["event_id"], x["code"]) for x in body["refused"]] == [
            (0, "u1", "unknown_type"), (1, None, "invalid_event_id"), (2, "u3", "detail_not_neutral"),
            (3, "u4", "after_call_only")]
        for echo in ("gaze_away_typo", "echo-me", "script", "cheat"):
            assert echo not in r.text, f"{echo} echoed back"
        big = post(c, "int_u", tok, None, raw=json.dumps({"events": [ev("b", "face_missing", 1.0, detail="x" * 100)] * 600}))
        assert big.status_code == 413 and "xxxx" not in big.text
        bad = post(c, "int_u", tok, None, raw='{"events": [], "image": "AAAA"}')
        assert bad.status_code == 422 and "AAAA" not in bad.text
    assert await stored_events("int_u") == []


async def test_a_retry_with_the_same_event_id_is_stored_once(app_env):
    from proctoring import intake
    from proctoring.intake import stored_events

    await seed(("int_d", "in_progress"))
    tok = intake.issue_token("int_d")
    with client() as c:
        e = ev("same-1", "share_stopped", 20.0, 25.0, confidence=0.9)
        assert post(c, "int_d", tok, [e]).json()["stored"] == 1
        again = post(c, "int_d", tok, [e]).json()
        assert again["stored"] == 0 and again["duplicates"] == 1
    assert len(await stored_events("int_d")) == 1


async def test_open_then_close_with_confidence(app_env):
    from proctoring import intake
    from proctoring.intake import stored_events

    await seed(("int_c", "in_progress"))
    tok = intake.issue_token("int_c")
    with client() as c:
        assert post(c, "int_c", tok, [ev("oc", "second_face", 30.0)]).json()["stored"] == 1
        assert post(c, "int_c", tok, [ev("oc", "second_face", 30.0, 41.0, confidence=0.8)]).json()["closed"] == 1
    (e,) = await stored_events("int_c")
    assert (e.end_seconds, e.confidence) == (41.0, 0.8) and e.received_at is not None


async def test_a_burst_is_rate_limited(app_env):
    from proctoring import intake

    await seed(("int_r", "in_progress"))
    tok = intake.issue_token("int_r")
    with client() as c:
        codes = [post(c, "int_r", tok, [ev(f"r{n}", "face_missing", 1.0, 2.0)]).status_code
                 for n in range(intake.RATE_LIMIT_BATCHES + 5)]
    assert codes.count(200) == intake.RATE_LIMIT_BATCHES and codes.count(429) == 5


# -- 2. the stored report ------------------------------------------------------------------------------


async def test_the_report_is_stored_at_the_end_served_as_stored_and_survives_a_restart(app_env):
    from proctoring import intake, stored

    await seed(("int_s", "in_progress"))
    tok = intake.issue_token("int_s")
    with client() as c:
        c.post("/admin/login", json={"password": "test-admin-pw"})
        post(c, "int_s", tok, [ev("s1", "looking_away", 10.0, 14.0, confidence=0.7),
                               ev("s2", "looking_away", 30.0, 31.5), ev("s3", "face_missing", 50.0),
                               ev("s4", "second_monitor", 0.0, 120.0)])
        assert c.get("/api/proctoring/interviews/int_s/integrity").json()["detail"] == "not_stored_yet"
        assert await stored.write_pending() == 0, "nothing is stored while the interview runs"
        await end("int_s", ago_seconds=intake.LATE_GRACE_SECONDS + 5)
        assert await stored.write_pending() == 1
    from app import db

    await db.reset_engine()  # a restart: a new engine and a new app instance
    with client() as c:
        assert c.get("/api/proctoring/interviews/int_s/integrity").status_code == 401
        c.post("/admin/login", json={"password": "test-admin-pw"})
        r = c.get("/api/proctoring/interviews/int_s/integrity")
        assert r.status_code == 200
        for secret in (PASSWORD, ROOM_URL, "canary-room", SAID):
            assert secret not in r.text, f"{secret} in the report"
        rep = r.json()
        totals = {t["kind"]: t for t in rep["by_type"]}
        assert (totals["looking_away"]["count"], totals["looking_away"]["total_seconds"]) == (2, 5.5)
        assert [x["start_seconds"] for x in totals["looking_away"]["ranges"]] == [10.0, 30.0]
        assert totals["looking_away"]["ranges"][0]["confidence"] == 0.7
        assert (totals["face_missing"]["count"], totals["face_missing"]["open_events"], totals["face_missing"]["total_seconds"]) == (1, 1, 0.0)
        assert rep["status"] == "final" and rep["purpose"] == "signals_for_review" and rep["scored"] is False
        assert rep["interview_status"] == "completed" and len(rep["report"]["events"]) == 4
        assert c.get("/api/proctoring/interviews/nope/integrity").json()["detail"] == "no_such_interview"
    assert await stored.write_pending() == 0, "a final report is not rewritten for nothing"


async def test_an_after_call_result_and_a_late_event_update_the_stored_report(app_env, monkeypatch):
    from aftercall import store as ac_store
    from proctoring import intake, stored
    from shared.interview_contracts import (
        AfterCallCheck, AfterCallCheckName, AfterCallState, AfterCallStatus, IntegrityEvent, SecondVoiceSignal,
        TimeRange)

    monkeypatch.setenv("AFTERCALL_ENABLED", "1")
    await seed(("int_ac", "in_progress"))
    tok = intake.issue_token("int_ac")
    with client() as c:
        post(c, "int_ac", tok, [ev("a1", "face_missing", 5.0, 9.0)])
    await end("int_ac", ago_seconds=10)  # inside the late grace
    assert (await stored.store("int_ac")).status.value == "awaiting_late_events"
    with client() as c:
        assert post(c, "int_ac", tok, [ev("a2", "share_stopped", 100.0, 110.0)]).json()["stored"] == 1  # a late event
    assert "int_ac" in await stored.pending()
    await end("int_ac", ago_seconds=intake.LATE_GRACE_SECONDS + 5)
    assert (await stored.store("int_ac")).status.value == "awaiting_after_call"
    t = datetime.now(UTC)
    ac_store.write_status(AfterCallStatus(interview_id="int_ac", updated_at=t, checks=[
        AfterCallCheck(check=AfterCallCheckName.SECOND_VOICE, state=AfterCallState.DONE)]))
    sig = SecondVoiceSignal(interview_id="int_ac", ranges=[TimeRange(start_seconds=40, end_seconds=44)], confidence=0.3,
                            method="m", method_version="1", wording="A voice other than the candidate's may be audible.",
                            integrity_event_id="aftercall-second-voice-int_ac")
    ac_store.write_signal(sig)
    from aftercall.runner import integrity_event

    ac_store.write_events("int_ac", [integrity_event(sig)])
    assert "int_ac" in await stored.pending()
    assert await stored.write_pending() == 1
    rep = await stored.load("int_ac")
    assert rep.status.value == "final" and rep.second_voice is not None
    assert {t.kind.value for t in rep.by_type} == {"face_missing", "share_stopped", "second_voice"}
    assert rep.after_call_status.checks[0].state.value == "done"


async def test_a_running_or_never_run_interview_has_no_stored_report(app_env):
    from proctoring import stored

    await seed(("int_run", "in_progress"), ("int_ns", "no_show"))
    assert await stored.build("int_run") is None and await stored.build("int_ns") is None
    assert await stored.write_pending() == 0
