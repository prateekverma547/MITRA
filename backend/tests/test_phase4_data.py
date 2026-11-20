"""Phase 4 data recorded now (D-39 to D-41): capacity events, no-shows, the
auditor's and cleaner's costs, and structured logs."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from tests.live.support import fresh_db, seed_interview
from tests.test_interviews import client, make_candidate  # noqa: F401  (fixture)

BACKEND = Path(__file__).resolve().parents[1]


def _capacity_rows(client):
    from app import db
    from live.models import CapacityEventRecord

    async def read():
        async with db.get_sessionmaker()() as s:
            return [(r.kind, r.interview_id, r.live_bots, r.limit)
                    for r in await s.scalars(select(CapacityEventRecord).order_by(CapacityEventRecord.id))]

    return client.portal.call(read)


async def test_every_start_and_every_refusal_for_being_full_is_recorded(client, monkeypatch):
    from app.capacity import registry

    cid = await make_candidate()
    created = client.post(f"/candidates/{cid}/interviews").json()
    payload = {"meeting_id": created["meeting_id"], "password": created["password"], "consent": True}
    assert client.post("/interviews/join", json=payload).status_code == 200
    started = _capacity_rows(client)
    assert started[-1][:3] == ("started", created["interview_id"], registry.live_count())
    # Full: the next candidate is refused, and that is recorded.
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")
    from app import db

    async def second():
        async with db.get_sessionmaker()() as s:
            s.add(db.Candidate(id="cand_two", job_id="job_1", name="B", cv_text="cv",
                               blueprint=(await s.get(db.Candidate, cid)).blueprint,
                               blueprint_status=db.BlueprintStatus.READY))
            await s.commit()

    client.portal.call(second)
    other = client.post("/candidates/cand_two/interviews").json()
    refused = client.post("/interviews/join", json={"meeting_id": other["meeting_id"], "password": other["password"],
                                                    "consent": True})
    assert refused.status_code == 503
    assert _capacity_rows(client)[-1] == ("refused", other["interview_id"], 1, 1)


async def test_a_booked_interview_nobody_joined_becomes_no_show_once_its_room_expires(tmp_path, monkeypatch):
    db = await fresh_db(tmp_path, monkeypatch, "noshow.db")
    from live.phase4 import mark_no_shows

    now = datetime.now(UTC)
    await seed_interview("int_noshow_old01", status="scheduled", started_ago=None)
    await seed_interview("int_noshow_new01", candidate_id="c2", status="scheduled", started_ago=None)  # room open
    await seed_interview("int_noshow_ran01", candidate_id="c3", status="completed", ended_ago=100)
    async with db.get_sessionmaker()() as s:
        for iid in ("int_noshow_old01", "int_noshow_ran01"):
            (await s.get(db.Interview, iid)).room_expires_at = now - timedelta(hours=1)
        await s.commit()
    assert await mark_no_shows(now) == 1
    async with db.get_sessionmaker()() as s:
        statuses = {i: (await s.get(db.Interview, i)).status
                    for i in ("int_noshow_old01", "int_noshow_new01", "int_noshow_ran01")}
    assert statuses == {"int_noshow_old01": "no_show", "int_noshow_new01": "scheduled",
                        "int_noshow_ran01": "completed"}
    assert await mark_no_shows(now) == 0  # once
    await db.reset_engine()


def test_a_no_show_is_refused_as_an_expired_link():
    from types import SimpleNamespace

    from app.interviews import _refusal

    refusal, _ = _refusal(SimpleNamespace(status="no_show", room_expires_at=datetime.now(UTC)), datetime.now(UTC))
    body = __import__("json").loads(refusal.body)  # a CandidateRefusal answer since C-163
    assert refusal.status_code == 410 and body["detail"] == "This interview link has expired."
    assert body["reason"] == "expired"


def test_check_intelligence_adds_the_auditors_and_the_cleaners_costs_once():
    from live import check_intel

    fixture = BACKEND / "tests" / "fixtures" / "check_intel" / "live_run_int_99bdc4598805.json"
    run = json.loads(fixture.read_text())
    metrics = {**run["session_metrics"], "auditor": {"stats": {"usd": 0.0123}},
               "text_model_calls": [{"job": "feedback", "cost_usd": 0.05}]}
    data = check_intel.build(interview_id="i", status="completed", started_at=None, ended_at=None,
                             transcript=run["transcript"], session_metrics=metrics, blueprint=None, events=[],
                             cleaned_transcript={"status": "ok", "usd": 0.004})
    jobs = data["tiles"]["cost"]["text_model_usd_by_job"]
    assert jobs == {"feedback": 0.05, "auditor": 0.0123, "cleaning": 0.004}
    realtime = run["session_metrics"]["usage"]["estimated_usd"]
    assert data["tiles"]["cost"]["total_usd"] == round(realtime + 0.05 + 0.0123 + 0.004, 6)
    # The stale "plan costs not stored" note is gone: they are stored since C-143 (C-166).
    assert not any("plan generation" in u["field"] for u in data["unfilled"])


def test_structured_logs_are_off_by_default_and_json_when_asked():
    code = ("import os; os.environ.setdefault('RECORDING_SWEEP_DISABLED','1');"
            "import app.main; from loguru import logger; logger.info('phase4-probe')")
    for env, expect_json in (({}, False), ({"LOG_JSON": "1"}, True)):
        run = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120,
                             env={**__import__("os").environ, **env, "LOG_JSON": env.get("LOG_JSON", "")})
        line = next(l for l in run.stderr.splitlines() if "phase4-probe" in l)
        if expect_json:
            record = json.loads(line)["record"]
            assert record["message"] == "phase4-probe" and record["level"]["name"] == "INFO"
        else:
            assert not line.startswith("{")
