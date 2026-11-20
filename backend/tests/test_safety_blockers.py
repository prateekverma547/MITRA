"""The safety blockers from the production review, each pinned by a test.

Each test here corresponds to a failure the review found and that was confirmed in
the code. The fixture and the candidate helper are shared with test_interviews so
that the join path is exercised the same way the rest of the suite exercises it.

What these tests cannot prove is the bot's behaviour on a real dropped connection.
The grace period lives in a closure inside `run_bot`, which only runs against a live
Daily room, so it is covered by a real call rather than by a unit test here.
"""

from datetime import UTC, datetime

from pathlib import Path

import pytest
from sqlalchemy import text

from tests.test_interviews import client, make_candidate  # noqa: F401  (shared fixture)

# --- response headers --------------------------------------------------------


def test_every_response_refuses_to_be_framed(client):
    """Clickjacking: without frame-ancestors, the admin panel can be layered under a
    transparent page and an admin can be made to click Delete."""
    response = client.get("/health")
    csp = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert response.headers["X-Frame-Options"] == "DENY"


def test_the_standard_hardening_headers_are_set(client):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "microphone=(self)" in response.headers["Permissions-Policy"]


def test_the_policy_allows_only_what_the_pages_load(client):
    """A policy that allows any script host would not stop the unpinned SDK problem
    from coming back through another route."""
    from app.main import CONTENT_SECURITY_POLICY

    script_src = next(d for d in CONTENT_SECURITY_POLICY.split(";") if "script-src" in d)
    tokens = script_src.split()
    # The SDK is bundled now, so no CDN is needed, and none is allowed.
    assert "https://unpkg.com" not in tokens
    assert "'unsafe-inline'" not in tokens and "'unsafe-eval'" not in tokens
    # Exact tokens only: a bare "*" or a scheme like "https:" would allow any host.
    assert "*" not in tokens and "https:" not in tokens


def test_the_daily_call_bundle_can_load_without_eval():
    """Daily's call object bundle loads by script tag (avoidEval, set on the candidate
    page), so script-src must allow Daily's hosts and must not need 'unsafe-eval'.
    The previous policy blocked c.dailywebrtc.net with a CSP EvalError."""
    from app.main import CONTENT_SECURITY_POLICY

    script_src = next(d for d in CONTENT_SECURITY_POLICY.split(";") if "script-src" in d)
    assert "'unsafe-eval'" not in script_src
    assert "https://*.dailywebrtc.net" in script_src
    assert "https://*.daily.co" in script_src
    from tests.frontend_source import join_source

    assert "avoidEval: true" in join_source()


def test_the_noise_filter_can_load_without_eval():
    """Krisp needs a blob: worker and WASM compilation. Allowing those must not
    quietly allow JavaScript eval as well."""
    from app.main import CONTENT_SECURITY_POLICY

    script_src = next(d for d in CONTENT_SECURITY_POLICY.split(";") if "script-src" in d)
    tokens = script_src.split()
    assert "blob:" in tokens and "'wasm-unsafe-eval'" in tokens
    assert "'unsafe-eval'" not in tokens
    worker_src = next(d for d in CONTENT_SECURITY_POLICY.split(";") if "worker-src" in d)
    assert "blob:" in worker_src.split()


def test_a_failed_noise_filter_never_leaves_the_candidate_muted():
    """Daily mutes the microphone when its audio processor fails. The interview must
    carry on unfiltered with the microphone back on, unless the candidate muted it."""
    from tests.frontend_source import join_source

    source = join_source()
    assert 'type: "noise-cancellation"' in source
    assert "audio-processor-error" in source and "input-settings-error" in source
    assert "micWanted() && !daily.localAudio()" in source
    assert "daily.setLocalAudio(true)" in source
    # Turned on after joining, so a filter that cannot load never blocks entry.
    assert "enableNoiseFilter(daily" in source
    assert "micWanted = !on" in source


def test_hsts_is_sent_only_over_https(client):
    assert "Strict-Transport-Security" not in client.get("/health").headers
    response = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=" in response.headers["Strict-Transport-Security"]


# --- the candidate page's script --------------------------------------------


