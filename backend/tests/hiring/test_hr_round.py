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
    assert r["join_url"].endswith("/join") and r["can_cancel"] and not r["can_end"]
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


async def test_a_round_nobody_joined_can_be_cancelled_with_a_reason(org_client):
    cid = await ready_candidate(org_client)
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/hr-round")
    assert org_client.post(f"/api/candidates/{cid}/hr-round/cancel", json={"reason": " "}).status_code == 422

    cancelled = org_client.post(f"/api/candidates/{cid}/hr-round/cancel", json={"reason": "Candidate asked to move it."})
    assert cancelled.status_code == 200
    assert cancelled.json()["candidate"]["stage"] == "ready_for_hr"
    history = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"]
    assert history[-1]["reason"] == "Candidate asked to move it."

    rebooked = org_client.post(f"/api/candidates/{cid}/hr-round")
    assert rebooked.status_code == 201


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

    sign_in(org_client, "finance.head@mitra.local")
    assert org_client.get(f"/api/candidates/{cid}/hr-round").status_code == 404
