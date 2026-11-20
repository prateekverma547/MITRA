"""Logging: the shape of each line, what is never written, and the request trail.

Deterministic: no network, no model calls. The log is captured with a loguru sink, so
these tests see exactly what a container would write to stdout.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import contextlib

import pytest
from loguru import logger

from shared.logging_setup import SECRET_FIELD_NAMES, format_json, redact

# Imported at module load, not inside a test. Importing the app configures logging, and
# configuring logging removes every sink, including one a fixture has just added.
from app.main import app  # noqa: E402


@pytest.fixture
def captured():
    """Every record that reaches the log, after redaction (the patcher runs first)."""
    records: list = []
    sink_id = logger.add(lambda message: records.append(message.record), level="DEBUG")
    yield records
    with contextlib.suppress(ValueError):  # already gone if something reconfigured logging
        logger.remove(sink_id)


def _as_text(records) -> str:
    return "\n".join(repr(r) for r in records)


# --- redaction ---------------------------------------------------------------------


def test_api_key_shaped_strings_are_masked():
    text = redact("calling with key sk-proj-AbCdEfGhIjKlMnOpQrStUvWx")
    assert "AbCdEfGh" not in text
    assert "sk-***" in text


def test_daily_meeting_tokens_are_masked():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyIjoiQ2kxVFJB.HyTqg8fFTt-xLcgHbUDtwI1BB8FShZAP"
    text = redact(f"joining with {jwt}")
    assert jwt not in text
    assert "***jwt***" in text


def test_secret_assignments_are_masked_whatever_the_case():
    assert redact("password=hunter2") == "password=***"
    assert redact("Token: abc123xyz") == "Token: ***"


def test_redaction_is_idempotent():
    once = redact("sk-proj-AbCdEfGhIjKlMnOpQrStUvWx")
    assert redact(once) == once


def test_ordinary_text_is_untouched():
    assert redact("[int_9b979b242139] transcript saved: 26 turn(s)") == (
        "[int_9b979b242139] transcript saved: 26 turn(s)"
    )


# --- the json line -----------------------------------------------------------------


def _record(message: str, **extra) -> dict:
    return {
        "time": datetime(2026, 11, 6, 9, 30, 0, tzinfo=UTC),
        "level": SimpleNamespace(name="INFO"),
        "name": "app.main",
        "message": message,
        "extra": extra,
        "exception": None,
    }


def test_each_line_is_one_json_object_with_the_fields_a_search_needs():
    line = json.loads(format_json(_record("POST /jobs 200 12.3ms", status=200, process="web")))

    assert line["level"] == "INFO"
    assert line["logger"] == "app.main"
    assert line["msg"] == "POST /jobs 200 12.3ms"
    assert line["status"] == 200
    assert line["ts"].endswith("+00:00")


def test_secret_named_fields_are_dropped_from_the_line():
    line = json.loads(
        format_json(_record("x", password="hunter2", bot_token="eyJ.abc", cookie="mitra_admin=abc", interview_id="int_1"))
    )
    for name in ("password", "bot_token", "cookie"):
        assert name not in line
    assert line["interview_id"] == "int_1"
    assert {"password", "token", "cookie", "authorization"} <= SECRET_FIELD_NAMES


def test_a_secret_inside_a_value_is_still_masked():
    line = json.loads(format_json(_record("connecting with sk-proj-AbCdEfGhIjKlMnOpQrStUvWx", note="sk-proj-AbCdEfGhIjKlMnOpQrStUvWx")))
    assert "AbCdEfGh" not in json.dumps(line)


# --- what reaches the log ----------------------------------------------------------


def test_a_request_is_logged_once_with_its_method_path_and_status(captured):
    from fastapi.testclient import TestClient

    from app.main import app

    TestClient(app).get("/jobs")

    lines = [r for r in captured if r["extra"].get("path") == "/jobs"]
    assert len(lines) == 1
    assert lines[0]["extra"]["method"] == "GET"
    assert lines[0]["extra"]["status"] == 401
    assert lines[0]["extra"]["duration_ms"] >= 0


def test_a_request_id_is_echoed_when_it_is_well_formed(captured):
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/health", headers={"X-Request-ID": "abc-12345678"})

    assert response.headers["X-Request-ID"] == "abc-12345678"
    line = next(r for r in captured if r["extra"].get("path") == "/health")
    assert line["extra"]["request_id"] == "abc-12345678"


def test_a_malformed_request_id_is_replaced_not_logged(captured):
    """A caller must not be able to write arbitrary text into the log through this header."""
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/health", headers={"X-Request-ID": "bad id\nwith newline"})

    issued = response.headers["X-Request-ID"]
    assert issued != "bad id\nwith newline"
    assert len(issued) == 32
    assert "newline" not in _as_text(captured)


def test_a_health_check_is_logged_at_debug_not_info(captured):
    from fastapi.testclient import TestClient

    from app.main import app

    TestClient(app).get("/health")

    line = next(r for r in captured if r["extra"].get("path") == "/health")
    assert line["level"].name == "DEBUG"


def test_no_cookie_or_credential_reaches_the_log(captured, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("ADMIN_PASSWORD", "admin-password-0123456789")
    TestClient(app).get("/jobs", headers={"Cookie": "mitra_admin=SECRETVALUE123456.abcdef"})

    text = _as_text(captured)
    assert "SECRETVALUE" not in text
    assert "admin-password-0123456789" not in text


def test_the_transcript_text_is_never_logged(captured, tmp_path):
    """Candidate speech goes to the database and the session file. It must not go to the
    log, which leaves the container and is kept for weeks."""
    from bot.observers import write_session_artifacts

    marker = "MY SALARY EXPECTATION IS CONFIDENTIAL"
    turns = [{"at_seconds": 3.0, "speaker": "candidate", "text": marker}]
    transcript = SimpleNamespace(turns=turns)
    latency = SimpleNamespace(
        summary=lambda: {"ttfa_median_ms": 1500},
        turns=[],
    )

    write_session_artifacts(
        directory=tmp_path,
        session_id="int_test",
        transcript=transcript,
        latency=latency,
    )

    assert marker not in _as_text(captured)
    assert marker in (tmp_path / "int_test.json").read_text(encoding="utf-8")  # kept where it belongs
    assert any("transcript saved: 1 turn(s)" in r["message"] for r in captured)


def test_a_bad_log_format_is_refused(monkeypatch):
    from shared.logging_setup import configure_logging

    monkeypatch.setenv("LOG_FORMAT", "yaml")
    with pytest.raises(ValueError):
        configure_logging("web")
