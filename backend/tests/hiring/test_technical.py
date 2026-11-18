"""Booking the technical round (Track I's stub) and sending the result to the BU head."""

from hiring import technical
from tests.console_support import org_client, sign_in  # noqa: F401


async def at_stage(client, label: str, stage: str) -> str:
    from app import db
    from app.db import Candidate

    cid = client.demo.candidate_ids[label]
    async with db.get_sessionmaker()() as session:
        (await session.get(Candidate, cid)).stage = stage
        await session.commit()
    return cid


async def test_hr_books_the_technical_round_and_gets_two_placeholder_links(org_client):
    cid = await at_stage(org_client, "ai", "ready_for_technical")
    sign_in(org_client, "hr@mitra.local")
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["can_book"] and view["duration"]["default"] == technical.DEFAULT_DURATION

    booked = org_client.post(f"/api/candidates/{cid}/technical", json={"duration_minutes": 50})
    assert booked.status_code == 201
    body = booked.json()
    assert body["candidate"]["stage"] == "technical_booked"
    assert body["booking"]["stub"] is True and body["booking"]["duration_minutes"] == 50
    assert body["booking"]["candidate_link"] and body["booking"]["live_view_link"]
    assert body["can_cancel"] and not body["can_book"]
    # The links survive a reload.
    again = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert again["booking"]["live_view_link"] == body["booking"]["live_view_link"]


async def test_duration_is_bounded_and_only_hr_books(org_client):
    cid = await at_stage(org_client, "ai", "ready_for_technical")
    sign_in(org_client, "hr@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/technical", json={"duration_minutes": 5}).status_code == 422
    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/technical", json={}).status_code == 403


async def test_a_booking_is_cancelled_with_a_reason(org_client):
    cid = await at_stage(org_client, "ai", "ready_for_technical")
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/technical", json={})
    assert org_client.post(f"/api/candidates/{cid}/technical/cancel", json={"reason": " "}).status_code == 422
    done = org_client.post(f"/api/candidates/{cid}/technical/cancel", json={"reason": "Candidate asked to move it."})
    assert done.status_code == 200 and done.json()["candidate"]["stage"] == "ready_for_technical"
    assert done.json()["booking"] is None


async def test_hr_sends_a_finished_result_to_the_bu_head_with_a_note(org_client):
    cid = await at_stage(org_client, "ai", "technical_booked")
    sign_in(org_client, "hr@mitra.local")
    # Not done yet: only Track I's report finishing the round makes it technical_done.
    assert org_client.post(f"/api/candidates/{cid}/send-to-bu-head", json={}).status_code == 409

    await at_stage(org_client, "ai", "technical_done")
    sent = org_client.post(f"/api/candidates/{cid}/send-to-bu-head", json={"note": "Strong on evaluation."})
    assert sent.status_code == 200
    assert sent.json()["candidate"]["stage"] == "with_bu_head"
    assert sent.json()["sent_to_bu_head"]["note"] == "Strong on evaluation."
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["actor"] == "hr" and last["to"] == "with_bu_head"


async def test_only_hr_sends_to_the_bu_head(org_client):
    cid = await at_stage(org_client, "ai", "technical_done")
    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/send-to-bu-head", json={}).status_code == 403
