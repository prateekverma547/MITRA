"""Start-up clean-up (wave 1): a restart ends interviews whose bot is gone, with
a plain reason and an ending event, and never touches one whose bot is alive.

Real processes stand in for bots: one whose command line is
`bot.run_bot --interview-id <id>` (alive), one that has exited (dead), and one
alive but running something else (a reused process id). The "restart" is the
app starting with these rows already in the database.
"""

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from tests.live.support import fresh_db, seed_interview

ALIVE, DEAD, NO_PID, REUSED = "int_restart_live", "int_restart_dead", "int_restart_nopid", "int_restart_reuse"


def stand_in_bot(interview_id: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "bot.run_bot",
                             "--interview-id", interview_id])


@pytest.fixture
async def restarted(tmp_path, monkeypatch):
    """The database as the dead web process left it, then the app starting."""
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    db = await fresh_db(tmp_path, monkeypatch, "restart.db")
    from app import capacity

    monkeypatch.setattr(capacity.registry, "_bots", {})
    alive = stand_in_bot(ALIVE)
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # not a bot
    try:
        await seed_interview(ALIVE, candidate_id="c1", status="in_progress", started_ago=300, bot_pid=alive.pid,
                             meeting_id="111-111-111", password="pw-alive")
        await seed_interview(DEAD, candidate_id="c2", status="in_progress", started_ago=300, bot_pid=exited.pid,
                             meeting_id="222-222-222", password="pw-dead")
        await seed_interview(NO_PID, candidate_id="c3", status="in_progress", started_ago=300)
        await seed_interview(REUSED, candidate_id="c4", status="in_progress", started_ago=300, bot_pid=other.pid)
        await db.reset_engine()
        from app import main

        with TestClient(main.app) as client:  # the lifespan runs the clean-up
            client.alive_pid = alive.pid
            yield client
    finally:
        for proc in (alive, other):
            proc.kill()
            proc.wait()
        await db.reset_engine()


def row(client, iid):
    from app import db

    async def read():
        async with db.get_sessionmaker()() as s:
            return await s.get(db.Interview, iid)

    return client.portal.call(read)


def test_a_live_bots_interview_is_never_touched_and_its_bot_is_adopted(restarted):
    from app.capacity import registry

    alive = row(restarted, ALIVE)
    assert alive.status == "in_progress" and alive.failure_reason is None and alive.ended_at is None
    assert registry.is_running(ALIVE) and registry.live_count() == 1


@pytest.mark.parametrize("iid", [DEAD, NO_PID, REUSED])
def test_an_interview_whose_bot_is_gone_is_ended_as_interrupted(restarted, iid):
    from live.store import all_events

    ended = row(restarted, iid)
    assert ended.status == "failed" and ended.ended_at is not None
    assert ended.failure_reason.startswith("Interrupted by a server restart")
    assert ended.session_metrics["ended_because"] == "interrupted_by_restart"
    [event] = [e for e in restarted.portal.call(lambda: all_events(iid)) if e.kind.value == "interview_ended"]
    assert event.source.value == "server" and event.payload.ended_because.value == "interrupted_by_restart"
    assert event.at_seconds > 250  # the interview clock at the restart, not 0


def test_a_reload_and_a_rejoin_are_refused_with_that_reason(restarted):
    from app import db
    from app.interviews import INTERRUPTED_DETAIL
    from live import rejoin

    for path in ("/interviews/check", "/interviews/join"):
        body = {"meeting_id": "222-222-222", "password": "pw-dead", **({"consent": True} if "join" in path else {})}
        r = restarted.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"] == INTERRUPTED_DETAIL, path

    async def pass_for_dead():
        async with db.get_sessionmaker()() as s:
            return await rejoin.issue_pass(await s.get(db.Interview, DEAD))

    issued = restarted.portal.call(pass_for_dead)
    r = restarted.post("/api/live/rejoin", json={"interview_id": DEAD, "rejoin_pass": issued.value})
    assert r.status_code == 410 and r.json()["reason"] == "interrupted_by_restart"
    # The live one still answers as running.
    live = restarted.post("/interviews/check", json={"meeting_id": "111-111-111", "password": "pw-alive"})
    assert live.status_code == 200 and live.json()["state"] == "in_progress"
