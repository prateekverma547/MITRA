"""The proctoring intake and the IntegrityReport assembled from it.

Requirements (the Session C brief, DEV_PLAN.md section 7): browser kinds only;
size and rate limits; idempotent by event_id; nothing outside the interview's
time window; an integrity_signal emitted per stored event; the report's summary
matches its events; no images or frames.
"""

import json

import pytest
from fastapi.testclient import TestClient

from live import tokens
from live.store import all_events
from proctoring import intake
from shared.interview_contracts import IntegrityReport
from tests.live.support import ADMIN_PASSWORD, seed_interview

IID = "int_proc00000001"
OTHER = "int_proc00000002"


def ev(event_id: str, kind: str = "tab_hidden", *, start: float = 30.0, source: str = "browser",
       reminder: str = "recorded_only", index: int | None = None, **extra) -> dict:
    return {"event_id": event_id, "kind": kind, "source": source, "start_seconds": start,
            "reminder": reminder, "reminder_index": index, **extra}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/proc.db")
    # The join test reaches Settings.load(); set the keys here rather than rely
    # on an earlier test having set them (it passed only in some orders).
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    intake.limiter.reset()
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        c.portal.call(seed_interview, IID)  # in progress for 10 minutes
        c.portal.call(lambda: seed_interview(OTHER, candidate_id="cand_other"))
        yield c
    await db.reset_engine()
    intake.limiter.reset()


def post(client, events, *, token=None, interview=IID, **body):
    token = intake.issue_token(interview) if token is None else token
    return client.post(f"/api/proctoring/interviews/{interview}/events",
                       json={"events": events, **body},
                       headers={"Authorization": f"Bearer {token}"} if token else {})


# -- auth -------------------------------------------------------------------------------


def test_a_valid_token_is_accepted(client):
    r = post(client, [ev("e1")])
    assert r.status_code == 200, r.text
    body = r.json()
    clock = body.pop("clock")
    assert body == {"stored": 1, "closed": 0, "duplicates": 0, "ignored": [], "refused": [],
                    "accommodation_set": False}
    assert clock["estimate"] is True and clock["basis"] == "started_at_minus_offset"


@pytest.mark.parametrize("make_token", [
    lambda: "",                                                    # none
    lambda: "v1.proctoring-intake.int_proc00000001.9999999999.00",  # forged signature
    lambda: intake.issue_token(OTHER),                              # another interview's
    lambda: tokens.issue("evidence", IID, 600)[0],                  # another purpose's
    lambda: tokens.issue(intake.PURPOSE, IID, -1)[0],               # expired
])
def test_any_other_token_is_refused(client, make_token):
    token = make_token()
    r = client.post(f"/api/proctoring/interviews/{IID}/events", json={"events": [ev("e1")]},
                    headers={"Authorization": f"Bearer {token}"} if token else {})
    assert r.status_code == 401


def test_join_issues_a_token_for_that_interview_only(client, monkeypatch):
    from app import interviews

    async def fake_token(**kw):
        return "daily-token"

    async def fake_start_bot(**kw):
        return None

    monkeypatch.setattr(interviews, "create_meeting_token", fake_token)
    monkeypatch.setattr(interviews, "_start_bot", fake_start_bot)
    client.portal.call(lambda: seed_interview("int_proc00000003", candidate_id="cand_join",
                                              status="scheduled", started_ago=None,
                                              meeting_id="123-456-789"))
    r = client.post("/interviews/join", json={"meeting_id": "123-456-789", "password": "pw",
                                              "consent": True})
    assert r.status_code == 200, r.text
    token = r.json()["proctoring_token"]
    assert intake.token_valid_for(token, "int_proc00000003")
    assert not intake.token_valid_for(token, IID)
    assert tokens.verify(token, "evidence") is None


# -- validation -------------------------------------------------------------------------


def test_after_call_kinds_and_sources_are_refused_from_a_browser(client):
    r = post(client, [
        ev("ok"),
        ev("voice", "second_voice"),                          # after-call kind, browser source
        ev("voice2", "second_voice", source="after_call"),    # after-call kind, claimed source
        ev("gaze", "looking_away", source="after_call"),      # either-source kind, wrong source
    ])
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] == 1
    assert {x["event_id"] for x in body["refused"]} == {"voice", "voice2", "gaze"}


def test_events_outside_the_time_window_are_refused(client):
    # The interview has run for 600 s; slack is 30 s.
    r = post(client, [ev("now", start=590), ev("future", start=700),
                      ev("ends_late", start=500, end_seconds=900)])
    body = r.json()
    assert body["stored"] == 1
    assert {x["event_id"] for x in body["refused"]} == {"future", "ends_late"}
    assert all(x["code"] == "outside_window" for x in body["refused"])


