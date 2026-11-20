"""The candidate room's lookup: what the page may know before consent.

Booking goes through the real HR-round and technical routes, with Daily and the
plan builder stubbed by their own test modules' fixtures.
"""

import pytest

from tests.console_support import org_client, sign_in  # noqa: F401
from tests.hiring.test_hr_round import ready_candidate
from tests.hiring.test_hr_round import stub_daily as _hr_stub_daily  # noqa: F401
from tests.hiring.test_technical import at_stage, set_interview
from tests.hiring.test_technical import stubs as _tech_stubs  # noqa: F401


@pytest.fixture
def stub_daily(_hr_stub_daily):
    return _hr_stub_daily


async def book_hr(client) -> dict:
    cid = await ready_candidate(client)
    sign_in(client, "hr@mitra.local")
    r = client.post(f"/api/candidates/{cid}/hr-round").json()["round"]
    client.cookies.clear()
    return r


async def test_an_hr_round_is_described_without_signing_in(org_client, stub_daily):
    r = await book_hr(org_client)
    found = org_client.post("/api/room/lookup", json={"meeting_id": r["meeting_id"], "password": r["password"]})
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["round"] == "hr" and body["status"] == "open"
    # An HR round is evaluated from its recording, so it is always recorded.
    assert body["recording"] is True and body["retention_days"] >= 1
    assert body["duration_minutes"] == 30 and body["role_title"]


async def test_wrong_credentials_get_one_message(org_client, stub_daily):
    r = await book_hr(org_client)
    wrong_password = org_client.post("/api/room/lookup", json={"meeting_id": r["meeting_id"], "password": "nope"})
    no_such_meeting = org_client.post("/api/room/lookup", json={"meeting_id": "999-999-999", "password": r["password"]})
    garbage = org_client.post("/api/room/lookup", json={"meeting_id": "abc", "password": "x"})
    assert wrong_password.status_code == no_such_meeting.status_code == garbage.status_code == 401
    assert wrong_password.json() == no_such_meeting.json() == garbage.json()


async def test_lookup_changes_nothing(org_client, stub_daily):
    from app import db
    from app.db import Interview

    r = await book_hr(org_client)
    org_client.post("/api/room/lookup", json={"meeting_id": r["meeting_id"], "password": r["password"]})
    async with db.get_sessionmaker()() as session:
        interview = await session.get(Interview, r["interview_id"])
        assert interview.status == "scheduled" and interview.consent_accepted_at is None


async def test_a_technical_round_reports_its_plan_length_and_recording_switch(org_client, monkeypatch):
    from bot.config import RECORDING_ENABLED_ENV

    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="ready")
    sign_in(org_client, "hr@mitra.local")
    b = org_client.post(f"/api/candidates/{cid}/technical").json()["booking"]
    org_client.cookies.clear()
    creds = {"meeting_id": b["meeting_id"], "password": b["password"]}

    monkeypatch.setenv(RECORDING_ENABLED_ENV, "0")
    body = org_client.post("/api/room/lookup", json=creds).json()
    assert body["round"] == "technical" and body["duration_minutes"] == b["duration_minutes"]
    assert body["recording"] is False and body["bot_name"]

    monkeypatch.setenv(RECORDING_ENABLED_ENV, "1")
    assert org_client.post("/api/room/lookup", json=creds).json()["recording"] is True

    await set_interview(cid, status="completed")
    assert org_client.post("/api/room/lookup", json=creds).json()["status"] == "finished"
    await set_interview(cid, status="expired")
    assert org_client.post("/api/room/lookup", json=creds).json()["status"] == "closed"


def test_meet_redirects_into_the_console_and_keeps_only_the_meeting_id(org_client):
    response = org_client.get("/meet?id=428-193-756&password=secret", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/console/#/meet?id=428-193-756"
    assert org_client.get("/meet", follow_redirects=False).headers["location"] == "/console/#/meet"


async def test_the_rooms_diagnostics_need_its_proctoring_token_and_are_kept_as_lines(org_client, tmp_path, monkeypatch):
    """Measurement only (2026-11-20): the room's timings, authorised like the integrity intake."""
    from hiring import diagnostics
    from proctoring.intake import issue_token

    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", tmp_path)
    body = {"phase": "end", "seconds_in_call": 120, "tab_age_seconds_at_join": 40, "earlier_calls_in_tab": 0,
            "face_check_load_ms": 3200, "face_check_status": "ready", "join_request_ms": 900, "daily_join_ms": 1500,
            "long_tasks": {"count": 12, "total_ms": 2100, "max_ms": 640, "first_minute_count": 10, "first_minute_ms": 1900},
            "mic": {"echo_cancellation": True, "noise_suppression": True, "auto_gain_control": True, "sample_rate": 48000}}
    assert org_client.post("/api/room/diagnostics/int_x", json=body).status_code == 401
    assert org_client.post("/api/room/diagnostics/int_x", json={**body, "video": "frame"},
                           headers={"Authorization": f"Bearer {issue_token('int_x')}"}).status_code == 422
    ok = org_client.post("/api/room/diagnostics/int_x", json=body, headers={"Authorization": f"Bearer {issue_token('int_x')}"})
    assert ok.status_code == 204
    import json

    line = json.loads((tmp_path / "int_x.jsonl").read_text().splitlines()[0])
    assert line["face_check_load_ms"] == 3200 and line["long_tasks"]["count"] == 12
