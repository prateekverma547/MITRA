"""Wiring Session D's after-call pipeline into the app (D-58).

Requirements: the pinned speaker model is fetched and checksum-verified in CI
and the image; the runner starts with the app and does nothing while switched
off; one retention rule deletes capture and result folders at 10 days; the
results reach the Integrity section data and a signed-in route; the settings
are documented with no values.
"""

import asyncio
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.live.support import ADMIN_PASSWORD, fresh_db, seed_interview

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent


# -- 1. the model in CI and the image -----------------------------------------------------------


def test_ci_fetches_and_checks_the_model_before_the_tests_and_caches_it_by_its_pin():
    steps = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())["jobs"]["backend-tests"]["steps"]
    runs = [s.get("run", "") for s in steps]
    fetch = runs.index("uv run python -m aftercall.models fetch && uv run python -m aftercall.models check")
    assert fetch < runs.index("uv run pytest -q") < runs.index("uv run python scripts/smoke_interview.py")
    [cache] = [s for s in steps if s.get("uses", "").startswith("actions/cache")]
    assert "hashFiles('backend/aftercall/models.py')" in cache["with"]["key"]


def test_the_image_fetches_and_checks_the_model_in_its_own_layer_and_never_copies_a_local_one():
    docker = (BACKEND / "Dockerfile").read_text()
    fetch = docker.index("RUN python -m aftercall.models fetch && python -m aftercall.models check")
    assert docker.index("RUN uv sync --frozen --no-dev --no-install-project") < fetch < docker.index("COPY backend/ ./")
    assert "backend/aftercall/models/*.onnx" in (REPO / ".dockerignore").read_text()


def test_a_model_file_with_the_wrong_checksum_is_refused(tmp_path):
    """What CI's `check` step relies on: a file of the right name but other bytes fails."""
    from aftercall import models

    (tmp_path / models.SPEAKER.name).write_bytes(b"\0" * models.SPEAKER.size)  # right size, wrong bytes
    run = subprocess.run([sys.executable, "-m", "aftercall.models", "check"], cwd=BACKEND, capture_output=True,
                         text=True, timeout=120, env={**os.environ, "AFTERCALL_MODELS_DIR": str(tmp_path)})
    assert run.returncode != 0 and "checksum differs" in run.stderr


# -- 2. the runner starts with the app and idles while off --------------------------------------


async def test_the_app_starts_the_runner(tmp_path, monkeypatch):
    await fresh_db(tmp_path, monkeypatch, "runner_start.db")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from aftercall import runner

    started = []

    async def fake_forever():
        started.append(True)
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "run_forever", fake_forever)
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app):
        pass
    assert started == [True]


async def _run_loop_briefly(monkeypatch, enabled: bool, iid: str) -> tuple[list, list]:
    from aftercall import runner, settings

    looked, ran = [], []
    real_find = runner.find_work

    async def spy_find():
        looked.append(True)
        return await real_find()

    async def fake_job(interview_id):
        ran.append(interview_id)
        return {}

    monkeypatch.setattr(runner, "find_work", spy_find)
    monkeypatch.setattr(runner, "run_job_in_subprocess", fake_job)
    monkeypatch.setattr(settings, "LOOP_SECONDS", 0.01)
    monkeypatch.setenv(settings.ENABLED_ENV, "1" if enabled else "")
    task = asyncio.create_task(runner.run_forever())
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return looked, ran


@pytest.mark.parametrize("enabled", [False, True])
async def test_the_runner_does_nothing_while_off_and_works_when_on(tmp_path, monkeypatch, enabled):
    await fresh_db(tmp_path, monkeypatch, "runner.db")
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    from aftercall import store
    from shared.interview_contracts import CaptureManifest

    iid = "int_aftercall_run1"
    await seed_interview(iid, status="completed", ended_ago=60)
    store.manifest_path(iid).parent.mkdir(parents=True, exist_ok=True)
    store.manifest_path(iid).write_text(CaptureManifest(interview_id=iid).model_dump_json())
    looked, ran = await _run_loop_briefly(monkeypatch, enabled, iid)
    if enabled:
        assert looked and ran and set(ran) == {iid}
    else:
        assert looked == [] and ran == []  # not even a database read while off


