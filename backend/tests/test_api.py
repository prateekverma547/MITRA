"""API tests for the employer panel — no LLM, no network.

The LLM stages are stubbed. What is under test is the wiring the DoD depends on:
uploads are parsed and stored, the clarification chat's spec is persisted, CV
upload schedules generation **at upload time**, and the blueprint lands in the
database with a status the panel can poll.
"""

import os

import pytest
from fastapi.testclient import TestClient

from shared.contracts import Competency, EvaluationSpec

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "documents")


def read(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


SPEC = EvaluationSpec(
    role_title="Senior Product Manager",
    seniority="Senior",
    experience_expectation="10+ years",
    duration_minutes=40,
    competencies=[
        Competency(id="alpha", name="Alpha", description="a", weight=0.6),
        Competency(id="beta", name="Beta", description="b", weight=0.4),
    ],
)


class StubChat:
    """Asks one question, then completes on the employer's reply."""

    def __init__(self, *args, **kwargs):
        pass

    async def next_turn(self, *, jd_text, history):
        from blueprint.clarify import ClarificationReply

        if not history:
            return ClarificationReply(reply="Which competency matters most?", done=False)
        return ClarificationReply(reply="Summary confirmed.", done=True, spec=SPEC)


class StubGenerator:
    def __init__(self, *args, **kwargs):
        pass

    async def generate(self, *, blueprint_id, spec, cv_text):
        from blueprint.generate import build_blueprint

        return build_blueprint(
            blueprint_id=blueprint_id,
            spec=spec,
            payload={
                "candidate_name": "Priya Raghavan",
                "candidate_summary": "Eleven years in payments.",
                "claims_to_verify": [{"claim": "Cut failures 38%.", "source": "cv"}],
                "suggested_opening": "Hello.",
                "interviewing_guidance": ["Probe specifics."],
                "competency_plans": [
                    {
                        "competency_id": c.id,
                        "name": c.name,
                        "target_depth": "deep",
                        "emphasis": 1.0,
                        "seed_questions": ["q1", "q2"],
                    }
                    for c in spec.competencies
                ],
            },
        )


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-test")
    monkeypatch.setenv("DAILY_API_KEY", "daily-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    from app import db, main

    await db.reset_engine()
    monkeypatch.setattr(main, "ClarificationChat", StubChat)
    monkeypatch.setattr(main, "BlueprintGenerator", StubGenerator)

    with TestClient(main.app) as test_client:
        # Every admin route is guarded now; these tests exercise what happens
        # behind the login, so they sign in once.
        test_client.post("/admin/login", json={"password": "test-admin-pw"})
        yield test_client

    await db.reset_engine()


async def complete_job(client) -> str:
    response = client.post(
        "/jobs", files={"file": ("jd.txt", read("jd_senior_pm.txt"), "text/plain")}
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]

    reply = client.post(f"/jobs/{job_id}/clarify", json={"message": "Prioritisation."})
    assert reply.status_code == 200, reply.text
    assert reply.json()["done"] is True
    return job_id


async def test_jd_upload_returns_a_first_question(client):
    response = client.post(
        "/jobs", files={"file": ("jd.txt", read("jd_senior_pm.txt"), "text/plain")}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["spec_status"] == "awaiting_clarification"
    assert body["first_question"]


async def test_unparseable_upload_is_rejected_with_a_clear_reason(client):
    response = client.post("/jobs", files={"file": ("jd.exe", b"x" * 500, "application/octet-stream")})

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


async def test_scanned_pdf_style_upload_is_rejected(client):
    """Too little extracted text means extraction failed, silently."""
    response = client.post("/jobs", files={"file": ("jd.txt", b"tiny", "text/plain")})

    assert response.status_code == 400
    assert "characters of text" in response.json()["detail"]


async def test_clarification_persists_the_spec_and_the_conversation(client):
    job_id = await complete_job(client)

    job = client.get(f"/jobs/{job_id}").json()
    assert job["spec_status"] == "ready"
    assert job["evaluation_spec"]["role_title"] == "Senior Product Manager"
    # Both sides of the conversation are kept.
    roles = [t["role"] for t in job["clarification"]]
    assert "employer" in roles and "assistant" in roles


async def test_cv_upload_before_the_spec_is_ready_is_refused(client):
    response = client.post(
        "/jobs", files={"file": ("jd.txt", read("jd_senior_pm.txt"), "text/plain")}
    )
    job_id = response.json()["job_id"]

    upload = client.post(
        f"/jobs/{job_id}/candidates",
        files={"file": ("cv.txt", read("cv_strong_pm.txt"), "text/plain")},
    )

    assert upload.status_code == 409
    assert "clarification" in upload.json()["detail"]


async def test_cv_upload_generates_and_stores_the_blueprint(client):
    """Generation is scheduled at upload time, not when the blueprint is read."""
    job_id = await complete_job(client)

    upload = client.post(
        f"/jobs/{job_id}/candidates",
        files={"file": ("cv.txt", read("cv_strong_pm.txt"), "text/plain")},
    )
    assert upload.status_code == 200
    candidate_id = upload.json()["candidate_id"]
    # The employer is not made to wait for the model.
    assert upload.json()["blueprint_status"] == "pending"

    # TestClient runs BackgroundTasks before returning, so by now it is done.
    candidate = client.get(f"/candidates/{candidate_id}").json()
    assert candidate["blueprint_status"] == "ready", candidate.get("error")

    blueprint = candidate["blueprint"]
    assert blueprint["candidate_name"] == "Priya Raghavan"
    assert blueprint["evaluation_spec"]["role_title"] == "Senior Product Manager"
    assert len(blueprint["competency_plans"]) == 2


async def test_stored_blueprint_drives_the_brain(client):
    """The DoD's real point: what comes out of the DB must run an interview."""
    from bot.brain.brain import InterviewBrain
    from shared.contracts import InterviewBlueprint

    job_id = await complete_job(client)
    upload = client.post(
        f"/jobs/{job_id}/candidates",
        files={"file": ("cv.txt", read("cv_strong_pm.txt"), "text/plain")},
    )
    candidate_id = upload.json()["candidate_id"]
    stored = client.get(f"/candidates/{candidate_id}").json()["blueprint"]

    brain = InterviewBrain(InterviewBlueprint.model_validate(stored))

    assert [s.id for s in brain.sections] == ["opening", "alpha", "beta", "closing"]
    assert brain.plan_turn().system_instruction


async def test_generation_failure_is_recorded_not_swallowed(client, monkeypatch):
    from app import main

    class ExplodingGenerator:
        def __init__(self, *args, **kwargs):
            pass

        async def generate(self, **kwargs):
            raise RuntimeError("model unavailable")

    job_id = await complete_job(client)
    monkeypatch.setattr(main, "BlueprintGenerator", ExplodingGenerator)

    upload = client.post(
        f"/jobs/{job_id}/candidates",
        files={"file": ("cv.txt", read("cv_strong_pm.txt"), "text/plain")},
    )
    candidate_id = upload.json()["candidate_id"]

    candidate = client.get(f"/candidates/{candidate_id}").json()
    assert candidate["blueprint_status"] == "failed"
    assert "model unavailable" in candidate["error"]
    assert candidate["blueprint"] is None


async def test_unknown_ids_return_404(client):
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/candidates/nope").status_code == 404
