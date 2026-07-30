"""Interview lifecycle and join authentication.

No Daily, no bot, no LLM — those are stubbed. What is under test is the glue
that lets an interview exist at all, and the access control on joining one.

The join endpoint is the only unauthenticated door into this system. A candidate
types a meeting ID and a password and gets a token into a live room. It gets the
most attention here.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.meeting import (
    new_meeting_id,
    new_password,
    normalise_meeting_id,
    passwords_match,
)
from shared.contracts import Competency, CompetencyPlan, EvaluationSpec, InterviewBlueprint

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "documents")


def blueprint_for(candidate_id: str) -> InterviewBlueprint:
    return InterviewBlueprint(
        blueprint_id=candidate_id,
        evaluation_spec=EvaluationSpec(
            role_title="Lead Product Manager — AI & Data Products",
            seniority="Senior",
            experience_expectation="10+ years",
            duration_minutes=40,
            competencies=[Competency(id="a", name="A", description="d", weight=1.0)],
        ),
        candidate_name="Prateek Verma",
        candidate_summary="Twelve years in AI product.",
        competency_plans=[
            CompetencyPlan(
                competency_id="a",
                name="A",
                target_depth="deep",
                seed_questions=["q"],
                time_budget_minutes=30.0,
            )
        ],
        suggested_opening="Hello.",
    )


class FakeRoom:
    def __init__(self):
        self.name = "abc123"
        self.url = "https://example.daily.co/abc123"
        self.expires_at = 0


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/int.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    from app import db, interviews, main

    await db.reset_engine()

    spawned: list[dict] = []

    async def fake_create_room(**kwargs):
        return FakeRoom()

    async def fake_create_token(**kwargs):
        return f"token-for-{kwargs.get('room_name')}-owner={kwargs.get('is_owner')}"

    async def fake_start_bot(**kwargs):
        spawned.append(kwargs)
        async with db.get_sessionmaker()() as session:
            interview = await session.get(db.Interview, kwargs["interview_id"])
            interview.status = db.InterviewStatus.IN_PROGRESS
            interview.started_at = datetime.now(UTC)
            await session.commit()

    monkeypatch.setattr(interviews, "create_room", fake_create_room)
    monkeypatch.setattr(interviews, "create_meeting_token", fake_create_token)
    monkeypatch.setattr(interviews, "_start_bot", fake_start_bot)

    with TestClient(main.app) as test_client:
        test_client.spawned = spawned
        test_client.post("/admin/login", json={"password": "test-admin-pw"})
        yield test_client

    await db.reset_engine()


async def make_candidate(*, ready: bool = True) -> str:
    """Insert a job and candidate directly — the upload path is tested elsewhere."""
    from app import db

    candidate_id = "cand_test1"
    async with db.get_sessionmaker()() as session:
        session.add(db.Job(id="job_1", jd_text="jd", spec_status=db.SpecStatus.READY))
        await session.commit()
    async with db.get_sessionmaker()() as session:
        session.add(
            db.Candidate(
                id=candidate_id,
                job_id="job_1",
                name="Prateek Verma",
                cv_text="cv",
                blueprint=blueprint_for(candidate_id).model_dump(mode="json") if ready else None,
                blueprint_status=(
                    db.BlueprintStatus.READY if ready else db.BlueprintStatus.GENERATING
                ),
            )
        )
        await session.commit()
    return candidate_id


# -- credentials -------------------------------------------------------------


def test_meeting_ids_are_readable_aloud():
    for _ in range(20):
        value = new_meeting_id()
        assert len(value) == 11 and value.count("-") == 2
        assert value.replace("-", "").isdigit()


def test_password_alphabet_contains_no_confusable_pair():
    """These get read out loud and typed by hand.

    Asserts the property rather than a hand-written list of bad characters —
    the first version of this test listed "0O1lI5S" while the alphabet still
    contained both `5` and `s`.
    """
    from app.meeting import CONFUSABLE_PAIRS, PASSWORD_ALPHABET

    for first, second in CONFUSABLE_PAIRS:
        assert not (first in PASSWORD_ALPHABET and second in PASSWORD_ALPHABET), (
            f"'{first}' and '{second}' are confusable but both are in the alphabet"
        )


def test_passwords_use_only_the_safe_alphabet():
    from app.meeting import PASSWORD_ALPHABET

    for _ in range(50):
        assert set(new_password()) <= set(PASSWORD_ALPHABET)


@pytest.mark.parametrize(
    "typed",
    ["428-193-756", "428193756", "428 193 756", " 428-193-756 ", "428.193.756"],
)
def test_meeting_ids_survive_how_people_actually_type_them(typed):
    """Refusing a correct ID over a stray space is a pointless way to start
    someone's interview."""
    assert normalise_meeting_id(typed) == "428-193-756"