# -- 3. one retention rule ----------------------------------------------------------------------


async def test_capture_and_result_folders_older_than_10_days_go_once_and_newer_stay(tmp_path, monkeypatch):
    """The folders Session D's own code writes (aftercall/store.py paths), deleted by
    the one rule kept: the recordings sweep's (app/recordings.py)."""
    db = await fresh_db(tmp_path, monkeypatch, "retention.db")
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    from aftercall import store
    from app import recordings

    old, new = "int_ac_old000001", "int_ac_new000001"
    await seed_interview(old, status="completed", started_ago=11 * 86400, ended_ago=11 * 86400 - 1800)
    await seed_interview(new, candidate_id="c2", status="completed", started_ago=9 * 86400, ended_ago=9 * 86400)
    for iid in (old, new):
        store.capture_dir(iid).mkdir(parents=True)
        (store.capture_dir(iid) / "candidate.wav").write_bytes(b"RIFF")
        store.result_dir(iid).mkdir(parents=True)
        (store.result_dir(iid) / "status.json").write_text("{}")
    assert await recordings.expire_aftercall_files(datetime.now(UTC)) == 2  # old: capture and results
    assert not store.capture_dir(old).exists() and not store.result_dir(old).exists()
    assert store.capture_dir(new).exists() and store.result_dir(new).exists()
    assert await recordings.expire_aftercall_files(datetime.now(UTC)) == 0  # once
    await db.reset_engine()


# -- 4. the results in the report and on a route -------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/ac_route.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        c.portal.call(lambda: seed_interview("int_ac_report001", status="completed", ended_ago=60))
        c.portal.call(lambda: seed_interview("int_ac_empty0001", candidate_id="c2", status="completed", ended_ago=60))
        yield c
    await db.reset_engine()


def _write_results(iid: str):
    from aftercall import runner, store
    from shared.interview_contracts import AfterCallStatus, SecondVoiceSignal

    status = AfterCallStatus.model_validate({"interview_id": iid, "updated_at": "2026-11-19T10:00:00Z", "checks": [
        {"check": "second_voice", "state": "done"},
        {"check": "screen_content", "state": "skipped", "reason": "Screen frames are not captured in wave 1."}]})
    signal = SecondVoiceSignal(interview_id=iid, ranges=[{"start_seconds": 760.0, "end_seconds": 775.0}],
                               confidence=0.72, method="speaker embedding", method_version="1",
                               wording="A second voice may be audible from 12:40 to 12:55.",
                               integrity_event_id="ie_ac_sv1")
    store.write_status(status)
    store.write_signal(signal)
    store.write_events(iid, [runner.integrity_event(signal)])
    return status, signal


def test_the_report_merges_the_after_call_event_status_and_signal(client):
    from shared.interview_contracts import IntegrityReport

    status, signal = _write_results("int_ac_report001")
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    report = IntegrityReport.model_validate(client.get("/api/proctoring/interviews/int_ac_report001/report").json())
    assert report.analysed_after_call is True
    [event] = [e for e in report.events if e.source.value == "after_call"]
    assert event.kind.value == "second_voice" and event.reminder.value == "recorded_only"
    assert report.summary.events_by_kind == {event.kind: 1}
    assert report.after_call_status == status and report.second_voice == signal


def test_the_after_call_route_needs_sign_in_and_says_what_ran(client):
    from shared.interview_contracts import AfterCallResults

    _write_results("int_ac_report001")
    assert client.get("/api/proctoring/interviews/int_ac_report001/after-call").status_code == 401
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    full = AfterCallResults.model_validate(client.get("/api/proctoring/interviews/int_ac_report001/after-call").json())
    assert full.enabled is False  # off by default, even with old results on disk
    assert full.status.checks[0].state.value == "done" and full.second_voice.ranges[0].start_seconds == 760.0
    empty = client.get("/api/proctoring/interviews/int_ac_empty0001/after-call").json()
    assert empty["status"] is None and empty["second_voice"] is None
    report = client.get("/api/proctoring/interviews/int_ac_empty0001/report").json()
    assert report["analysed_after_call"] is False and report["second_voice"] is None
    assert client.get("/api/proctoring/interviews/int_nope/after-call").status_code == 404


