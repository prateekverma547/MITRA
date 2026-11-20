"""The HR round: booked by HR, joined by HR and the candidate, never by a bot.

Daily is stubbed; what is under test is the booking, the stage moves, the
join path's refusal to start a bot or claim a bot slot, and who sees what.
"""

import json
from pathlib import Path

import pytest

from tests.console_support import org_client, sign_in  # noqa: F401

QUESTIONS = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "hr_questions.json").read_text()
)


class Room:
    name = "hr-room-1"
    url = "https://example.daily.co/hr-room-1"


@pytest.fixture(autouse=True)
def stub_daily(monkeypatch):
    from bot.services import daily

    tokens: list[dict] = []

    async def create_room(**kwargs):
        return Room()

    async def create_meeting_token(**kwargs):
        tokens.append(kwargs)
        return f"token-{len(tokens)}"

    monkeypatch.setattr(daily, "create_room", create_room)
    monkeypatch.setattr(daily, "create_meeting_token", create_meeting_token)
    # The candidate's join path imports these names directly.
    import app.interviews as interviews

    monkeypatch.setattr(interviews, "create_meeting_token", create_meeting_token)

    async def no_bot(**kwargs):
        raise AssertionError("an HR round must never start a bot")

    monkeypatch.setattr(interviews, "_start_bot", no_bot)
    monkeypatch.setattr(interviews.registry, "claim", lambda: (_ for _ in ()).throw(AssertionError("no bot slot for an HR round")))
    return tokens


async def ready_candidate(client, stage: str = "ready_for_hr") -> str:
    from app import db
    from app.db import Candidate
    from hiring.models import Screening

    cid = client.demo.candidate_ids["ai"]
    async with db.get_sessionmaker()() as session:
        candidate = await session.get(Candidate, cid)
        candidate.stage = stage
        session.add(Screening(candidate_id=cid, status="ready", hr_questions=QUESTIONS))
        await session.commit()
    return cid


async def test_hr_books_a_round_and_gets_credentials_to_send(org_client):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    assert org_client.get(f"/api/candidates/{cid}/hr-round").json()["can_book"] is True

    booked = org_client.post(f"/api/candidates/{cid}/hr-round")
    assert booked.status_code == 201, booked.text
    view = booked.json()
    assert view["candidate"]["stage"] == "hr_booked"
    r = view["round"]
    assert r["status"] == "scheduled" and r["meeting_id"] and r["password"]
    assert r["join_url"].endswith(f"/meet?id={r['meeting_id']}") and r["can_cancel"] and not r["can_end"]
    assert len(view["questions"]) == 10

    assert org_client.post(f"/api/candidates/{cid}/hr-round").status_code == 409


async def test_only_a_candidate_ready_for_the_hr_round_can_be_booked(org_client):
    cid = await ready_candidate(org_client, stage="screened")
    sign_in(org_client, "hr@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/hr-round").status_code == 409


async def test_the_candidate_joins_and_no_bot_is_started(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    r = org_client.post(f"/api/candidates/{cid}/hr-round").json()["round"]

    org_client.cookies.clear()
    joined = org_client.post(
        "/interviews/join", json={"meeting_id": r["meeting_id"], "password": r["password"], "consent": True}
    )
    assert joined.status_code == 200, joined.text
    body = joined.json()
    assert body["round"] == "hr"
    assert body["role_title"] == "AI Engineer"  # from the job: no blueprint yet
    assert stub_daily[-1]["is_owner"] is False

    sign_in(org_client, "hr@mitra.local")
    assert org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]["status"] == "in_progress"


