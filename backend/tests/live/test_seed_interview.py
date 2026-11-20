"""scripts/seed_interview.py: a ready-to-join interview through the real code
paths, against a temporary SQLite database; refused for a non-local database."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_interview.py"


def _load():
    spec = importlib.util.spec_from_file_location("seed_interview", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("url,local", [
    ("sqlite+aiosqlite:///./interviewer.db", True),
    ("postgresql+asyncpg://me@localhost:5432/mitra", True),
    ("postgresql+asyncpg://me@127.0.0.1/mitra", True),
    ("postgresql+asyncpg://u:p@postgres.railway.internal:5432/railway", False),
    ("postgresql+asyncpg://u:p@roundhouse.proxy.rlwy.net:41234/railway", False),
    ("mysql://me@localhost/db", False),
])
def test_database_is_judged_by_its_url(url, local):
    assert _load().database_is_local(url) is local


async def test_refuses_a_database_that_is_not_local(monkeypatch):
    seed = _load()
    # A remote host that can never resolve (.invalid, RFC 2606): if the guard
    # ever broke, the test would fail fast instead of reaching a real server.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@remote-db.invalid:5432/railway")
    with pytest.raises(seed.NotLocal, match="not local"):
        await seed.seed(fixture="pm_senior", duration=None, engine="realtime", base_url="http://x")


async def test_refuses_on_a_deployed_instance_even_with_a_local_url(monkeypatch, tmp_path):
    seed = _load()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/s.db")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(seed.NotLocal, match="deployed"):
        await seed.seed(fixture="pm_senior", duration=None, engine="realtime", base_url="http://x")


async def test_seeds_a_ready_to_join_interview_through_the_booking_route(monkeypatch, tmp_path):
    seed = _load()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/seed.db")
    monkeypatch.setenv("INTERVIEW_ENGINE", "realtime")  # the script writes it; restored after
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000")
    from app import db, interviews
    from tests.test_interviews import FakeRoom

    rooms = []

    async def fake_room(**kw):
        rooms.append(kw)
        return FakeRoom()

    monkeypatch.setattr(interviews, "create_room", fake_room)
    await db.reset_engine()
    try:
        seeded = await seed.seed(fixture="pm_senior", duration=60, engine="realtime",
                                 base_url="http://localhost:8000")
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, seeded["interview_id"])
            cand = await s.get(db.Candidate, seeded["candidate_id"])
    finally:
        await db.reset_engine()
    assert len(rooms) == 1  # one room, through the real booking route
    assert row.status == "scheduled" and row.meeting_id == seeded["meeting_id"]
    assert (row.booked_duration_minutes, row.planned_duration_minutes, row.planned_grace_minutes) == (60, 56, 2)
    assert cand.blueprint_status == "ready" and cand.blueprint["blueprint_id"] == seeded["candidate_id"]
    text = seed.report(seeded, recording=True)
    for needed in (seeded["links"]["candidate_join_url"], seeded["meeting_id"], seeded["password"],
                   "INTERVIEW_ENGINE=realtime RECORDING_ENABLED=1", "Planned for 56 minutes"):
        assert needed in text


async def test_a_duration_over_60_is_refused_by_the_booking_rule(monkeypatch, tmp_path):
    seed = _load()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/seed2.db")
    # The script writes INTERVIEW_ENGINE straight into os.environ; setting it
    # through monkeypatch first means it is put back after the test.
    monkeypatch.setenv("INTERVIEW_ENGINE", "realtime")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    from app import db

    await db.reset_engine()
    try:
        with pytest.raises(RuntimeError, match="20 to 60"):
            await seed.seed(fixture="pm_senior", duration=75, engine="pipeline", base_url="http://x")
    finally:
        await db.reset_engine()
