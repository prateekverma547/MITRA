"""replay_events.py and fake_bot.py write through the real emitter into the real store."""

import importlib.util
import json
from pathlib import Path

import pytest

from live.store import all_events
from tests.live.support import fresh_db, seed_interview

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FIXTURE = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "interview_events.json"
IID = "int_script000001"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def db(tmp_path, monkeypatch):
    database = await fresh_db(tmp_path, monkeypatch, "scripts.db")
    await seed_interview(IID)
    yield database
    await database.reset_engine()


async def test_replay_stores_every_fixture_event_in_order_and_twice_is_new_events(db):
    replay = _load("replay_events")
    fixture = json.loads(FIXTURE.read_text())
    stats = await replay.replay(IID, speed=1e6, fixture=FIXTURE, run_id="r1")
    assert stats["written"] == len(fixture) and stats["dropped"] == 0
    assert "skip" in {e["kind"] for e in fixture}  # every event kind is replayed, skip included
    stored = await all_events(IID)
    expected = [e["event_id"] for e in sorted(fixture, key=lambda e: e["at_seconds"])]
    assert [e.event_id.rsplit(":", 1)[1] for e in stored] == expected
    assert {e.interview_id for e in stored} == {IID}
    await replay.replay(IID, speed=1e6, fixture=FIXTURE, run_id="r2")
    assert len(await all_events(IID)) == 2 * len(fixture)


async def test_replay_keeps_the_fixture_timing(db):
    replay = _load("replay_events")
    # 1800 s of fixture at 3600x: about half a second, not instantly.
    stats = await replay.replay(IID, speed=3600, fixture=FIXTURE, run_id="t")
    assert 0.45 <= stats["played_seconds"] <= 1.5


async def test_fake_bot_emits_a_valid_complete_stream(db):
    fake = _load("fake_bot")
    stats = await fake.run(IID, turns=6, interval=0.001, seed=1)
    assert stats["dropped"] == 0 and stats["rejected"] == 0
    kinds = [e.kind.value for e in await all_events(IID)]
    assert kinds[0] == "participant" and kinds[-1] == "interview_ended"
    assert kinds.count("transcript_turn") == 12 and kinds.count("reply_wait") == 5
    assert kinds.count("skip") == 1