@pytest.mark.parametrize("typed", ["", "42819375", "4281937560", "abc-def-ghi"])
def test_malformed_meeting_ids_are_rejected(typed):
    assert normalise_meeting_id(typed) == ""


def test_password_comparison_is_case_insensitive():
    assert passwords_match("Ab3Kd9Xy", "ab3kd9xy")
    assert not passwords_match("ab3kd9xy", "ab3kd9xz")


# -- creating an interview ---------------------------------------------------


async def test_interview_is_created_with_credentials(client):
    candidate_id = await make_candidate()

    response = client.post(f"/candidates/{candidate_id}/interviews")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "scheduled"
    assert normalise_meeting_id(body["meeting_id"]) == body["meeting_id"]
    assert len(body["password"]) >= 8


async def test_interview_cannot_be_created_without_a_ready_blueprint(client):
    """An interview with no plan to run is not an interview."""
    candidate_id = await make_candidate(ready=False)

    response = client.post(f"/candidates/{candidate_id}/interviews")

    assert response.status_code == 409
    assert "not ready" in response.json()["detail"]


async def test_scheduling_does_not_start_a_bot(client):
    """Booking a week early must not park a bot in an empty room."""
    candidate_id = await make_candidate()
    client.post(f"/candidates/{candidate_id}/interviews")

    assert client.spawned == []


# -- joining -----------------------------------------------------------------


async def test_correct_credentials_return_a_token_and_start_the_bot(client):
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["room_url"].startswith("https://")
    assert body["candidate_name"] == "Prateek Verma"
    assert len(client.spawned) == 1


async def test_wrong_password_is_refused(client):
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": "wrongpass", "consent": True},
    )

    assert response.status_code == 401
    assert client.spawned == []


async def test_unknown_and_wrong_password_are_indistinguishable(client):
    """Different messages would let someone probe for valid meeting IDs."""
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    wrong_password = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": "wrongpass", "consent": True},
    )
    no_such_meeting = client.post(
        "/interviews/join", json={"meeting_id": "111-222-333", "password": "wrongpass", "consent": True}
    )

    assert wrong_password.status_code == no_such_meeting.status_code == 401
    assert wrong_password.json()["detail"] == no_such_meeting.json()["detail"]


async def test_rejoining_does_not_start_a_second_bot(client):
    """A candidate refreshing the page must not put two interviewers in the room."""
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    payload = {
        "meeting_id": created["meeting_id"],
        "password": created["password"],
        "consent": True,
    }

    client.post("/interviews/join", json=payload)
    second = client.post("/interviews/join", json=payload)

    assert second.status_code == 200
    assert len(client.spawned) == 1


async def test_a_join_is_refused_when_the_container_is_full(client, monkeypatch):
    """Refusing one candidate beats OOM-killing everyone else's interview."""
    from app.capacity import registry

    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")

    class Running:
        returncode = None

    registry.register("int_someone_else", Running())
    try:
        candidate_id = await make_candidate()
        created = client.post(f"/candidates/{candidate_id}/interviews").json()

        response = client.post(
            "/interviews/join",
            json={
                "meeting_id": created["meeting_id"],
                "password": created["password"],
                "consent": True,
            },
        )

        assert response.status_code == 503
        assert "still valid" in response.json()["detail"]
        assert client.spawned == []
    finally:
        registry.release("int_someone_else")


