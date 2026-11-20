"""Track H handoff (C-170, C-171): what their candidate room needs from the backend.

- The consent facts: /interview/config says whether the call is recorded, whether the
  candidate's own audio is captured, and whether after-call checks run, each read the
  same way the bot and the runner read it.
- Every refusal after correct credentials has one shape (CandidateRefusal), the reload
  refusals included.
"""

import pytest
from fastapi.testclient import TestClient

from tests.live.support import seed_interview


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/handoff.db")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main
    from live import credential_limits

    credential_limits.limits.reset()
    await db.reset_engine()
    with TestClient(main.app) as c:
        yield c
    await db.reset_engine()
    credential_limits.limits.reset()


@pytest.mark.parametrize("capture,aftercall", [("", ""), ("0", "1"), ("off", "yes"), ("1", "")])
def test_the_config_says_what_the_bot_and_the_runner_will_do(client, monkeypatch, capture, aftercall):
    from aftercall import settings
    from bot.capture import capture_enabled

    monkeypatch.setenv("CANDIDATE_AUDIO_CAPTURE", capture)
    monkeypatch.setenv("AFTERCALL_ENABLED", aftercall)
    body = client.get("/interview/config").json()
    assert body["candidate_audio_capture"] is capture_enabled()
    assert body["after_call_checks"] is settings.enabled()
    assert set(body) == {"schema_version", "retention_days", "recording_enabled", "candidate_audio_capture",
                         "after_call_checks"}


def test_capture_is_on_by_default_so_consent_must_mention_it(client, monkeypatch):
    monkeypatch.delenv("CANDIDATE_AUDIO_CAPTURE", raising=False)
    monkeypatch.delenv("AFTERCALL_ENABLED", raising=False)
    body = client.get("/interview/config").json()
    assert body["candidate_audio_capture"] is True and body["after_call_checks"] is False


def test_a_reload_refusal_has_the_same_shape_as_every_other(client):
    from app.capacity import registry

    client.portal.call(lambda: seed_interview("int_handoff00001", status="in_progress", meeting_id="333-444-555",
                                              password="kmnp3478"))
    assert not registry.is_running("int_handoff00001")  # no interviewer: a reload is refused
    for path in ("/interviews/check", "/interviews/join"):
        r = client.post(path, json={"meeting_id": "333-444-555", "password": "kmnp3478", "consent": True})
        assert r.status_code == 409 and r.json()["reason"] == "interviewer_gone"
        assert set(r.json()) == {"detail", "reason", "retry_after_seconds"}