async def test_hr_joins_as_the_rooms_owner_and_notes_are_saved(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")

    joined = org_client.post(f"/api/candidates/{cid}/hr-round/join").json()
    assert joined["room_url"] == Room.url and joined["token"]
    assert stub_daily[-1]["is_owner"] is True and stub_daily[-1]["user_name"] == "HR Partner"

    saved = org_client.post(
        f"/api/candidates/{cid}/hr-round/progress",
        json={"asked": ["hr2", "hr1", "hr2"], "notes": "Clear on client work. Notice period 60 days."},
    )
    assert saved.status_code == 200
    r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert r["asked"] == ["hr2", "hr1"] and r["notes"].startswith("Clear on client work")
    assert r["status"] == "in_progress" and r["can_end"]

    ended = org_client.post(f"/api/candidates/{cid}/hr-round/end").json()["round"]
    assert ended["status"] == "completed" and ended["ended_at"] and not ended["can_join"]


async def test_a_round_nobody_joined_can_be_cancelled(org_client):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")

    cancelled = org_client.post(f"/api/candidates/{cid}/hr-round/cancel", json={"reason": "Candidate asked to move it."})
    assert cancelled.status_code == 200
    assert cancelled.json()["candidate"]["stage"] == "ready_for_hr"
    history = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"]
    assert history[-1]["reason"] == "Candidate asked to move it."

    rebooked = org_client.post(f"/api/candidates/{cid}/hr-round")
    assert rebooked.status_code == 201
    # No reason is needed to cancel an invitation.
    again = org_client.post(f"/api/candidates/{cid}/hr-round/cancel", json={})
    assert again.status_code == 200
    assert org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]["reason"] == "Cancelled by HR"


async def test_a_started_round_cannot_be_cancelled(org_client):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")
    org_client.post(f"/api/candidates/{cid}/hr-round/join")
    assert org_client.post(f"/api/candidates/{cid}/hr-round/cancel", json={"reason": "x"}).status_code == 409


async def test_only_hr_sees_the_password_and_runs_the_round(org_client):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")

    for email in ("ai.head@mitra.local", "admin@mitra.local"):
        sign_in(org_client, email)
        r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
        assert r["password"] is None and r["meeting_id"] is None and not r["can_join"]
        assert org_client.post(f"/api/candidates/{cid}/hr-round/join").status_code == 403

    sign_in(org_client, "sales.head@mitra.local")
    assert org_client.get(f"/api/candidates/{cid}/hr-round").status_code == 404