async def test_a_refused_join_does_not_record_consent(client, monkeypatch):
    """Consent means "this interview is being recorded". If no interview
    happened, storing an acceptance would put a false record against a person."""
    from app import db
    from app.capacity import registry

    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")

    class Running:
        returncode = None

    registry.register("int_someone_else", Running())
    try:
        candidate_id = await make_candidate()
        created = client.post(f"/candidates/{candidate_id}/interviews").json()
        client.post(
            "/interviews/join",
            json={
                "meeting_id": created["meeting_id"],
                "password": created["password"],
                "consent": True,
            },
        )

        async with db.get_sessionmaker()() as session:
            interview = await session.get(db.Interview, created["interview_id"])
            assert interview.consent_accepted_at is None
            assert interview.status == db.InterviewStatus.SCHEDULED
    finally:
        registry.release("int_someone_else")


async def test_capacity_does_not_block_a_candidate_rejoining(client, monkeypatch):
    """Their bot is already running, so reconnecting costs no new memory.
    Refusing them would strand someone mid-interview on a dropped connection."""
    from app.capacity import registry

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    payload = {
        "meeting_id": created["meeting_id"],
        "password": created["password"],
        "consent": True,
    }
    assert client.post("/interviews/join", json=payload).status_code == 200

    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")

    class Running:
        returncode = None

    registry.register("int_someone_else", Running())
    try:
        assert client.post("/interviews/join", json=payload).status_code == 200
    finally:
        registry.release("int_someone_else")


async def test_expired_room_is_refused(client):
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.room_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert response.status_code == 410
    assert "expired" in response.json()["detail"]


async def test_completed_interview_cannot_be_rejoined(client):
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        await session.commit()

    response = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    )

    assert response.status_code == 410


async def test_join_response_tells_the_candidate_nothing_about_scoring(client):
    """The candidate's page has no business knowing what it is being scored on."""
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    body = client.post(
        "/interviews/join",
        json={"meeting_id": created["meeting_id"], "password": created["password"], "consent": True},
    ).json()

    serialised = str(body).lower()
    for leak in ("competenc", "weight", "red_flag", "target_depth", "seed_question"):
        assert leak not in serialised


# -- persistence -------------------------------------------------------------


async def test_transcript_is_written_back_to_the_interview(client):
    """The bot writes this on exit; here we check the record round-trips."""
    from app import db
    from bot.persistence import build_transcript, save_interview_result

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    interview_id = created["interview_id"]

    transcript = build_transcript(
        interview_id=interview_id,
        turns=[
            {"speaker": "pre_interview_audio", "text": "Enjoyment", "at_seconds": 1.0},
            {"speaker": "interviewer", "text": "Good evening.", "at_seconds": 2.0},
            {"speaker": "candidate", "text": "Hello there.", "at_seconds": 4.0},
        ],
        duration_seconds=310.0,
    )
    await save_interview_result(
        interview_id=interview_id,
        transcript=transcript,
        section_outcomes=[{"section_id": "a"}],
        session_metrics={"latency_summary": {"ttfa_median_ms": 2100.0}},
    )

    view = client.get(f"/interviews/{interview_id}").json()
    assert view["status"] == "completed"
    assert view["ended_at"]
    speakers = [t["speaker"] for t in view["transcript"]["turns"]]
    # Room noise never becomes part of the candidate's record.
    assert speakers == ["interviewer", "candidate"]
    assert view["section_outcomes"] == [{"section_id": "a"}]
    # Telemetry must survive too: on Railway the session files are ephemeral,
    # so the database is the only place latency data can live.
    assert view["session_metrics"]["latency_summary"]["ttfa_median_ms"] == 2100.0


async def test_saving_against_a_missing_interview_does_not_raise(client):
    """A database problem must not lose the session on top of whatever already
    went wrong."""
    from bot.persistence import build_transcript, save_interview_result

    await save_interview_result(
        interview_id="int_nope",
        transcript=build_transcript(interview_id="int_nope", turns=[], duration_seconds=0.0),
        section_outcomes=[],
    )