def test_the_daily_sdk_is_pinned_and_checked():
    """The page holds the meeting password and the microphone. An unpinned script
    runs whatever unpkg serves next, and an unannounced release breaks interviews."""
    from tests.frontend_source import STATIC, join_source

    # The SDK is vendored at one exact version, so a release cannot change it silently. The
    # file is named for its version, and the page imports that exact file.
    vendored = STATIC / "vendor" / "daily-js-0.94.0.esm.js"
    assert vendored.exists()
    assert "../vendor/daily-js-0.94.0.esm.js" in join_source()
    # The record says where it came from and which version it is.
    assert "**exactly 0.94.0**" in (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    # Nothing loads the SDK from a CDN at runtime.
    assert "unpkg.com" not in join_source()
    assert "daily-js@" not in join_source()


# --- a join that goes wrong ---------------------------------------------------


async def _set_status(interview_id: str, status: str) -> None:
    from app import db

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        interview.status = status
        await session.commit()


async def _status_of(interview_id: str) -> tuple[str, str | None]:
    from app import db

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        return interview.status, interview.failure_reason


async def test_a_join_that_fails_before_spawning_gives_its_seat_back(client, monkeypatch):
    """A failure after the seat was reserved but before the bot started must not leave a
    phantom bot counted against the cap, and must leave the interview joinable."""
    from app import interviews
    from app.capacity import registry

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    async def broken_token(**kwargs):
        raise RuntimeError("Daily is unreachable")

    monkeypatch.setattr(interviews, "create_meeting_token", broken_token)
    with pytest.raises(RuntimeError):
        client.post(
            "/interviews/join",
            json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
        )

    assert registry.is_running(created["interview_id"]) is False
    status, _ = await _status_of(created["interview_id"])
    assert status == "scheduled"


async def test_a_running_interview_with_no_bot_is_closed_not_joined(client):
    """The stuck row: marked in progress, but the bot died. Sending the candidate into
    the empty room, and blocking a new session forever, was the failure."""
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    await _set_status(created["interview_id"], db.InterviewStatus.IN_PROGRESS)

    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert response.status_code == 410
    assert "interrupted" in response.json()["detail"]
    status, reason = await _status_of(created["interview_id"])
    assert status == "failed"
    assert reason


async def test_a_second_join_while_the_first_is_starting_is_refused(client):
    """Double-click or two tabs. The first request holds the seat and is still starting
    the bot; a second must not start a second bot into the same room."""
    from app.capacity import registry

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    registry.reserve(created["interview_id"])  # the first request, mid-start

    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert response.status_code == 409
    assert client.spawned == []


async def test_a_reconnect_to_a_live_bot_gets_a_token_and_no_second_bot(client):
    from app import db
    from app.capacity import registry

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    await _set_status(created["interview_id"], db.InterviewStatus.IN_PROGRESS)

    class LiveBot:
        returncode = None

    registry.register(created["interview_id"], LiveBot())
    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert response.status_code == 200
    assert client.spawned == []


async def test_a_failed_interview_cannot_be_rejoined(client):
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    await _set_status(created["interview_id"], db.InterviewStatus.FAILED)

    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert response.status_code == 410


# --- the database enforces one live session -----------------------------------


async def test_the_database_refuses_a_second_live_session_for_one_candidate(client):
    """The check in create_interview is a read followed by a write, so two simultaneous
    bookings could both pass it. The partial unique index is the guard that holds."""
    from app import db

    candidate_id = await make_candidate()
    client.post(f"/candidates/{candidate_id}/interviews")

    async with db.get_sessionmaker()() as session:
        exists = await session.scalar(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='uq_interviews_one_live_per_candidate'"
            )
        )
    assert exists == "uq_interviews_one_live_per_candidate"

    from sqlalchemy.exc import IntegrityError

    async with db.get_sessionmaker()() as session:
        session.add(
            db.Interview(
                id="int_duplicate",
                candidate_id=candidate_id,
                meeting_id="111111111",
                password="x" * 8,
                daily_room_name="room-dup",
                daily_room_url="https://example.daily.co/room-dup",
                room_expires_at=datetime.now(UTC),
                status=db.InterviewStatus.SCHEDULED,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


# --- restart -----------------------------------------------------------------


async def test_a_restart_closes_work_the_previous_process_left_behind(client):
    """Bots are children of the process. After a restart nothing is running, so a row
    still marked running (or feedback still generating) can never finish on its own."""
    from app import db, interviews

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    await _set_status(created["interview_id"], db.InterviewStatus.IN_PROGRESS)

    counts = await interviews.reconcile_after_restart()

    assert counts["interrupted"] == 1
    status, reason = await _status_of(created["interview_id"])
    assert status == "failed"
    assert "interrupted" in reason


async def test_a_restart_leaves_finished_interviews_alone(client):
    from app import db, interviews

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    await _set_status(created["interview_id"], db.InterviewStatus.COMPLETED)

    await interviews.reconcile_after_restart()

    status, _ = await _status_of(created["interview_id"])
    assert status == "completed"


# --- setup failures in the bot -------------------------------------------------


async def test_a_bot_that_fails_before_its_save_still_closes_its_interview(client):
    """Missing voice key, unknown blueprint: the bot never reaches its own save, so the
    row stayed in progress and blocked the candidate from ever joining again."""
    from app import db
    from bot.persistence import mark_failed_if_running

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    await _set_status(created["interview_id"], db.InterviewStatus.IN_PROGRESS)

    await mark_failed_if_running(interview_id=created["interview_id"], reason="ELEVENLABS_API_KEY is not set")

    status, reason = await _status_of(created["interview_id"])
    assert status == "failed"
    assert "ELEVENLABS" in reason


async def test_a_setup_failure_never_overwrites_a_saved_transcript(client):
    """Only a row still marked running may be closed here. A completed interview's
    transcript must survive a late failure report."""
    from app import db
    from bot.persistence import mark_failed_if_running

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    await _set_status(created["interview_id"], db.InterviewStatus.COMPLETED)

    await mark_failed_if_running(interview_id=created["interview_id"], reason="late error")

    status, _ = await _status_of(created["interview_id"])
    assert status == "completed"


def test_the_daily_call_bundle_host_is_allowed_to_connect():
    """Daily's SDK fetches its call bundle from dailywebrtc.net. Without it the browser
    blocks the fetch and the candidate sees "Failed to fetch" before joining."""
    from app.main import CONTENT_SECURITY_POLICY

    connect = next(d for d in CONTENT_SECURITY_POLICY.split(";") if "connect-src" in d)
    assert "https://*.dailywebrtc.net" in connect
    assert "wss://*.dailywebrtc.net" in connect
