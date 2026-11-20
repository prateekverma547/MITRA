"""Phase 5 polish, the backend part (Session B's audit B-111): what the candidate's
pages are told, and the HR pages' developer text.

- PA-04 (C-162): the check and join routes send the real role, booked length, area
  names and server times, only after correct credentials; the status and clock
  answers carry the current area's position.
- PA-18 (C-163): after correct credentials an expired link says "expired"; with wrong
  credentials every case is the same 401.
- PA-07 (C-164): a stored recording whose file is gone is "file_missing", never
  "stored"; each recording case has its own state.
- PA-15 (C-165): a 429 says how long to wait, and the wait reveals nothing about
  whether the interview exists.
- PA-28 (C-166): Check Intelligence carries stable codes and plain reasons, no field
  paths in reasons and no notes about sessions or requests.

The plans are real fixtures (bot/fixtures/pm_senior.json, ml_senior.json).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import interviews
from live import credential_limits
from live.credential_limits import ADDRESS_PER_MINUTE, PAIR_FAILURES, PAIR_WINDOW, CredentialLimits
from proctoring import intake, status
from tests.live.support import ADMIN_PASSWORD, seed_interview

BACKEND = Path(__file__).resolve().parents[1]
PM = json.loads((BACKEND / "bot" / "fixtures" / "pm_senior.json").read_text())
ML = json.loads((BACKEND / "bot" / "fixtures" / "ml_senior.json").read_text())
A = {"iid": "int_polish000001", "cid": "cand_polish_a", "mid": "111-222-333", "pw": "kmnp3478", "plan": PM, "minutes": 40}
B = {"iid": "int_polish000002", "cid": "cand_polish_b", "mid": "444-555-666", "pw": "rstv2345", "plan": ML, "minutes": 25}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/polish.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    async def fake_token(**kw):
        return "daily-token"

    async def fake_start_bot(*, interview_id, **kw):
        async with db.get_sessionmaker()() as s:  # as the real _start_bot does
            row = await s.get(db.Interview, interview_id)
            row.status, row.started_at = db.InterviewStatus.IN_PROGRESS, datetime.now(UTC)
            await s.commit()

    monkeypatch.setattr(interviews, "create_meeting_token", fake_token)
    monkeypatch.setattr(interviews, "_start_bot", fake_start_bot)
    credential_limits.limits.reset()
    intake.limiter.reset()
    status.status_limiter.reset()
    await db.reset_engine()
    with TestClient(main.app) as c:
        for x in (A, B):
            c.portal.call(lambda x=x: seed_interview(x["iid"], candidate_id=x["cid"], status="scheduled",
                                                     started_ago=None, meeting_id=x["mid"], password=x["pw"],
                                                     blueprint=x["plan"], booked_duration_minutes=x["minutes"]))
        yield c
    await db.reset_engine()
    credential_limits.limits.reset()


def check(client, mid, pw):
    return client.post("/interviews/check", json={"meeting_id": mid, "password": pw})


def join(client, mid, pw):
    return client.post("/interviews/join", json={"meeting_id": mid, "password": pw, "consent": True})


def expected_areas(plan):
    return ["Opening", *[p["name"] for p in plan["competency_plans"]], "Closing"]


def set_row(client, iid, **fields):
    from app import db

    async def go():
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, iid)
            for k, v in fields.items():
                setattr(row, k, v)
            await s.commit()

    client.portal.call(go)


# -- PA-04: the real interview, after correct credentials only ---------------------------------


def test_the_check_sends_this_interviews_own_role_length_and_areas(client):
    for x in (A, B):
        details = check(client, x["mid"], x["pw"]).json()["details"]
        assert details["role_title"] == x["plan"]["evaluation_spec"]["role_title"]
        assert details["booked_minutes"] == x["minutes"]
        assert details["areas"] == expected_areas(x["plan"])
        assert details["started_at"] is None and details["server_time"]
    a, b = (check(client, x["mid"], x["pw"]).json()["details"] for x in (A, B))
    assert (a["role_title"], a["booked_minutes"], a["areas"]) != (b["role_title"], b["booked_minutes"], b["areas"])
    assert "Senior AI/ML Engineer" != a["role_title"] or a["areas"] != ["Opening", "Applied LLM depth", "System design",
                                                                       "Production", "Closing"]


def test_the_join_sends_the_same_details_with_the_start_time(client):
    r = join(client, A["mid"], A["pw"])
    details = r.json()["details"]
    assert r.status_code == 200 and details["areas"] == expected_areas(PM) and details["booked_minutes"] == 40
    started = datetime.fromisoformat(details["started_at"])
    assert abs((datetime.now(UTC) - started).total_seconds()) < 30


def test_details_carry_names_only_nothing_internal(client):
    text = json.dumps(check(client, A["mid"], A["pw"]).json()["details"])
    for p in PM["competency_plans"]:
        assert p["competency_id"] not in text
        assert not any(q in text for q in p["seed_questions"])
    for word in ("weight", "emphasis", "red_flag", "hr_findings", "question_ids", "score"):
        assert word not in text


@pytest.mark.parametrize("mid,pw", [(A["mid"], "wrongpass"), ("999-999-999", A["pw"]), ("", "")])
def test_wrong_credentials_reveal_nothing(client, mid, pw):
    r = check(client, mid, pw)
    assert r.status_code == 401 and r.json() == {"detail": interviews.INVALID_CREDENTIALS}
    for leak in (PM["evaluation_spec"]["role_title"], *expected_areas(PM)[1:-1]):
        assert leak not in r.text


def _area_event(client, iid, area_id, seq_name):
    from live.emit import make_event
    from live.store import insert_events
    from shared.interview_contracts import AreaChanged

    client.portal.call(insert_events, [make_event(iid, AreaChanged(area_id=area_id, area_name="x"),
                                                  source="coordinator", at_seconds=10.0,
                                                  event_id=f"{iid}:{seq_name}")])


def test_the_status_and_clock_answers_carry_the_current_areas_position(client):
    join(client, A["mid"], A["pw"])
    token = intake.issue_token(A["iid"])
    headers = {"Authorization": f"Bearer {token}"}
    reading = {"kind": "integrity_status", "distance_cm": 62, "distance_in_band": True, "faces_in_view": 1,
               "page_visible": True}

    def area_now():
        status.status_limiter.reset()
        return client.post(f"/api/proctoring/interviews/{A['iid']}/status", json=reading,
                           headers=headers).json()["current_area_index"]

    assert area_now() is None  # before the first area change
    _area_event(client, A["iid"], "opening", "a1")
    assert area_now() == 0
    second = PM["competency_plans"][1]["competency_id"]
    _area_event(client, A["iid"], second, "a2")
    assert area_now() == 2 and expected_areas(PM)[2] == PM["competency_plans"][1]["name"]
    _area_event(client, A["iid"], "closing", "a3")
    assert area_now() == len(expected_areas(PM)) - 1
    intake.limiter.reset()
    from proctoring.routes import clock_limiter

    clock_limiter.reset()
    clock = client.get(f"/api/proctoring/interviews/{A['iid']}/clock", headers=headers).json()
    assert clock["current_area_index"] == len(expected_areas(PM)) - 1
    # Another interview's pass reads nothing of this one.
    other = {"Authorization": f"Bearer {intake.issue_token(B['iid'])}"}
    status.status_limiter.reset()
    assert client.post(f"/api/proctoring/interviews/{A['iid']}/status", json=reading,
                       headers=other).status_code == 401


# -- PA-18: expired, after correct credentials only ----------------------------------------------


def test_an_expired_link_says_expired_and_a_finished_one_taken_place(client):
    set_row(client, A["iid"], status="expired")
    set_row(client, B["iid"], status="completed")
    for route in (check, join):
        r = route(client, A["mid"], A["pw"])
        assert r.status_code == 410 and r.json()["reason"] == "expired"
        assert r.json()["detail"] == interviews.EXPIRED_DETAIL
        f = route(client, B["mid"], B["pw"])
        assert f.status_code == 410 and f.json()["reason"] == "taken_place"
        assert f.json()["detail"] == interviews.TAKEN_PLACE_DETAIL


def test_a_room_that_expired_before_anyone_joined_stays_expired_on_the_next_try(client):
    set_row(client, A["iid"], room_expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert join(client, A["mid"], A["pw"]).json()["reason"] == "expired"  # marks it EXPIRED
    again = join(client, A["mid"], A["pw"])
    assert again.status_code == 410 and again.json()["reason"] == "expired"  # was "already taken place"


def test_with_wrong_credentials_unknown_wrong_and_expired_get_one_answer(client):
    set_row(client, A["iid"], status="expired")
    answers = [check(client, A["mid"], "wrongpass"), check(client, B["mid"], "wrongpass"),
               check(client, "999-999-999", "wrongpass")]
    assert {(r.status_code, r.text) for r in answers} == {(401, json.dumps({"detail": interviews.INVALID_CREDENTIALS},
                                                                          separators=(",", ":")))}


# -- PA-15: how long to wait ------------------------------------------------------------------


def test_the_limiter_says_how_long_for_each_rule():
    lim = CredentialLimits()
    for i in range(PAIR_FAILURES):
        lim.refuse("1.1.1.1", "m", now=float(i))
        lim.failed("1.1.1.1", "m", now=float(i))
    reason, wait = lim.refusal("1.1.1.1", "m", now=100.0)
    assert reason == "pair" and wait == int(PAIR_WINDOW - 100.0)  # the first failure ages out at 900 s
    lim2 = CredentialLimits()
    for i in range(ADDRESS_PER_MINUTE):
        lim2.refuse("2.2.2.2", f"m{i}", now=10.0 + i * 0.5)
    reason, wait = lim2.refusal("2.2.2.2", "x", now=40.0)
    assert reason == "address" and wait == 30  # the first attempt (10 s) ages out at 70 s


def test_a_429_carries_the_wait_and_it_is_the_same_whether_the_interview_exists(client):
    answers = {}
    for name, mid in (("exists", A["mid"]), ("unknown", "999-999-999")):
        credential_limits.limits.reset()
        for _ in range(PAIR_FAILURES):
            assert check(client, mid, "wrongpass").status_code == 401
        r = check(client, mid, "wrongpass")
        assert r.status_code == 429 and r.json()["reason"] == "too_many_attempts"
        assert r.headers["Retry-After"] == str(r.json()["retry_after_seconds"])
        answers[name] = r
        if name == "exists":
            # The right password is refused the same way meanwhile (no hint that it is right).
            same = check(client, A["mid"], A["pw"])
            assert same.status_code == 429 and same.json() == r.json()
    assert answers["exists"].json() == answers["unknown"].json()
    assert 1 <= answers["exists"].json()["retry_after_seconds"] <= PAIR_WINDOW


# -- PA-07: each recording case has its own state ------------------------------------------------


def _recording(client, iid):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    return client.get(f"/api/live/interviews/{iid}/recording").json()


def test_each_recording_case_has_its_own_state(client, tmp_path, monkeypatch):
    from app.recordings import DOWNLOAD_FAILING_AFTER

    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "rec"))
    cases = {
        "not_recorded": {"recording_status": "not_recorded"},
        "recording": {"recording_status": "recording"},
        "download_failing": {"recording_status": "recording", "recording_download_failures": DOWNLOAD_FAILING_AFTER},
        "expired": {"recording_status": "expired"},
        "file_missing": {"recording_status": "stored", "recording_bytes": 10, "recording_offset_seconds": 1.0,
                         "recording_path": f"{A['iid']}.mp4"},  # as a stored row has; the file is not there
    }
    seen = {}
    for name, fields in cases.items():
        set_row(client, A["iid"], **{"recording_download_failures": 0, **fields})
        seen[name] = _recording(client, A["iid"])["status"]
    assert seen == {name: name for name in cases}
    # Stored and on disk: "stored", with something to play.
    (tmp_path / "rec").mkdir(exist_ok=True)
    (tmp_path / "rec" / f"{A['iid']}.mp4").write_bytes(b"0" * 10)
    ready = _recording(client, A["iid"])
    assert ready["status"] == "stored" and ready["media_url"]


# -- PA-28: stable codes, plain reasons ----------------------------------------------------------


def test_check_intelligence_sends_codes_and_plain_reasons_only():
    from live import check_intel

    run = json.loads((BACKEND / "tests" / "fixtures" / "check_intel" / "live_run_int_99bdc4598805.json").read_text())
    data = check_intel.build(interview_id=run["interview_id"], status="completed", started_at=None, ended_at=None,
                             transcript=run["transcript"], session_metrics=run["session_metrics"], blueprint=None,
                             events=[])
    assert data["unfilled"] and all(u["code"] for u in data["unfilled"])
    for u in data["unfilled"]:
        assert "Session" not in u["reason"] and "C-1" not in u["reason"]
        assert "_" not in u["reason"] and "[]" not in u["reason"] and u["reason"][0].isupper()
    assert not any("plan generation" in u["field"] for u in data["unfilled"])  # the stale note is gone
    notes = data["after_last_turn"] + [n for r in data["rows"] for n in r["notes"]]
    assert notes and all("code" in n for n in notes)
    assert [n["code"] for n in notes if n["type"] == "cut"] == ["reply_cut"] * sum(n["type"] == "cut" for n in notes)
