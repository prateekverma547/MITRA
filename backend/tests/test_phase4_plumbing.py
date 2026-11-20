"""Phase 4 plumbing (Session C): the summary routes mounted in the app (C-157), the
summary shapes in the shared contracts (C-159), and the start-up line saying which
interview engine runs (C-160)."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from tests.live.support import ADMIN_PASSWORD, seed_interview

BACKEND = Path(__file__).resolve().parents[1]
IID = "int_summarymnt01"
PASSWORD = "canary-pw-7f3e"
SUMMARY = ("/api/analytics/summary/jobs/job_live", f"/api/analytics/summary/interviews/{IID}",
           "/api/analytics/summary/operations")


# -- the summary routes, mounted -----------------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/summary.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        c.portal.call(lambda: seed_interview(IID, status="completed", ended_ago=60, password=PASSWORD,
                                             transcript=[{"speaker": "candidate", "text": "a canary answer turn"}],
                                             session_metrics={"ended_because": "interview_complete"}))
        yield c
    await db.reset_engine()


def test_the_summary_routes_are_mounted_and_refuse_a_signed_out_caller(client):
    for path in SUMMARY:
        assert client.get(path).status_code == 401, path


def test_signed_in_the_job_and_interview_summaries_answer_with_nothing_secret(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    job = client.get(SUMMARY[0])
    one = client.get(SUMMARY[1])
    assert job.status_code == one.status_code == client.get(SUMMARY[2]).status_code == 200
    assert job.json()["interviews"] == 1 and one.json()["interview_id"] == IID
    for r in (job, one):
        for secret in (PASSWORD, "a canary answer turn", f"room-{IID}", "example.daily.co"):
            assert secret not in r.text, secret


# -- the summary shapes in the shared contracts ---------------------------------------------------


def test_the_summary_shapes_are_re_exported_unchanged_from_the_shared_contracts():
    import shared.interview_contracts as contracts
    from analytics.summary import shapes

    for name in ("InterviewSummary", "JobSummary", "OperationsSummary"):
        assert getattr(contracts, name) is getattr(shapes, name), name


# -- which engine interviews will use -------------------------------------------------------------


def _announce(monkeypatch, value):
    from app import startup

    if value is None:
        monkeypatch.delenv("INTERVIEW_ENGINE", raising=False)
    else:
        monkeypatch.setenv("INTERVIEW_ENGINE", value)
    lines: list[tuple[str, str]] = []
    sink = logger.add(lambda m: lines.append((m.record["level"].name, m.record["message"])), level="INFO")
    try:
        engine = startup.announce_engine()
    finally:
        logger.remove(sink)
    return engine, lines


@pytest.mark.parametrize("value", [None, "", "realtime", " Realtime ", "pipeline", " PIPELINE ", "pipelin"])
def test_the_engine_line_reads_the_setting_exactly_as_the_bot_does(monkeypatch, value):
    from bot.config import interview_engine

    engine, lines = _announce(monkeypatch, value)
    assert engine == interview_engine()  # what bot/run_bot.py runs
    assert any(engine in message for _, message in lines)


def test_the_bot_reads_its_engine_through_the_same_function():
    """The line above is only true while run_bot.py decides with interview_engine()."""
    source = (BACKEND / "bot" / "run_bot.py").read_text()
    assert "engine = interview_engine()" in source and "if engine == REALTIME_ENGINE:" in source


def test_realtime_is_one_plain_line(monkeypatch):
    engine, lines = _announce(monkeypatch, None)
    assert engine == "realtime" and lines == [("INFO", "Interview engine: realtime (OpenAI Realtime)")]


def test_pipeline_is_a_loud_warning(monkeypatch):
    engine, lines = _announce(monkeypatch, "pipeline")
    [(level, message)] = lines
    assert engine == "pipeline" and level == "WARNING"
    assert "OLD scripted engine" in message and "NOT OpenAI Realtime" in message


def test_the_line_prints_no_other_setting_and_not_the_raw_value(monkeypatch):
    canaries = {"OPENAI_API_KEY": "sk-canary-openai", "DAILY_API_KEY": "canary-daily",
                "ADMIN_PASSWORD": "canary-admin", "DATABASE_URL": "sqlite+aiosqlite:///canary.db"}
    for name, value in canaries.items():
        monkeypatch.setenv(name, value)
    for setting in (None, "pipeline", "pipelin3-typo"):
        _, lines = _announce(monkeypatch, setting)
        text = " ".join(message for _, message in lines)
        assert not any(v in text for v in canaries.values())
        assert "pipelin3-typo" not in text


async def test_the_app_says_it_at_start_up(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/start.db")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setenv("INTERVIEW_ENGINE", "pipeline")
    from app import db, main

    await db.reset_engine()
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="WARNING")
    try:
        with TestClient(main.app):
            pass
    finally:
        logger.remove(sink)
    await db.reset_engine()
    assert sum("INTERVIEW ENGINE: pipeline" in line for line in lines) == 1
    assert os.environ["INTERVIEW_ENGINE"] == "pipeline"
