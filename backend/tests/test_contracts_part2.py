"""Contracts part 2: question ids, strengths, quote matches, failed_to_start, the
candidate app's config and its /interview mount.

Each addition is additive and optional. For each one: old data without the
field still loads, new data round-trips unchanged, and invalid values are
rejected.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.contracts import (
    FIRST_COMPETENCY_AREA,
    EvidenceQuote,
    FeedbackReport,
    InterviewBlueprint,
    ObservedStrength,
    QuoteMatch,
    Speaker,
    Transcript,
    TranscriptTurn,
    plan_question_id,
    question_ids_for,
)
from shared.interview_contracts import CandidateAppConfig, EndReason

BACKEND = Path(__file__).resolve().parents[1]
SHARED_FIXTURES = BACKEND / "shared" / "fixtures"
OLD_BLUEPRINT = BACKEND / "bot" / "fixtures" / "pm_senior.json"


# -- a. the numbered question bank ------------------------------------------------------


def test_an_old_blueprint_without_ids_gets_them_derived_on_load():
    raw = json.loads(OLD_BLUEPRINT.read_text())
    assert all("question_ids" not in p for p in raw["competency_plans"]), "fixture must be old data"
    bp = InterviewBlueprint.model_validate(raw)
    for index, plan in enumerate(bp.competency_plans):
        area = FIRST_COMPETENCY_AREA + index
        assert plan.question_ids == [f"Q{area}.{n}" for n in range(1, len(plan.seed_questions) + 1)]
    assert bp.competency_plans[0].numbered_questions()[0] == ("Q2.1", raw["competency_plans"][0]["seed_questions"][0])


def test_the_numbered_blueprint_fixture_round_trips_unchanged():
    raw = json.loads((SHARED_FIXTURES / "interview_blueprint.json").read_text())
    assert InterviewBlueprint.model_validate(raw).model_dump(mode="json") == raw


def test_generation_numbers_questions_in_code(monkeypatch):
    """build_blueprint is the one path for generate and refine; the model's
    payload has no ids, and the plans come back numbered."""
    from blueprint.generate import build_blueprint

    bp = InterviewBlueprint.model_validate(json.loads(OLD_BLUEPRINT.read_text()))
    payload = {
        "competency_plans": [
            {"competency_id": p.competency_id, "name": p.name, "target_depth": p.target_depth,
             "seed_questions": p.seed_questions, "emphasis": 1.0}
            for p in bp.competency_plans
        ],
        "suggested_opening": "Hello.",
    }
    built = build_blueprint(blueprint_id="b", spec=bp.evaluation_spec, payload=payload)
    assert [p.question_ids for p in built.competency_plans] == [
        question_ids_for(i, len(p.seed_questions)) for i, p in enumerate(built.competency_plans)
    ]


@pytest.mark.parametrize("ids", [["Q1.1", "Q1.2", "Q1.3", "Q1.4"], ["Q2.1"], ["2.1", "2.2", "2.3", "2.4"]])
def test_question_ids_not_numbered_by_the_code_are_rejected(ids):
    raw = json.loads(OLD_BLUEPRINT.read_text())
    raw["competency_plans"][0]["question_ids"] = ids
    with pytest.raises(ValidationError, match="numbered in code"):
        InterviewBlueprint.model_validate(raw)


def test_question_ids_use_the_realtime_prompts_area_numbers():
    from bot.realtime_prompt import _render_plan

    bp = InterviewBlueprint.model_validate(json.loads(OLD_BLUEPRINT.read_text()))
    plan_text = _render_plan(bp)
    first = bp.competency_plans[0]
    assert f"{FIRST_COMPETENCY_AREA}. {first.name}," in plan_text
    assert first.question_ids[0].startswith(f"Q{FIRST_COMPETENCY_AREA}.")
    with pytest.raises(ValueError):
        plan_question_id(0, 1)


# -- b. strengths_observed ----------------------------------------------------------------

REPORT = SHARED_FIXTURES / "feedback_report.json"


def test_the_feedback_report_fixture_round_trips_and_has_strengths():
    raw = json.loads(REPORT.read_text())
    report = FeedbackReport.model_validate(raw)
    assert report.model_dump(mode="json") == raw
    assert report.strengths_observed and all(s.evidence for s in report.strengths_observed)


def test_an_old_report_without_strengths_or_matches_still_loads():
    raw = json.loads(REPORT.read_text())
    raw.pop("strengths_observed")
    for score in raw["competency_scores"]:
        for quote in score["evidence"]:
            quote.pop("match")
    report = FeedbackReport.model_validate(raw)
    assert report.strengths_observed == []
    assert all(q.match is None for s in report.competency_scores for q in s.evidence)


def test_a_strength_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        ObservedStrength(competency_id="a", description="x", evidence=[])


def test_a_strength_naming_an_area_not_in_the_report_is_rejected():
    raw = json.loads(REPORT.read_text())
    raw["strengths_observed"][0]["competency_id"] = "not_an_area"
    with pytest.raises(ValidationError, match="not in this report"):
        FeedbackReport.model_validate(raw)


# -- c. how each quote matched ---------------------------------------------------------

TRANSCRIPT = Transcript(
    interview_id="i",
    turns=[
        TranscriptTurn(index=0, speaker=Speaker.INTERVIEWER, text="Tell me about it.", at_seconds=1),
        TranscriptTurn(index=1, speaker=Speaker.CANDIDATE, text="We cut latency by 40%, mostly caching.", at_seconds=5),
        TranscriptTurn(index=2, speaker=Speaker.CANDIDATE, text="I, uh, owned the rollout.", at_seconds=9),
    ],
)


def test_verification_records_exact_and_normalised_matches():
    from feedback.score import verify_quote

    exact = verify_quote("We cut latency by 40%", 1, TRANSCRIPT)
    assert exact.match is QuoteMatch.EXACT
    tidied = verify_quote("we cut latency by 40, mostly caching", 1, TRANSCRIPT)
    assert tidied.match is QuoteMatch.NORMALISED
    reanchored = verify_quote("owned the rollout", 1, TRANSCRIPT)
    assert (reanchored.turn_index, reanchored.match) == (2, QuoteMatch.EXACT)


def test_nothing_approximate_is_ever_kept():
    """APPROXIMATE is reserved. A dropped filler word is not a substring, so it
    is dropped, despite _normalise's docstring saying such a quote survives
    (P2 finding)."""
    from feedback.score import verify_quote

    assert verify_quote("I owned the rollout", 2, TRANSCRIPT) is None
    assert verify_quote("We reduced latency by 40%", 1, TRANSCRIPT) is None


def test_an_invalid_match_value_is_rejected():
    with pytest.raises(ValidationError):
        EvidenceQuote(text="x", turn_index=1, at_seconds=1, match="fuzzy")


def test_build_report_keeps_only_strengths_with_verified_quotes():
    from feedback.score import build_report

    bp = InterviewBlueprint.model_validate(json.loads(OLD_BLUEPRINT.read_text()))
    spec = bp.evaluation_spec
    area = spec.competencies[0].id
    payload = {
        "competency_scores": [],
        "strengths_observed": [
            {"competency_id": area, "description": "kept",
             "evidence": [{"turn_index": 1, "text": "We cut latency by 40%"},
                          {"turn_index": 1, "text": "an invented sentence"}]},
            {"competency_id": area, "description": "no evidence left",
             "evidence": [{"turn_index": 1, "text": "an invented sentence"}]},
            {"competency_id": area, "description": "no evidence given", "evidence": []},
            {"competency_id": "unknown", "description": "wrong area",
             "evidence": [{"turn_index": 1, "text": "We cut latency by 40%"}]},
        ],
        "summary": "s", "recommendation": "limited_evidence",
    }
    report = build_report(interview_id="i", blueprint_id="b", spec=spec, transcript=TRANSCRIPT, payload=payload)
    assert [s.description for s in report.strengths_observed] == ["kept"]
    assert [q.text for q in report.strengths_observed[0].evidence] == ["We cut latency by 40%"]
    assert report.strengths_observed[0].evidence[0].match is QuoteMatch.EXACT


# -- d. failed_to_start beside the failed status ----------------------------------------


async def test_a_start_up_failure_records_failed_to_start(interview_db):
    from app.db import Interview, InterviewStatus
    from bot.persistence import mark_failed_if_unfinished
    from tests.test_text_models import _seed

    iid = await _seed(interview_db, InterviewStatus.IN_PROGRESS)
    assert await mark_failed_if_unfinished(
        interview_id=iid, reason="could not start", ended_because=EndReason.FAILED_TO_START.value
    )
    async with interview_db.get_sessionmaker()() as s:
        iv = await s.get(Interview, iid)
        assert (str(iv.status), iv.session_metrics) == ("failed", {"ended_because": "failed_to_start"})


async def test_existing_session_metrics_are_never_overwritten(interview_db):
    from app.db import Interview, InterviewStatus
    from bot.persistence import mark_failed_if_unfinished
    from tests.test_text_models import _seed

    iid = await _seed(interview_db, InterviewStatus.IN_PROGRESS)
    async with interview_db.get_sessionmaker()() as s:
        (await s.get(Interview, iid)).session_metrics = {"ended_because": "candidate_dropped"}
        await s.commit()
    await mark_failed_if_unfinished(interview_id=iid, reason="x", ended_because="failed_to_start")
    async with interview_db.get_sessionmaker()() as s:
        assert (await s.get(Interview, iid)).session_metrics == {"ended_because": "candidate_dropped"}


def test_the_real_start_up_path_records_failed_to_start(tmp_path, monkeypatch):
    """run_bot.main, as in test_the_bot_marks_its_own_start_up_failure."""
    from app import db
    from app.db import Interview, InterviewStatus
    from bot import run_bot
    from tests.test_text_models import _seed

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/fts.db")

    async def setup():
        await db.reset_engine()
        await db.create_all()
        iid = await _seed(db, InterviewStatus.IN_PROGRESS)
        await db.reset_engine()
        return iid

    async def read(iid):
        async with db.get_sessionmaker()() as s:
            iv = await s.get(Interview, iid)
            result = (str(iv.status), iv.session_metrics)
        await db.reset_engine()
        return result

    iid = asyncio.run(setup())
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("DAILY_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["run_bot", "--room-url", "https://x.daily.co/r",
                                      "--interview-id", iid, "--blueprint-id", "not-a-candidate"])
    with pytest.raises(SystemExit):
        run_bot.main()
    assert asyncio.run(read(iid)) == ("failed", {"ended_because": EndReason.FAILED_TO_START.value})


@pytest.fixture
async def interview_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/p2.db")
    from app import db

    await db.reset_engine()
    await db.create_all()
    yield db
    await db.reset_engine()


# -- the candidate app: config and the /interview mount --------------------------------


def test_the_config_fixture_round_trips_and_bad_values_are_rejected():
    raw = json.loads((SHARED_FIXTURES / "candidate_app_config.json").read_text())
    assert CandidateAppConfig.model_validate(raw).model_dump(mode="json") == raw
    with pytest.raises(ValidationError):
        CandidateAppConfig(retention_days=0)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """The real app, with a stand-in build of the candidate app."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/mount.db")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>candidate app</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('app')")
    (dist / "favicon.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(main, "CANDIDATE_APP_DIST", dist)
    with TestClient(main.app) as client:
        yield client


def test_config_is_public_and_carries_the_retention_constant(app_client):
    from shared.contracts import RECORDING_RETENTION_DAYS

    r = app_client.get("/interview/config")
    assert r.status_code == 200
    assert r.json()["retention_days"] == RECORDING_RETENTION_DAYS


@pytest.mark.parametrize("env, expected", [("1", True), ("", False), ("false", False)])
def test_config_says_whether_interviews_are_recorded(app_client, monkeypatch, env, expected):
    """Task 19 item a: read from the switch the recorder reads, at request time."""
    from bot.config import recording_enabled

    monkeypatch.setenv("RECORDING_ENABLED", env)
    body = app_client.get("/interview/config").json()
    assert body["recording_enabled"] is expected is recording_enabled()


async def test_a_bot_that_cannot_be_launched_records_failed_to_start(tmp_path, monkeypatch):
    """Task 19 item b: the spawn failure in app/interviews.py, recorded like a start-up failure."""
    from fastapi.testclient import TestClient

    from tests.test_interviews import FakeRoom, blueprint_for

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/spawn.db")
    for key in ("OPENAI_API_KEY", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, interviews, main
    from app.db import BlueprintStatus, Candidate, Interview, Job

    await db.reset_engine()

    async def fake_room(**kw):
        return FakeRoom()

    async def fake_token(**kw):
        return "tok"

    async def cannot_launch(*args, **kwargs):
        raise OSError("no such file: python")

    monkeypatch.setattr(interviews, "create_room", fake_room)
    monkeypatch.setattr(interviews, "create_meeting_token", fake_token)
    monkeypatch.setattr(interviews.asyncio, "create_subprocess_exec", cannot_launch)
    with TestClient(main.app) as client:
        async def seed():
            async with db.get_sessionmaker()() as s:
                s.add(Job(id="j1", title="PM", jd_text="jd"))
                s.add(Candidate(id="c1", job_id="j1", name="A", cv_text="cv",
                                blueprint=json.loads(blueprint_for("c1").model_dump_json()),
                                blueprint_status=BlueprintStatus.READY))
                await s.commit()

        client.portal.call(seed)
        client.post("/admin/login", json={"password": "pw"})
        booked = client.post("/candidates/c1/interviews").json()
        joined = client.post("/interviews/join", json={
            "meeting_id": booked["meeting_id"], "password": booked["password"], "consent": True})
        assert joined.status_code == 500
        # The same refusal shape and code as every other (B-121, C-174).
        assert joined.json() == {"detail": "The interviewer could not be started.", "reason": "failed_to_start",
                                 "retry_after_seconds": None}

        async def read():
            async with db.get_sessionmaker()() as s:
                iv = await s.get(Interview, booked["interview_id"])
                return str(iv.status), iv.failure_reason, iv.session_metrics

        status, reason, metrics = client.portal.call(read)
    await db.reset_engine()
    assert status == "failed" and reason.startswith("Bot failed to start")
    assert metrics == {"ended_because": EndReason.FAILED_TO_START.value}


@pytest.mark.parametrize("path", ["/interview/", "/interview/device-check", "/interview/a/b/c"])
def test_client_routes_get_the_app(app_client, path):
    r = app_client.get(path)
    assert r.status_code == 200 and "candidate app" in r.text
    assert r.headers["cache-control"].startswith("no-cache")


def test_real_files_are_served_as_files(app_client):
    r = app_client.get("/interview/assets/index-abc123.js")
    assert r.status_code == 200 and r.text == "console.log('app')"
    assert "immutable" in r.headers["cache-control"]
    assert app_client.get("/interview/favicon.png").content == b"\x89PNG"


@pytest.mark.parametrize("path", ["/interview/assets/missing.js", "/interview/nothing.png"])
def test_a_missing_file_is_a_404_not_the_app(app_client, path):
    assert app_client.get(path).status_code == 404


def test_nothing_outside_the_build_is_served(app_client):
    for path in ("/interview/../../app/main.py", "/interview/%2e%2e/%2e%2e/app/main.py"):
        r = app_client.get(path)
        assert "FastAPI" not in r.text and "import" not in r.text


def test_the_bare_path_redirects_to_the_slash(app_client):
    r = app_client.get("/interview", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (308, "/interview/")


def test_join_admin_and_the_api_are_unchanged(app_client):
    assert "<html" in app_client.get("/join").text.lower()
    assert app_client.get("/admin").status_code == 200
    assert app_client.get("/health").json() == {"status": "ok"}
    assert app_client.get("/interviews/not-real").status_code == 401  # still guarded


def test_an_unbuilt_app_says_so(app_client, monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "CANDIDATE_APP_DIST", tmp_path / "nowhere")
    assert app_client.get("/interview/").status_code == 503