def _pending_after_a_pause(iid: str):
    """What a pause mid-run leaves on disk: the check back at "pending" (runner.py)."""
    from aftercall import store
    from shared.interview_contracts import AfterCallStatus

    store.write_status(AfterCallStatus.model_validate({"interview_id": iid, "updated_at": "2026-11-19T10:00:00Z",
                                                       "checks": [{"check": "second_voice", "state": "pending"}]}))


def test_the_route_says_paused_while_the_paused_file_exists_and_not_after(client, monkeypatch):
    """B-104: a paused check is "pending" on disk; `paused` is what tells it from "not run yet"."""
    from aftercall import settings

    monkeypatch.setenv(settings.ENABLED_ENV, "1")
    monkeypatch.delenv(settings.PAUSED_ENV, raising=False)
    _pending_after_a_pause("int_ac_report001")
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    url = "/api/proctoring/interviews/int_ac_report001/after-call"
    before = client.get(url).json()
    assert before["paused"] is False and before["status"]["checks"][0]["state"] == "pending"
    flag = settings.aftercall_root() / "PAUSED"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    during = client.get(url).json()
    assert during["paused"] is True and during["status"]["checks"][0]["state"] == "pending"
    flag.unlink()
    assert client.get(url).json()["paused"] is False


def test_the_route_says_paused_when_the_server_started_with_the_setting(client, monkeypatch):
    from aftercall import settings

    monkeypatch.setenv(settings.PAUSED_ENV, "1")
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    body = client.get("/api/proctoring/interviews/int_ac_empty0001/after-call").json()
    assert body["paused"] is True and body["status"] is None


def test_paused_is_always_sent_and_old_answers_without_it_still_load():
    """Additive: the frontend's exporter marks every field required, so the route
    always sends it; an answer from before the field still validates (as false)."""
    from shared.interview_contracts import AfterCallResults

    old = AfterCallResults.model_validate({"interview_id": "int_x", "enabled": False})
    assert old.paused is False and "paused" in old.model_dump(mode="json")

# -- 5. the settings ----------------------------------------------------------------------------


def test_the_after_call_settings_are_documented_with_no_values():
    text = (REPO / ".env.example").read_text()
    for name in ("AFTERCALL_ENABLED", "AFTERCALL_PAUSED", "AFTERCALL_MODELS_DIR"):
        assert re.search(rf"^{name}=$", text, re.M), name


def test_the_paused_setting_says_start_up_only_and_where_the_paused_file_goes(tmp_path, monkeypatch):
    """D-67 / D-74: the setting cannot pause a running job; the PAUSED file does.
    The path the comment names is the one the runner actually checks."""
    text = (REPO / ".env.example").read_text()
    comment = text[: text.index("\nAFTERCALL_PAUSED=")].rsplit("\n\n", 1)[-1].split("AFTERCALL_ENABLED=")[-1]
    flat = " ".join(line.lstrip("# ").strip() for line in comment.splitlines())
    assert "read only as the server starts" in flat and "cannot pause a job that is already running" in flat
    assert "<RECORDINGS_DIR>/aftercall/PAUSED" in flat and "backend/sessions/recordings/aftercall/PAUSED" in flat
    assert "without a restart; a file" not in flat  # the old wording, which said the setting needed no restart
    from aftercall import settings

    monkeypatch.delenv("RECORDINGS_DIR", raising=False)
    assert settings.aftercall_root() / "PAUSED" == BACKEND / "sessions" / "recordings" / "aftercall" / "PAUSED"
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    assert settings.aftercall_root() / "PAUSED" == tmp_path / "aftercall" / "PAUSED"