def test_an_interview_that_is_not_running_takes_nothing(client):
    client.portal.call(lambda: seed_interview("int_proc00000004", candidate_id="c4",
                                              status="scheduled", started_ago=None))
    client.portal.call(lambda: seed_interview("int_proc00000005", candidate_id="c5",
                                              status="completed", ended_ago=3600))
    client.portal.call(lambda: seed_interview("int_proc00000006", candidate_id="c6",
                                              status="completed", ended_ago=30))
    assert post(client, [ev("a")], interview="int_proc00000004").status_code == 409
    assert post(client, [ev("a")], interview="int_proc00000005").status_code == 409
    # Just ended: the browser's last flush still lands.
    assert post(client, [ev("a", start=500)], interview="int_proc00000006").json()["stored"] == 1


def test_contract_violations_are_refused_per_event(client):
    r = post(client, [ev("nudged_without_index", reminder="nudged"),
                      ev("accusing", detail="looked suspicious"),
                      ev("fine", "paste")])
    body = r.json()
    assert body["stored"] == 1
    assert {x["event_id"] for x in body["refused"]} == {"nudged_without_index", "accusing"}


def test_size_limits(client):
    # Too many events in one batch.
    assert post(client, [ev(f"e{i}") for i in range(intake.MAX_EVENTS_PER_BATCH + 1)]).status_code == 422
    # A full batch of the longest allowed events still fits under the byte limit...
    full = [ev(f"e{i}", detail="x" * 120) for i in range(intake.MAX_EVENTS_PER_BATCH)]
    assert len(json.dumps({"events": full})) < intake.MAX_BODY_BYTES
    assert post(client, full).status_code == 200
    # ...and a body over the limit is refused before it is parsed.
    huge = json.dumps({"events": [ev("x")]}) + " " * intake.MAX_BODY_BYTES
    r = client.post(f"/api/proctoring/interviews/{IID}/events", content=huge,
                    headers={"Authorization": f"Bearer {intake.issue_token(IID)}",
                             "content-type": "application/json"})
    assert r.status_code == 413


def test_no_frames_or_images_can_be_sent(client):
    r = post(client, [ev("e1")], frame="data:image/jpeg;base64,AAAA")
    assert r.status_code == 422


def test_rate_limit_per_interview(client):
    for i in range(intake.RATE_LIMIT_BATCHES):
        assert post(client, [ev(f"r{i}")]).status_code == 200
    assert post(client, [ev("over")]).status_code == 429
    # Another interview has its own budget.
    assert post(client, [ev("o1")], interview=OTHER).status_code == 200


def test_resent_events_are_stored_once(client):
    first = post(client, [ev("a"), ev("b")]).json()
    again = post(client, [ev("a"), ev("b"), ev("c")]).json()
    assert first["stored"] == 2
    assert again["stored"] == 1 and again["duplicates"] == 2


def test_each_stored_event_emits_one_integrity_signal(client):
    post(client, [ev("s1", "distance_out_of_band", reminder="nudged", index=1),
                  ev("s2", "face_missing")])
    post(client, [ev("s1", "distance_out_of_band", reminder="nudged", index=1)])  # resent

    async def flushed():
        from live.emit import web_emitter
        import asyncio

        for _ in range(100):
            if web_emitter().written >= 2:
                break
            await asyncio.sleep(0.02)
        return await all_events(IID)

    stored = client.portal.call(flushed)
    signals = [e for e in stored if e.kind.value == "integrity_signal"]
    assert sorted(s.payload.integrity_event_id for s in signals) == ["s1", "s2"]
    s1 = next(s for s in signals if s.payload.integrity_event_id == "s1")
    assert s1.source.value == "browser" and s1.payload.reminder_index == 1 and s1.at_seconds == 30.0


# -- the report --------------------------------------------------------------------------


def test_report_summary_matches_the_stored_events_and_the_accommodation(client):
    post(client, [ev("d1", "distance_out_of_band", start=40, reminder="nudged", index=1),
                  ev("d2", "distance_out_of_band", start=90, reminder="nudged", index=2),
                  ev("t1", "tab_hidden", start=60, end_seconds=64),
                  ev("g1", "looking_away", start=70)],
         accommodation={"gaze_reminders_off": True})
    # A later attempt to change the accommodation does not overwrite consent.
    post(client, [], accommodation={"gaze_reminders_off": False})
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    r = client.get(f"/api/proctoring/interviews/{IID}/report")
    assert r.status_code == 200
    report = IntegrityReport.model_validate(r.json())  # the contract re-checks the summary
    assert [e.event_id for e in report.events] == ["d1", "t1", "g1", "d2"]  # by time
    assert report.summary.events_by_kind == {"distance_out_of_band": 2, "tab_hidden": 1, "looking_away": 1}
    assert report.summary.reminders_by_kind == {"distance_out_of_band": 2}
    assert report.accommodation.gaze_reminders_off is True
    assert report.analysed_after_call is False


def test_report_needs_the_admin_session(client):
    assert client.get(f"/api/proctoring/interviews/{IID}/report").status_code == 401
