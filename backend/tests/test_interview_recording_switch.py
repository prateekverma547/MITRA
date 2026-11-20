"""The recording switch is per interview: chosen at scheduling, carried to the bot, and stated
to the candidate from the same value before they agree."""

import pytest

from tests.test_interviews import client, make_candidate  # noqa: F401  (fixture)


async def stored_switch(interview_id: str) -> bool:
    from app import db

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        return bool(interview.recording_enabled)


async def test_the_admin_chooses_recording_when_scheduling(client, monkeypatch):
    monkeypatch.delenv("RECORDING_ENABLED", raising=False)
    candidate_id = await make_candidate()

    created = client.post(
        f"/candidates/{candidate_id}/interviews", json={"recording_enabled": True}
    ).json()

    assert await stored_switch(created["interview_id"]) is True


async def test_an_interview_scheduled_without_recording_stays_unrecorded(client, monkeypatch):
    monkeypatch.setenv("RECORDING_ENABLED", "1")
    candidate_id = await make_candidate()

    created = client.post(
        f"/candidates/{candidate_id}/interviews", json={"recording_enabled": False}
    ).json()

    assert await stored_switch(created["interview_id"]) is False


async def test_omitting_the_choice_uses_the_environment_default(client, monkeypatch):
    candidate_id = await make_candidate()

    monkeypatch.setenv("RECORDING_ENABLED", "1")
    on = client.post(f"/candidates/{candidate_id}/interviews").json()
    assert await stored_switch(on["interview_id"]) is True

    # One live session per candidate, so cancel the first before booking the second.
    assert client.delete(f"/interviews/{on['interview_id']}").status_code == 200

    monkeypatch.delenv("RECORDING_ENABLED", raising=False)
    off = client.post(f"/candidates/{candidate_id}/interviews").json()
    assert await stored_switch(off["interview_id"]) is False


async def test_the_bot_is_started_with_the_interviews_own_switch(client, monkeypatch):
    monkeypatch.delenv("RECORDING_ENABLED", raising=False)
    candidate_id = await make_candidate()
    created = client.post(
        f"/candidates/{candidate_id}/interviews", json={"recording_enabled": True}
    ).json()

    client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert client.spawned[0]["recording"] is True


async def test_the_notice_reports_the_interviews_own_switch(client):
    candidate_id = await make_candidate()
    created = client.post(
        f"/candidates/{candidate_id}/interviews", json={"recording_enabled": True}
    ).json()

    response = client.post(
        "/interviews/notice",
        json={"meeting_id": created["meeting_id"], "password": created["password"]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"recording_enabled": True}


async def test_the_notice_refuses_a_wrong_password_like_join_does(client):
    candidate_id = await make_candidate()
    created = client.post(
        f"/candidates/{candidate_id}/interviews", json={"recording_enabled": True}
    ).json()

    wrong = client.post(
        "/interviews/notice",
        json={"meeting_id": created["meeting_id"], "password": "not-the-password"},
    )
    unknown = client.post(
        "/interviews/notice", json={"meeting_id": "999-999-999", "password": "x"}
    )

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json(), "the answer must not reveal which meetings exist"
    assert "recording_enabled" not in wrong.text
