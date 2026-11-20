"""Shared fixtures for the hiring tests."""

import pytest


@pytest.fixture(autouse=True)
def no_daily_presence(monkeypatch):
    """Never ask Daily who is in a room from a test.

    `.env` may hold a real key, and a test must not reach the network. By
    default the answer is "could not ask" (the screens fall back to the stored
    status); a test that needs a room's people sets `room.people`.
    """
    from hiring import presence

    class Room:
        people: list[dict] | None = None

    room = Room()

    async def fake_fetch(room_name: str):
        return room.people

    presence._cache.clear()
    monkeypatch.setattr(presence, "_fetch", fake_fetch)
    monkeypatch.setattr(presence, "CACHE_SECONDS", 0.0)
    return room


@pytest.fixture(autouse=True)
def no_deepgram(monkeypatch):
    """`.env` may hold a Deepgram key, which changes where the HR round's
    transcript comes from. Off by default; a test that wants it sets it."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
