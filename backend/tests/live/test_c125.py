"""Session B's closing items (C-125): the security-rule log wording, two sweeps
at once, and Check Intelligence's fallback to the auditor's stored labels."""

import asyncio

from loguru import logger

from tests.live.test_recording_round2 import IID, _row, client  # noqa: F401  (fixture)
from tests.test_recordings import a_recording_file


def test_the_violation_log_says_what_the_rule_does():
    from live import csp_reports, interview_static

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        csp_reports.record("script-src", "https://evil.example/x.js")
    finally:
        logger.remove(sink)
    assert interview_static.CSP_HEADER == "Content-Security-Policy"  # enforcing (B-89)
    assert "csp blocked violation" in lines[0] and "report-only" not in lines[0]


def test_two_sweeps_at_once_download_once_and_count_no_failure(client, monkeypatch):
    from app import recordings
    from bot.services.daily import DailyRecording

    downloads, deletes = [], []

    async def find(*, api_key, room_name):
        return DailyRecording(id="daily-rec-7", room_name=room_name, start_ts=0, duration_seconds=60)

    async def slow_download(*, api_key, recording_id, destination):
        downloads.append(recording_id)
        await asyncio.sleep(0.3)  # long enough for the second sweep to arrive mid-download
        a_recording_file(destination, size=4000)
        return destination.stat().st_size

    async def delete(*, api_key, recording_id):
        deletes.append(recording_id)

    monkeypatch.setattr(recordings, "find_recording", find)
    monkeypatch.setattr(recordings, "download_recording", slow_download)
    monkeypatch.setattr(recordings, "delete_recording", delete)

    async def both():
        return await asyncio.gather(recordings.run_sweep(api_key="t"), recordings.run_sweep(api_key="t"))

    first, second = client.portal.call(both)
    assert len(downloads) == 1 and len(deletes) == 1
    assert sorted([first["stored"], second["stored"]]) == [0, 1]
    assert sorted([first["busy"], second["busy"]]) == [0, 1]
    assert first["error"] == second["error"] == 0
    row = _row(client)
    assert row.recording_status == "stored" and row.recording_collect_by is None  # the claim released


def test_check_intel_falls_back_to_the_auditors_stored_label_and_says_so():
    import json
    from pathlib import Path

    from live import check_intel
    from live.emit import make_event
    from shared.interview_contracts import EventSource, QuestionLabelled

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "check_intel" / "live_run_int_99bdc4598805.json"
    run = json.loads(fixture.read_text())
    plan = json.loads((Path(__file__).resolve().parents[2] / "bot" / "fixtures" / "pm_senior.json").read_text())
    metrics = {**run["session_metrics"], "auditor": {"labels": {
        "2": {"label": "plan", "question_id": "Q2.1", "area": 2},
        "4": {"label": "follow_up", "question_id": None, "area": 2},   # B-90 A1: refused live, kept stored
        "6": {"label": "not_a_question", "question_id": None, "area": 2}}}}
    live = [make_event(run["interview_id"], QuestionLabelled(turn_index=2, label="plan", question_id="Q2.2"),
                       source=EventSource.AUDITOR, at_seconds=36.0)]
    data = check_intel.build(interview_id=run["interview_id"], status="completed", started_at=None, ended_at=None,
                             transcript=run["transcript"], session_metrics=metrics, blueprint=plan, events=live)
    rows = {r["turn_index"]: r for r in data["rows"]}
    assert (rows[2]["label"], rows[2]["question_id"], rows[2]["label_source"]) == ("plan", "Q2.2", "live")
    assert (rows[4]["label"], rows[4]["label_source"], rows[4]["area_id"]) == ("follow_up", "stored", "product_strategy")
    assert (rows[6]["label"], rows[6]["label_source"]) == ("not yet labelled", None)
    assert rows[0]["label_source"] is None