async def test_interviews_are_listed_for_a_candidate(client):
    """History accumulates once sessions actually finish."""
    from app import db

    candidate_id = await make_candidate()
    first = client.post(f"/candidates/{candidate_id}/interviews").json()

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, first["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        await session.commit()

    client.post(f"/candidates/{candidate_id}/interviews")

    rows = client.get(f"/candidates/{candidate_id}/interviews").json()

    assert len(rows) == 2
    assert {r["status"] for r in rows} == {"completed", "scheduled"}


# -- one session at a time ---------------------------------------------------


async def test_a_second_session_is_refused_while_one_is_open(client):
    """Two sessions meant two Daily rooms and two credential pairs — and
    whichever set the candidate got, the other room sat paid for and empty."""
    candidate_id = await make_candidate()
    first = client.post(f"/candidates/{candidate_id}/interviews")
    assert first.status_code == 200

    second = client.post(f"/candidates/{candidate_id}/interviews")

    assert second.status_code == 409
    assert first.json()["meeting_id"] in second.json()["detail"]


async def test_a_new_session_is_allowed_once_the_last_one_completed(client):
    from app import db

    candidate_id = await make_candidate()
    first = client.post(f"/candidates/{candidate_id}/interviews").json()

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, first["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        await session.commit()

    second = client.post(f"/candidates/{candidate_id}/interviews")

    assert second.status_code == 200
    assert second.json()["meeting_id"] != first["meeting_id"]


async def test_cancelling_a_scheduled_session_frees_the_candidate(client):
    candidate_id = await make_candidate()
    first = client.post(f"/candidates/{candidate_id}/interviews").json()

    cancelled = client.delete(f"/interviews/{first['interview_id']}")
    assert cancelled.status_code == 200

    assert client.post(f"/candidates/{candidate_id}/interviews").status_code == 200


async def test_a_live_interview_cannot_be_cancelled(client):
    """Someone is mid-sentence in that room; a stray click must not cut them off."""
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.status = db.InterviewStatus.IN_PROGRESS
        await session.commit()

    response = client.delete(f"/interviews/{created['interview_id']}")

    assert response.status_code == 409
    assert "cannot be cancelled" in response.json()["detail"]


async def test_a_completed_interview_cannot_be_cancelled(client):
    """It is a record of something that happened. Records are not cancelled."""
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        await session.commit()

    assert client.delete(f"/interviews/{created['interview_id']}").status_code == 409


async def test_unknown_interview_returns_404(client):
    assert client.get("/interviews/int_nope").status_code == 404


# -- consent -----------------------------------------------------------------


async def test_no_bot_starts_without_consent(client):
    """There must be no path where someone is recorded before agreeing to be."""
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    response = client.post(
        "/interviews/join",
        json={
            "meeting_id": created["meeting_id"],
            "password": created["password"],
            "consent": False,
        },
    )

    assert response.status_code == 400
    assert "recording notice" in response.json()["detail"]
    assert client.spawned == []


async def test_consent_is_timestamped_against_the_interview(client):
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    client.post(
        "/interviews/join",
        json={
            "meeting_id": created["meeting_id"],
            "password": created["password"],
            "consent": True,
        },
    )

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        assert interview.consent_accepted_at is not None


async def test_frontend_lives_outside_the_python_package(client):
    """Both UIs are their own thing at the repo root, not files inside the
    backend package — and the Docker image mirrors that layout, so a path that
    works locally works in production too."""
    from app.main import FRONTEND_DIR

    assert FRONTEND_DIR.name == "frontend"
    assert (FRONTEND_DIR / "candidate" / "index.html").exists()
    assert (FRONTEND_DIR / "admin" / "index.html").exists()
    assert (FRONTEND_DIR / "admin" / "admin.js").exists()


async def test_the_join_page_is_served(client):
    """Candidates stay on our domain and never see a Daily URL."""
    response = client.get("/join")

    assert response.status_code == 200
    body = response.text
    assert "AI interviewer" in body
    assert "recorded and transcribed" in body
    # The consent box must not be pre-ticked.
    assert 'type="checkbox" checked' not in body
    assert "daily.co" not in body.lower()
    # Voice only: the candidate is told their camera is not used.
    assert "voice only" in body.lower()


async def test_the_bot_is_named_consistently_everywhere(client):
    """The name reaches the candidate three ways — the spoken introduction, the
    participant list, and this page. A bot that calls itself one thing and
    appears as another is unsettling when someone is already nervous."""
    from bot.brain.prompting import render_section_prompt
    from bot.blueprint_source import load_blueprint
    from bot.brain.state import build_sections
    from bot.run_bot import BOT_NAME as call_name
    from shared.branding import BOT_NAME

    page = client.get("/join").text
    assert BOT_NAME in page
    assert "{{" not in page  # every placeholder substituted

    assert call_name == BOT_NAME

    blueprint = load_blueprint()
    opening = render_section_prompt(
        blueprint=blueprint,
        section=build_sections(blueprint)[0],
        carried_claims=[],
        unprobed_contradictions=[],
        remaining_seconds=120,
        is_last_competency=False,
        time_of_day="evening",
    )
    assert BOT_NAME in opening


async def test_the_call_ui_is_ours_not_daily_prebuilt(client):
    """Prebuilt gives a meeting app — participant grid, screen share, Daily
    branding. A candidate should see an interview, not a conferencing tool."""
    body = client.get("/join").text

    assert "createCallObject" in body
    assert "createFrame" not in body
    # No embedded frame. (The SDK's global is named `DailyIframe` even for call
    # objects, so checking for the substring "iframe" would be meaningless.)
    assert "<iframe" not in body.lower()
    # We own media playback with a call object; without this the bot is silent.
    assert "track-started" in body


# -- deployment safety -------------------------------------------------------


def test_deployed_instance_refuses_to_start_without_a_database(monkeypatch):
    """Railway does not share a Postgres service's DATABASE_URL automatically.

    Forget the reference variable and the SQLite fallback would engage
    silently — green health checks, working interviews, and every transcript
    written to a filesystem wiped by the next deploy. Loud failure is the only
    safe behaviour.
    """
    from app.db import DatabaseNotConfigured, database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")

    with pytest.raises(DatabaseNotConfigured, match="Postgres.DATABASE_URL"):
        database_url()


def test_local_development_still_falls_back_to_sqlite(monkeypatch):
    """The convenience is fine on a laptop, where nothing is lost."""
    from app.db import database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(key, raising=False)

    assert database_url().startswith("sqlite+aiosqlite")


def test_railway_postgres_scheme_is_rewritten_for_asyncpg(monkeypatch):
    """Railway hands out `postgresql://`; the async engine needs the driver named."""
    from app.db import database_url

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host:5432/railway")
    assert database_url() == "postgresql+asyncpg://user:pw@host:5432/railway"

    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host:5432/railway")
    assert database_url() == "postgresql+asyncpg://user:pw@host:5432/railway"


# -- employer panel ----------------------------------------------------------


async def test_admin_panel_is_served_with_branding(client):
    response = client.get("/admin")

    assert response.status_code == 200
    assert "Mitra" in response.text
    assert "{{" not in response.text  # every placeholder substituted


async def test_admin_script_is_served_as_javascript(client):
    response = client.get("/admin/admin.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "{{" not in response.text


async def test_jobs_and_candidates_can_be_listed(client):
    """The panel needs these; nothing else did, so they did not exist."""
    candidate_id = await make_candidate()

    jobs = client.get("/jobs").json()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "job_1"

    candidates = client.get("/jobs/job_1/candidates").json()
    assert [c["candidate_id"] for c in candidates] == [candidate_id]
    assert candidates[0]["blueprint_status"] == "ready"


async def test_candidate_listing_surfaces_generation_failures(client):
    """A blueprint that failed must be visible in the list, not silently absent."""
    from app import db

    await make_candidate()
    async with db.get_sessionmaker()() as session:
        session.add(
            db.Candidate(
                id="cand_broken",
                job_id="job_1",
                cv_text="cv",
                blueprint_status=db.BlueprintStatus.FAILED,
                blueprint_error="model unavailable",
            )
        )
        await session.commit()

    rows = client.get("/jobs/job_1/candidates").json()
    broken = next(r for r in rows if r["candidate_id"] == "cand_broken")
    assert broken["blueprint_status"] == "failed"
    assert broken["blueprint_error"] == "model unavailable"


# -- admin authentication ----------------------------------------------------


async def test_admin_routes_are_closed_without_a_session(client):
    """The panel exposes every JD, CV and transcript in the database. On a
    public URL that cannot be open to anyone who guesses the path."""
    client.cookies.clear()

    for path in ("/jobs", "/jobs/job_1", "/candidates/cand_x"):
        assert client.get(path).status_code == 401, path


async def test_candidate_join_stays_public(client):
    """Candidates have no account. Their credential is the meeting password."""
    client.cookies.clear()

    response = client.post(
        "/interviews/join",
        json={"meeting_id": "111-222-333", "password": "nope", "consent": True},
    )
    # 401 for wrong credentials, not for being signed out of the admin panel.
    assert "meeting ID and password" in response.json()["detail"]


async def test_wrong_admin_password_is_refused(client):
    client.cookies.clear()
    assert client.post("/admin/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/jobs").status_code == 401


async def test_signing_out_ends_the_session(client):
    assert client.get("/jobs").status_code == 200
    client.post("/admin/logout")
    assert client.get("/jobs").status_code == 401


def test_login_cookie_is_not_secure_over_plain_http(monkeypatch):
    """Hardcoding secure=True meant the browser never sent the cookie back over
    HTTP, so login succeeded and every following request 401'd — on localhost."""
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    from app.main import app as fresh

    with TestClient(fresh) as c:
        response = c.post("/admin/login", json={"password": "pw"})
        assert "Secure" not in response.headers.get("set-cookie", "")
        assert "HttpOnly" in response.headers["set-cookie"]


async def test_deleting_a_profile_removes_everything_under_it(client):
    """Cascades to CVs, plans and transcripts. There is no undo."""
    from app import db

    candidate_id = await make_candidate()
    client.post(f"/candidates/{candidate_id}/interviews")

    assert client.delete("/jobs/job_1").status_code == 200
    assert client.get("/jobs").json() == []
    assert client.get(f"/candidates/{candidate_id}").status_code == 404

    async with db.get_sessionmaker()() as session:
        from sqlalchemy import select

        rows = (await session.scalars(select(db.Interview))).all()
        assert rows == []


async def test_deleting_an_unknown_profile_is_404(client):
    assert client.delete("/jobs/job_nope").status_code == 404


#: Everything a signed-out visitor is allowed to reach. Anything not on this
#: list must be behind the admin login — the database holds job descriptions,
#: CVs and interview transcripts, all of it personal data on a public URL.
PUBLIC_ROUTES = {
    ("POST", "/interviews/join"),  # candidate's own credential is the password
    ("GET", "/join"),  # the candidate join page itself
    ("GET", "/health"),
    ("POST", "/admin/login"),
    ("POST", "/admin/logout"),
    ("GET", "/admin/session"),  # answers "am I signed in?"; leaks nothing
    ("GET", "/admin"),  # the login screen has to render before you can log in
    ("GET", "/admin/admin.js"),
    ("GET", "/"),
}


def test_every_route_is_guarded_unless_it_is_deliberately_public():
    """A sweep, not a spot check.

    Guards were added route by route, which is exactly the pattern where a new
    endpoint gets merged without one. This fails the moment that happens.
    """
    from fastapi.routing import APIRoute

    from app.auth import require_admin
    from app.main import app

    unguarded = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in PUBLIC_ROUTES:
                continue
            if not any(d.call is require_admin for d in route.dependant.dependencies):
                unguarded.append(f"{method} {route.path}")

    assert not unguarded, (
        "These routes are reachable without signing in. Either guard them with "
        f"Depends(require_admin) or add them to PUBLIC_ROUTES: {sorted(unguarded)}"
    )


async def test_transcripts_are_not_readable_signed_out(client):
    """The most sensitive read in the system: a candidate's full transcript."""
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews")
    interview_id = created.json()["interview_id"]

    client.cookies.clear()
    assert client.get(f"/interviews/{interview_id}").status_code == 401
    assert client.get(f"/candidates/{candidate_id}/interviews").status_code == 401


async def test_starting_an_interview_requires_signing_in(client):
    """It mints a paid Daily room. An open endpoint is a billable one."""
    candidate_id = await make_candidate()
    client.cookies.clear()

    assert client.post(f"/candidates/{candidate_id}/interviews").status_code == 401


# -- feedback ----------------------------------------------------------------


async def test_feedback_can_be_retried_for_a_completed_interview(client, monkeypatch):
    """Scoring normally happens by itself. This is the path for when the bot
    process was killed mid-scoring, or the model call failed."""
    from app import db, interviews

    scored = []

    async def fake_generate(interview_id):
        scored.append(interview_id)

    monkeypatch.setattr("feedback.run.generate_feedback", fake_generate)

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        interview.feedback_status = db.FeedbackStatus.FAILED
        interview.feedback_error = "model unavailable"
        await session.commit()

    response = client.post(f"/interviews/{created['interview_id']}/feedback")

    assert response.status_code == 200
    assert scored == [created["interview_id"]]
    # The previous failure is cleared so the panel does not keep showing it.
    view = client.get(f"/interviews/{created['interview_id']}").json()
    assert view["feedback_error"] is None


async def test_an_unfinished_interview_cannot_be_scored(client):
    """There is no complete transcript yet, and scoring a partial one would
    judge someone on half a conversation."""
    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()

    response = client.post(f"/interviews/{created['interview_id']}/feedback")

    assert response.status_code == 409
    assert "completed" in response.json()["detail"]


async def test_scoring_is_not_started_twice_concurrently(client):
    from app import db

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        interview.feedback_status = db.FeedbackStatus.GENERATING
        await session.commit()

    response = client.post(f"/interviews/{created['interview_id']}/feedback")

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


async def test_a_successful_report_can_be_regenerated(client, monkeypatch):
    """Not only a retry for failures — rebuilding a good report from the same
    transcript is a normal thing to want after the prompt improves."""
    from app import db

    scored = []

    async def fake_generate(interview_id):
        scored.append(interview_id)

    monkeypatch.setattr("feedback.run.generate_feedback", fake_generate)

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        interview.feedback_status = db.FeedbackStatus.READY
        interview.feedback_report = {"summary": "the old one"}
        await session.commit()

    response = client.post(f"/interviews/{created['interview_id']}/feedback")

    assert response.status_code == 200
    assert scored == [created["interview_id"]]


async def test_regenerating_scores_the_stored_transcript_not_a_new_one(client, monkeypatch):
    """The transcript is written once when the interview ends. Regenerating
    re-reads it; it cannot reach a live or partial conversation."""
    from app import db
    from shared.contracts import Speaker, Transcript, TranscriptTurn

    seen = {}

    async def fake_generate(interview_id):
        async with db.get_sessionmaker()() as session:
            row = await session.get(db.Interview, interview_id)
            seen["turns"] = len(row.transcript["turns"])
            seen["status"] = row.status

    monkeypatch.setattr("feedback.run.generate_feedback", fake_generate)

    candidate_id = await make_candidate()
    created = client.post(f"/candidates/{candidate_id}/interviews").json()
    stored = Transcript(
        interview_id=created["interview_id"],
        turns=[
            TranscriptTurn(index=0, speaker=Speaker.INTERVIEWER, text="Hello.", at_seconds=0.0),
            TranscriptTurn(index=1, speaker=Speaker.CANDIDATE, text="Hi.", at_seconds=4.0),
        ],
        duration_seconds=10.0,
    )
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, created["interview_id"])
        interview.status = db.InterviewStatus.COMPLETED
        interview.transcript = stored.model_dump(mode="json")
        interview.feedback_status = db.FeedbackStatus.READY
        await session.commit()

    client.post(f"/interviews/{created['interview_id']}/feedback")

    assert seen == {"turns": 2, "status": db.InterviewStatus.COMPLETED}
