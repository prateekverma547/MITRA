"""B-71: GET /interviews/{id} returns the cleaned transcript beside the raw one,
and two contract descriptions say what the code does.

Requirements: the cleaned transcript is always in the answer; when none is
stored it is empty with a clear reason (not cleaned yet, or failed), never an
error; same sign-in as before; nothing new carries the meeting password;
interviews from before the column still answer.
"""

import json

import pytest
from fastapi.testclient import TestClient

from shared.interview_contracts import CleanedTranscript, RecordingInfo, Skip
from tests.live.support import ADMIN_PASSWORD, seed_interview

PASSWORD = "kmnp3478"

CLEANED = CleanedTranscript(status="ok", model_job="feedback", candidate_turns=1, turns=[
    {"index": 0, "speaker": "candidate", "text": "someone off camera", "guard": "copied", "pre_interview": True},
    {"index": 1, "speaker": "interviewer", "text": "Hello, I'm Mitra.", "guard": "copied"},
    {"index": 2, "speaker": "candidate", "text": "I led the Kafka migration.", "guard": "ok"},
]).model_dump(mode="json")


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/view.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        seed = lambda iid, cand, **kw: c.portal.call(lambda: seed_interview(  # noqa: E731
            iid, candidate_id=cand, status="completed", ended_ago=60, password=PASSWORD, **kw))
        seed("int_view_ok", "c1", cleaned_transcript=CLEANED)
        seed("int_view_none", "c2")
        seed("int_view_failed", "c3", cleaned_transcript={"status": "missing", "reason": "model unavailable"})
        seed("int_view_standin", "c4", session_metrics={"cleaned_transcript": CLEANED})
        seed("int_view_junk", "c5", cleaned_transcript={"status": "maybe", "turns": "x"})
        c.post("/admin/login", json={"password": ADMIN_PASSWORD})
        yield c
    await db.reset_engine()


def view(client, iid) -> dict:
    r = client.get(f"/interviews/{iid}")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_cleaned_transcript_is_returned_beside_the_raw_one(client):
    body = view(client, "int_view_ok")
    cleaned = body["cleaned_transcript"]
    assert cleaned["status"] == "ok" and cleaned["reason"] is None
    # Pre-interview audio is left out, as it is from the raw transcript in this view.
    assert [t["index"] for t in cleaned["turns"]] == [1, 2]
    assert cleaned["turns"][1]["text"] == "I led the Kafka migration."
    assert CleanedTranscript.model_validate(cleaned).status == "ok"


@pytest.mark.parametrize("iid,reason", [
    ("int_view_none", "Not cleaned yet."),
    ("int_view_failed", "Cleaning failed: model unavailable"),
    ("int_view_junk", "The stored cleaned transcript could not be read."),
])
def test_no_cleaned_transcript_is_empty_with_a_reason_never_an_error(client, iid, reason):
    cleaned = view(client, iid)["cleaned_transcript"]
    assert cleaned["status"] == "missing" and cleaned["turns"] == [] and cleaned["reason"] == reason


def test_the_cleaners_stand_in_is_read_until_session_a_switches(client):
    cleaned = view(client, "int_view_standin")["cleaned_transcript"]
    assert cleaned["status"] == "ok" and [t["index"] for t in cleaned["turns"]] == [1, 2]


def test_same_sign_in_as_before(client):
    client.cookies.clear()
    assert client.get("/interviews/int_view_ok").status_code == 401


def test_the_new_field_carries_no_password_and_nothing_else_is_added(client):
    body = view(client, "int_view_ok")
    assert PASSWORD not in json.dumps(body["cleaned_transcript"])
    from app.interviews import InterviewView

    # The only new key is cleaned_transcript. The existing `password` field
    # (F-059, on hold) is unchanged and not this task's (C-101).
    assert set(body) == set(InterviewView.model_fields)
    assert "cleaned_transcript" in body and "token" not in json.dumps(body["cleaned_transcript"]).lower()


async def test_an_interview_from_before_the_column_still_answers(tmp_path, monkeypatch):
    from sqlalchemy import text

    from tests.live.support import fresh_db

    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    db = await fresh_db(tmp_path, monkeypatch, "old.db")
    await seed_interview("int_view_old", status="completed", ended_ago=60)
    async with db.get_engine().begin() as conn:  # the database as it was before C-A6
        await conn.execute(text("ALTER TABLE interviews DROP COLUMN cleaned_transcript"))
    await db.reset_engine()
    from app import main

    with TestClient(main.app) as c:  # start-up adds the column back
        c.post("/admin/login", json={"password": ADMIN_PASSWORD})
        r = c.get("/interviews/int_view_old")
    assert r.status_code == 200
    assert r.json()["cleaned_transcript"] == {**CleanedTranscript(status="missing", reason="Not cleaned yet.")
                                              .model_dump(mode="json")}
    await db.reset_engine()


# -- the two descriptions (B-71 items 2 and 3) ------------------------------------------------


def test_recording_info_lists_exactly_the_statuses_the_database_uses():
    from app.db import RecordingStatus

    described = RecordingInfo.model_fields["status"].description
    first_sentence = described.split(". ", 1)[0]
    listed = first_sentence.split(":", 1)[1].strip().rstrip(".").split(", ")
    assert listed == [s.value for s in RecordingStatus]
    assert "failed" not in listed
    # The console-only state (C-103) is named separately, not as a database status.
    assert "download_failing" in described.split(". ", 1)[1]


def test_the_skip_contract_says_the_candidate_asked_to_skip():
    doc = Skip.__doc__
    assert "candidate asked to skip" in doc and "moved on to the next plan question" in doc
    assert "Mitra moved past" not in doc
    # Descriptions reach the JSON schema the frontend types are generated from.
    assert "candidate asked to skip" in json.dumps(Skip.model_json_schema())
    assert "not_recorded, recording, stored" in json.dumps(RecordingInfo.model_json_schema())
