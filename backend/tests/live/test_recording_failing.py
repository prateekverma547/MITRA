"""A recording whose download keeps failing (C-103, from C-102).

Requirements: after DOWNLOAD_FAILING_AFTER failed downloads in a row the
console shows "download_failing" with a short, plain reason; the sweep keeps
retrying while Daily has the file; a later success clears it and stores; it is
logged loudly once, not on every attempt; the signed download link never
reaches a log or the database; recordings stored before this still load.
The 403 is simulated (no Daily, no paid calls).
"""

import pytest
from loguru import logger

from app import recordings
from tests.live.support import ADMIN_PASSWORD
from tests.live.test_recording_round2 import IID, _good, _row, _sweep, client  # noqa: F401  (fixture)

SIGNED = "https://storage.daily.example/rec.mp4?X-Amz-Signature=SECRETSIG123&X-Amz-Credential=AKIDEXAMPLE"


def refused(destination):
    raise RuntimeError("Downloading the recording failed (403).")


def leaks_the_link(destination):
    # An HTTP library's error quoting the request's address, signature and all.
    import aiohttp

    raise aiohttp.ClientPayloadError(f"Response payload is not completed for {SIGNED}")


def _logs():
    lines: list[tuple[str, str]] = []
    sink = logger.add(lambda m: lines.append((m.record["level"].name, str(m))), level="DEBUG")
    return lines, sink


def _info(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    return client.get(f"/api/live/interviews/{IID}/recording").json()


def test_repeated_failures_reach_the_failing_state_after_the_chosen_count(client, monkeypatch):
    assert recordings.DOWNLOAD_FAILING_AFTER == 3
    for attempt in range(1, recordings.DOWNLOAD_FAILING_AFTER):
        counts, steps = _sweep(client, monkeypatch, refused)
        assert counts["error"] == 1 and steps == ["download"]  # Daily's copy untouched
        row = _row(client)
        assert row.recording_download_failures == attempt and row.recording_error is None
        assert recordings.console_status(row) == "recording"
    _sweep(client, monkeypatch, refused)
    row = _row(client)
    assert row.recording_status == "recording"  # the database status is unchanged: still at Daily
    assert recordings.console_status(row) == "download_failing"
    info = _info(client)
    assert info["status"] == "download_failing" and info["media_url"] is None
    assert info["error"] == ("Download refused by Daily (403). The recording is still at Daily and "
                             "is tried again every 5 minutes.")


def test_it_keeps_retrying_and_a_later_success_clears_the_failing_state(client, monkeypatch):
    for _ in range(recordings.DOWNLOAD_FAILING_AFTER + 2):
        _sweep(client, monkeypatch, refused)
    assert _row(client).recording_download_failures == recordings.DOWNLOAD_FAILING_AFTER + 2
    counts, steps = _sweep(client, monkeypatch, _good)  # still tried, and this time it works
    assert counts["stored"] == 1 and steps == ["download", "verified", "delete daily copy"]
    row = _row(client)
    assert row.recording_status == "stored" and row.recording_error is None
    assert row.recording_download_failures == 0
    assert _info(client)["status"] == "stored"


def test_files_that_fail_verification_count_as_failed_downloads(client, monkeypatch):
    def junk(destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"<html>expired</html>")
        return destination.stat().st_size

    for _ in range(recordings.DOWNLOAD_FAILING_AFTER):
        _sweep(client, monkeypatch, junk)
    row = _row(client)
    assert recordings.console_status(row) == "download_failing"
    assert row.recording_error == ("The downloaded file was not a whole, playable video. The recording is "
                                   "still at Daily and is tried again every 5 minutes.")


def test_the_loud_line_is_logged_once_not_on_every_attempt(client, monkeypatch):
    lines, sink = _logs()
    try:
        for _ in range(recordings.DOWNLOAD_FAILING_AFTER + 3):
            _sweep(client, monkeypatch, refused)
    finally:
        logger.remove(sink)
    loud = [text for level, text in lines if level == "ERROR" and IID in text]
    assert len(loud) == 1 and "RECORDING DOWNLOAD FAILING" in loud[0] and "(403)" in loud[0]
    quiet = [text for level, text in lines if level == "INFO" and "download attempt" in text]
    assert len(quiet) == recordings.DOWNLOAD_FAILING_AFTER + 2  # every other attempt: one quiet line
    assert not any(level == "WARNING" and "download" in text.lower() for level, text in lines)


def test_the_signed_link_never_reaches_a_log_or_the_database(client, monkeypatch):
    lines, sink = _logs()
    try:
        for _ in range(recordings.DOWNLOAD_FAILING_AFTER + 1):
            _sweep(client, monkeypatch, leaks_the_link)
    finally:
        logger.remove(sink)
    logged = "\n".join(text for _, text in lines)
    assert "download attempt" in logged  # the attempts were logged (the search has something to search)
    for secret in ("SECRETSIG123", "AKIDEXAMPLE", "X-Amz", "storage.daily.example"):
        assert secret not in logged, secret
        assert secret not in (_row(client).recording_error or "")
    assert _row(client).recording_error.startswith("The download did not complete (ClientPayloadError).")


@pytest.mark.parametrize("message,reason", [
    ("Downloading the recording failed (403).", "Download refused by Daily (403)."),
    ("Downloading the recording failed (401).", "Download refused by Daily (401)."),
    ("Downloading the recording failed (404).", "Daily's download link found no file (404)."),
    ("Downloading the recording failed (503).", "Daily's storage answered 503."),
    ("Daily access link failed (429): {'error': 'rate-limit'}", "Daily would not give a download link (429)."),
    ("Daily returned an access link with no download URL.", "Daily gave no download link."),
    ("The recording download was incomplete (100 of 2000 bytes).", "The download stopped early (100 of 2000 bytes)."),
    ("The recording downloaded as an empty file.", "The download was empty."),
    (f"something new {SIGNED}", "The download did not complete (RuntimeError)."),
])
def test_every_reason_is_short_plain_and_built_without_the_errors_text(message, reason):
    assert recordings.download_failure_reason(RuntimeError(message)) == reason
    assert len(reason) <= 80


async def test_recordings_stored_before_this_still_load(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy import text

    from tests.live.support import fresh_db, seed_interview

    db = await fresh_db(tmp_path, monkeypatch, "old_rec.db")
    await seed_interview("int_rec_old_0001", status="completed", ended_ago=60, recording_status="stored",
                         recording_path="recordings/int_rec_old_0001.mp4")
    async with db.get_engine().begin() as conn:  # the database as it was before C-103
        await conn.execute(text("ALTER TABLE interviews DROP COLUMN recording_download_failures"))
    await db.create_all()  # start-up adds the column back
    async with db.get_sessionmaker()() as session:
        row = await session.get(db.Interview, "int_rec_old_0001")
        assert row.recording_download_failures is None
        assert recordings.console_status(row) == "stored"
    # A row still collecting from before this existed reads as not failing.
    assert recordings.console_status(SimpleNamespace(recording_status="recording",
                                                     recording_download_failures=None)) == "recording"
    await db.reset_engine()
