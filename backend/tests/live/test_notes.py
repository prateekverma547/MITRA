"""Watchers' notes (wave 1): stamped by the server, audited, on the live stream,
limited, and never reaching the bot."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from live import notes
from live.emit import make_event
from live.store import all_events, insert_events
from shared.interview_contracts import AreaChanged, EventSource, WatcherNotesSection
from tests.live.support import ADMIN_PASSWORD, seed_interview

IID = "int_notes0000001"
BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/notes.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    notes.limiter.reset()
    await db.reset_engine()
    with TestClient(main.app) as c:
        zero = datetime.now(UTC) - timedelta(seconds=100)
        c.portal.call(lambda: seed_interview(IID, status="in_progress", started_ago=105, transcript_zero_at=zero))
        c.portal.call(insert_events, [
            make_event(IID, AreaChanged(area_id="opening", area_name="Opening"), source=EventSource.COORDINATOR,
                       at_seconds=0.0),
            make_event(IID, AreaChanged(area_id="metrics", area_name="Metrics"), source=EventSource.COORDINATOR,
                       at_seconds=60.0),
            make_event(IID, AreaChanged(area_id="closing", area_name="Closing"), source=EventSource.COORDINATOR,
                       at_seconds=500.0)])  # in the future: must not be picked
        c.post("/admin/login", json={"password": ADMIN_PASSWORD})
        yield c
    notes.limiter.reset()
    await db.reset_engine()


def add(client, text="Strong answer on the rollback plan.", iid=IID):
    return client.post(f"/api/live/interviews/{iid}/notes", json={"text": text})


def test_a_note_is_stamped_by_the_server_with_time_and_area(client):
    r = add(client)
    assert r.status_code == 200, r.text
    note = r.json()
    assert 99.0 <= note["at_seconds"] <= 102.0  # the server clock (transcript zero 100 s ago)
    assert note["area_id"] == "metrics"         # the area current then, from the stored events
    assert note["author"] == "watcher:admin" and note["text"] == "Strong answer on the rollback plan."


def test_the_note_goes_on_the_live_stream_and_is_listed(client):
    note = add(client).json()
    events = client.portal.call(lambda: all_events(IID))
    [added] = [e for e in events if e.kind.value == "note_added"]
    assert added.source.value == "server" and added.payload.note_id == note["note_id"]
    assert added.at_seconds == note["at_seconds"] and added.payload.area_id == "metrics"
    section = WatcherNotesSection.model_validate(client.get(f"/api/live/interviews/{IID}/notes").json())
    assert [n.note_id for n in section.notes] == [note["note_id"]] and section.scored is False


def test_every_note_is_audited_without_its_text(client):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        note = add(client, "Mentions a private detail.").json()
    finally:
        logger.remove(sink)
    from sqlalchemy import select

    from app import db
    from live.models import WatcherNoteRecord

    async def row():
        async with db.get_sessionmaker()() as s:
            return await s.scalar(select(WatcherNoteRecord).where(WatcherNoteRecord.note_id == note["note_id"]))

    rec = client.portal.call(row)
    assert rec.author == "watcher:admin" and rec.created_at is not None and rec.at_estimate is False
    [audit] = [l for l in lines if note["note_id"] in l]
    assert "watcher:admin" in audit and IID in audit and "private detail" not in audit


@pytest.mark.parametrize("text,status", [("", 422), ("   ", 422), ("x" * 2001, 422)])
def test_empty_and_overlong_notes_are_refused(client, text, status):
    assert add(client, text).status_code == status


def test_notes_need_sign_in(client):
    client.cookies.clear()
    assert add(client).status_code == 401
    assert client.get(f"/api/live/interviews/{IID}/notes").status_code == 401


def test_notes_are_rate_limited(client):
    codes = [add(client, f"note {i}").status_code for i in range(notes.NOTES_PER_MINUTE + 1)]
    assert codes[:-1] == [200] * notes.NOTES_PER_MINUTE
    r = add(client, "one more")
    assert r.status_code == 429 and r.json()["reason"] == "rate_limited"


@pytest.mark.parametrize("status,ended_ago,code", [("scheduled", None, 409), ("completed", 30 * 60, 409),
                                                   ("completed", 5 * 60, 200), ("failed", 60, 409)])
def test_notes_are_taken_while_it_runs_and_shortly_after(client, status, ended_ago, code):
    iid = f"int_notes_{status[:4]}{ended_ago or 0}"
    client.portal.call(lambda: seed_interview(iid, candidate_id=f"c_{iid}", status=status,
                                             started_ago=None if status == "scheduled" else 2000,
                                             ended_ago=ended_ago))
    r = add(client, iid=iid)
    assert r.status_code == code, r.text
    if code == 409:
        assert r.json()["reason"] == "not_running"


def test_an_unknown_interview_is_404(client):
    assert add(client, iid="int_nope").status_code == 404


# -- nothing reaches the bot -------------------------------------------------------------------

BOT_CHANNELS = re.compile(r"(from bot|import bot|api\.daily\.co|DAILY_API_URL|app.message|send_app_message"
                          r"|sendAppMessage|create_watcher_token|create_meeting_token)")


def test_the_notes_code_has_no_way_into_the_call():
    for path in (BACKEND / "live" / "notes.py",):
        hits = [l for l in path.read_text().splitlines() if BOT_CHANNELS.search(l) and not l.strip().startswith("#")
                and "never" not in l and "nothing" not in l.lower()]
        assert hits == [], hits
    route = (BACKEND / "live" / "routes.py").read_text()
    body = route[route.index("async def add_note"):route.index("async def list_notes")]
    assert not BOT_CHANNELS.search(body), "the notes route must not talk to the call"


def test_the_bot_never_reads_notes():
    found = [f"{p}:{i}" for p in (BACKEND / "bot").rglob("*.py") for i, l in enumerate(p.read_text().splitlines(), 1)
             if re.search(r"note_added|NoteAdded|watcher_notes|WatcherNote|live\.notes", l)]
    assert found == []


def test_the_channel_scan_finds_a_planted_one():
    """Positive control for the two scans above."""
    assert BOT_CHANNELS.search("    from bot.services.daily import DAILY_API_URL")
    assert re.search(r"note_added|NoteAdded", "if e.kind == 'note_added':")