async def test_the_live_transcript_is_kept_and_the_evaluation_starts_from_it(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")
    assert org_client.post(f"/api/candidates/{cid}/hr-round/join").json()["transcribe"] is True

    lines = [
        {"id": "a1", "speaker": "hr", "text": "What led you to move to Noida?", "start": 3.0, "end": 5.2},
        {"id": "a2", "speaker": "candidate", "text": "My team moved there last year.", "start": 6.0, "end": 8.4},
    ]
    assert org_client.post(f"/api/candidates/{cid}/hr-round/transcript", json={"lines": lines}).json()["stored"] == 2
    # A resend is not stored twice; an empty line is ignored.
    again = lines + [{"id": "a3", "speaker": "candidate", "text": "  ", "start": 9, "end": 9}]
    assert org_client.post(f"/api/candidates/{cid}/hr-round/transcript", json={"lines": again}).json()["stored"] == 2

    r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert [t["speaker"] for t in r["transcript"]] == ["hr", "candidate"] and r["attribution"]["method"] == "live"

    ended = org_client.post(f"/api/candidates/{cid}/hr-round/end").json()["round"]
    # No waiting for the recording: the live transcript goes straight to evaluation.
    assert ended["eval_status"] == "evaluating"


async def test_without_a_live_transcript_the_recording_is_used(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")
    org_client.post(f"/api/candidates/{cid}/hr-round/join")
    assert org_client.post(f"/api/candidates/{cid}/hr-round/end").json()["round"]["eval_status"] == "waiting_recording"


# ---- skipping the HR round (product owner, 2026-11-20) ----------------------------------------


async def test_hr_can_skip_the_hr_round_and_the_candidate_goes_to_the_mitra_round(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    r = org_client.post(f"/api/candidates/{cid}/hr-round/skip", json={})
    assert r.status_code == 200 and r.json()["stage"] == "ready_for_technical"
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["from"] == "ready_for_hr" and last["to"] == "ready_for_technical" and last["reason"] == "HR round skipped"
    results = org_client.get(f"/api/candidates/{cid}/results").json()
    assert results["hr_skipped"]["reason"] is None and results["hr_round"] is None


async def test_a_booked_but_unjoined_round_can_be_skipped_with_a_reason(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")
    r = org_client.post(f"/api/candidates/{cid}/hr-round/skip", json={"reason": "Referred by the BU head"})
    assert r.status_code == 200
    assert org_client.get(f"/api/candidates/{cid}/results").json()["hr_skipped"]["reason"] == "Referred by the BU head"


async def test_a_round_in_progress_cannot_be_skipped_and_only_hr_skips(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/hr-round/skip", json={}).status_code == 403
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")
    org_client.post(f"/api/candidates/{cid}/hr-round/join")
    r = org_client.post(f"/api/candidates/{cid}/hr-round/skip", json={})
    assert r.status_code == 409 and "started" in r.json()["detail"]


# ---- listen mode: HR's phone call, MITRA listens (product owner, 2026-11-20) -------------------


async def test_a_phone_call_round_needs_the_candidates_consent_ticked_before_mitra_listens(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    booked = org_client.post(f"/api/candidates/{cid}/hr-round", json={"mode": "listen"}).json()["round"]
    # The candidate never joins MITRA's room, so there are no credentials to send.
    assert booked["mode"] == "listen" and booked["meeting_id"] is None and booked["password"] is None
    refused = org_client.post(f"/api/candidates/{cid}/hr-round/join", json={})
    assert refused.status_code == 422 and "agreed" in refused.json()["detail"]
    joined = org_client.post(f"/api/candidates/{cid}/hr-round/join", json={"phone_consent": True}).json()
    assert joined["mode"] == "listen" and joined["transcribe"] is True
    r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert r["phone_consent_at"] and r["status"] == "in_progress"


async def test_a_phone_calls_live_lines_are_labelled_by_voice_not_by_device(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round", json={"mode": "listen"})
    org_client.post(f"/api/candidates/{cid}/hr-round/join", json={"phone_consent": True})
    lines = [{"id": "p1", "speaker": "hr", "text": "Walk me through your profile.", "start": 2, "end": 4},
             {"id": "p2", "speaker": "candidate", "text": "I build retrieval systems.", "start": 5, "end": 8}]
    org_client.post(f"/api/candidates/{cid}/hr-round/transcript", json={"lines": lines})
    r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert r["attribution"]["method"] == "live_one_mic" and r["attribution"]["confidence"] == "likely"
    # The live lines still count for the evaluation's fallback.
    assert org_client.post(f"/api/candidates/{cid}/hr-round/end").json()["round"]["eval_status"] == "evaluating"


async def test_a_room_round_is_unchanged(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    booked = org_client.post(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert booked["mode"] == "room" and booked["meeting_id"] and booked["password"]
    assert org_client.post(f"/api/candidates/{cid}/hr-round/join").status_code == 200


async def test_hr_can_swap_a_phone_calls_speakers_when_the_voices_were_mixed_up(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round", json={"mode": "listen"})
    org_client.post(f"/api/candidates/{cid}/hr-round/join", json={"phone_consent": True})
    org_client.post(f"/api/candidates/{cid}/hr-round/transcript", json={"lines": [
        {"id": "p1", "speaker": "hr", "text": "Hello?", "start": 1, "end": 2},
        {"id": "p2", "speaker": "candidate", "text": "Hi, this is HR from the company.", "start": 3, "end": 5}]})
    assert org_client.post(f"/api/candidates/{cid}/hr-round/transcript/swap").json()["swapped"] == 2
    r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert [t["speaker"] for t in r["transcript"]] == ["candidate", "hr"]


async def test_a_room_rounds_speakers_cannot_be_swapped(org_client, stub_daily):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")
    org_client.post(f"/api/candidates/{cid}/hr-round/join")
    assert org_client.post(f"/api/candidates/{cid}/hr-round/transcript/swap").status_code == 409
