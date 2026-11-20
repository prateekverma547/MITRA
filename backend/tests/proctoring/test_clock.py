"""The server is the clock: interview time now, exact or marked as an estimate."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from live import clock
from proctoring import intake, status
from tests.live.support import seed_interview
from tests.proctoring.test_intake import IID, OTHER, client, ev, post  # noqa: F401

NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)


def test_with_a_stored_zero_the_clock_is_exact():
    interview = SimpleNamespace(transcript_zero_at=NOW - timedelta(seconds=125.5),
                                started_at=NOW - timedelta(seconds=200))
    got = clock.interview_time_now(interview, NOW)
    assert got == {"interview_seconds": 125.5, "estimate": False, "basis": "transcript_zero",
                   "server_time": NOW.isoformat()}


def test_without_a_zero_the_clock_is_an_estimate_from_started_at(monkeypatch):
    monkeypatch.delenv(clock.ESTIMATE_OFFSET_ENV, raising=False)
    interview = SimpleNamespace(transcript_zero_at=None, started_at=NOW - timedelta(seconds=100))
    got = clock.interview_time_now(interview, NOW)
    assert got["estimate"] is True and got["basis"] == "started_at_minus_offset"
    assert got["interview_seconds"] == round(100 - clock.DEFAULT_ESTIMATE_OFFSET_SECONDS, 3)
    monkeypatch.setenv(clock.ESTIMATE_OFFSET_ENV, "6.32")
    assert clock.interview_time_now(interview, NOW)["interview_seconds"] == 93.68


def test_a_naive_stored_time_is_read_as_utc_and_an_unstarted_one_is_null():
    naive = SimpleNamespace(transcript_zero_at=(NOW - timedelta(seconds=10)).replace(tzinfo=None),
                            started_at=None)
    assert clock.interview_time_now(naive, NOW)["interview_seconds"] == 10.0
    unstarted = SimpleNamespace(transcript_zero_at=None, started_at=None)
    assert clock.interview_time_now(unstarted, NOW)["interview_seconds"] is None


def test_a_bad_offset_setting_falls_back(monkeypatch):
    monkeypatch.setenv(clock.ESTIMATE_OFFSET_ENV, "soon")
    assert clock.estimate_offset_seconds() == clock.DEFAULT_ESTIMATE_OFFSET_SECONDS


# -- through the routes ---------------------------------------------------------------


def _get_clock(client, *, token=None, interview=IID):
    token = intake.issue_token(interview) if token is None else token
    return client.get(f"/api/proctoring/interviews/{interview}/clock",
                      headers={"Authorization": f"Bearer {token}"} if token else {})


def test_the_clock_route_answers_with_an_estimate_until_the_zero_is_stored(client):
    r = _get_clock(client)
    assert r.status_code == 200
    c = r.json()["clock"]
    assert c["estimate"] is True and 590 < c["interview_seconds"] < 600  # running ~600 s


def test_the_clock_route_is_exact_once_the_zero_is_stored(client):
    from app import db

    async def store_zero():
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, IID)
            row.transcript_zero_at = datetime.now(UTC) - timedelta(seconds=42)
            await s.commit()

    client.portal.call(store_zero)
    c = _get_clock(client).json()["clock"]
    assert c["estimate"] is False and c["basis"] == "transcript_zero"
    assert 41.5 < c["interview_seconds"] < 43.5


@pytest.mark.parametrize("token", ["", "v1.proctoring-intake.int_proc00000001.9999999999.00"])
def test_the_clock_route_needs_this_interviews_token(client, token):
    assert _get_clock(client, token=token).status_code == 401


def test_the_clock_route_refuses_another_interviews_token_and_a_closed_interview(client):
    assert _get_clock(client, token=intake.issue_token(OTHER)).status_code == 401
    client.portal.call(lambda: seed_interview("int_proc00000009", candidate_id="c9",
                                              status="completed", ended_ago=3600))
    assert _get_clock(client, interview="int_proc00000009").status_code == 409


def test_intake_and_status_responses_carry_the_clock(client):
    body = post(client, [ev("c1")]).json()
    assert body["clock"]["estimate"] is True and body["clock"]["interview_seconds"] > 0
    status.status_limiter.reset()
    r = client.post(f"/api/proctoring/interviews/{IID}/status",
                    json={"kind": "integrity_status", "distance_cm": 60},
                    headers={"Authorization": f"Bearer {intake.issue_token(IID)}"})
    assert r.status_code == 200 and set(r.json()["clock"]) == {"interview_seconds", "estimate",
                                                                "basis", "server_time"}
