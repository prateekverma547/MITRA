"""Who is in the room, and the states the screens show because of it."""

from datetime import UTC, datetime, timedelta

from hiring.presence import Presence, classify
from hiring.technical import live_state
from tests.console_support import org_client, sign_in  # noqa: F401
from tests.hiring.test_technical import at_stage, set_interview
from tests.hiring.test_technical import stubs as _tech_stubs  # noqa: F401

MITRA = {"userName": "Interviewer", "userId": "u1"}
CANDIDATE = {"userName": "Priya Nair", "userId": "u2"}
MARKED = {"userName": "Someone", "userId": "candidate:int_x"}
WATCHER = {"userName": "Neha Kapoor", "userId": "u3"}


def test_classify_tells_mitra_the_candidate_and_everyone_else_apart():
    seen = classify([MITRA, CANDIDATE, WATCHER], "Priya Nair")
    assert seen.known and seen.mitra and seen.candidate and seen.others == 1
    assert classify([MARKED], "Priya Nair").candidate
    assert classify(None, "Priya Nair") == Presence(known=False)


def test_a_hidden_watcher_is_never_counted():
    """Daily's presence API lists hidden participants too (Track I, D-04)."""
    hidden = {"userName": "Live view", "userId": "watcher:usr_hr"}
    seen = classify([MITRA, hidden], "Priya Nair")
    assert seen.mitra and not seen.candidate and seen.others == 0


class Row:
    def __init__(self, status, started_minutes_ago=5.0, feedback="pending", consent=None):
        self.status = status
        self.started_at = datetime.now(UTC) - timedelta(minutes=started_minutes_ago)
        self.feedback_status = feedback
        self.consent_accepted_at = consent


def test_a_running_interview_reads_what_is_actually_happening():
    running = Row("in_progress")
    assert live_state(running, None) == "live"
    assert live_state(running, Presence(known=False)) == "live"
    assert live_state(running, Presence(known=True, candidate=True, mitra=True)) == "live"
    assert live_state(running, Presence(known=True, mitra=True)) == "candidate_left"
    assert live_state(running, Presence(known=True)) == "closing"
    # Seconds after joining, an empty room is a start, not an ending.
    assert live_state(Row("in_progress", started_minutes_ago=0.2), Presence(known=True)) == "starting"
    assert live_state(Row("completed", feedback="generating"), None) == "scoring"
    assert live_state(Row("completed", feedback="ready"), None) == "done"


async def test_the_candidate_page_says_the_candidate_left(org_client, no_daily_presence):
    from app import db
    from app.db import Candidate

    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="ready")
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/technical")
    async with db.get_sessionmaker()() as session:
        name = (await session.get(Candidate, cid)).name
    await set_interview(cid, status="in_progress", started_at=datetime.now(UTC) - timedelta(minutes=6))

    no_daily_presence.people = [MITRA, {"userName": name, "userId": "x"}]
    assert org_client.get(f"/api/candidates/{cid}/technical").json()["round"]["state"] == "live"

    no_daily_presence.people = [MITRA]
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["round"]["state"] == "candidate_left"
    assert org_client.get(f"/api/candidates/{cid}/technical/live").json()["state"] == "candidate_left"

    no_daily_presence.people = []
    assert org_client.get(f"/api/candidates/{cid}/technical").json()["round"]["state"] == "closing"

    await set_interview(cid, status="completed", feedback_status="generating", ended_at=datetime.now(UTC))
    r = org_client.get(f"/api/candidates/{cid}/technical").json()["round"]
    assert r["state"] == "scoring" and r["scoring_step"] == "compiling"
