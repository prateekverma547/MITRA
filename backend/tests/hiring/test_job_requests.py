"""Job requests: a BU head describes a role until MITRA understands it, then
approves it to HR. The model is stubbed; what is under test is who can do
what, what is stored, and what each person can see at each step.
"""

from pathlib import Path

import pytest

from shared.contracts import Competency, EvaluationSpec
from tests.console_support import org_client, sign_in  # noqa: F401

SPEC = EvaluationSpec(
    role_title="AI Engineer",
    seniority="Mid",
    experience_expectation="2-3 years shipping ML to production",
    duration_minutes=40,
    competencies=[
        Competency(id="llm_apps", name="LLM application engineering", description="a", weight=0.6),
        Competency(id="evaluation", name="Evaluation", description="b", weight=0.4),
    ],
)
ASSUMED = ["Client communication matters (you did not say; taken from the JD)"]


class StubChat:
    """Asks questions until the employer says 'confirm', then finishes."""

    calls: list[dict] = []

    async def next_turn(self, *, jd_text, history, role_title=""):
        from blueprint.clarify import ClarificationReply

        StubChat.calls.append({"jd_text": jd_text, "role_title": role_title, "turns": len(history)})
        last = history[-1]["content"] if history else ""
        if last == "confirm":
            return ClarificationReply(reply="Ready.", done=True, spec=SPEC, inferred=ASSUMED)
        return ClarificationReply(reply=f"Question {len(history) // 2 + 1}?", done=False)


@pytest.fixture(autouse=True)
def stub_chat(monkeypatch):
    import hiring.jobs as jobs

    StubChat.calls = []
    monkeypatch.setattr(jobs, "_chat", lambda: StubChat())


def create(client, title="Prompt Engineer", file=None):
    files = {"file": file} if file else None
    return client.post("/api/jobs", data={"title": title}, files=files)


def finish(client, job_id: str) -> dict:
    client.post(f"/api/jobs/{job_id}/chat", json={"message": "We build RAG for clients."})
    response = client.post(f"/api/jobs/{job_id}/chat", json={"message": "confirm"})
    assert response.status_code == 200, response.text
    return response.json()


# ---- creating ----------------------------------------------------------------------


async def test_a_bu_head_starts_a_request_with_only_a_title(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    response = create(org_client)
    assert response.status_code == 201
    job = response.json()
    assert job["request_status"] == "draft"
    assert job["bu_name"] == "AI"
    assert job["has_jd"] is False
    assert job["chat"] == [{"from": "mitra", "text": "Question 1?"}]
    assert job["can_chat"] and not job["can_approve"]
    assert StubChat.calls[0] == {"jd_text": "", "role_title": "Prompt Engineer", "turns": 0}


async def test_an_uploaded_jd_reaches_the_chat(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    text = (Path(__file__).resolve().parents[1] / "fixtures" / "documents" / "JD-C-senior-aiml-engineer.md").read_bytes()
    jd = ("jd.md", text, "text/markdown")
    job = create(org_client, file=jd).json()
    assert job["has_jd"] and job["source_filename"] == "jd.md"
    assert "real-time voice agents" in StubChat.calls[0]["jd_text"]


async def test_a_title_is_required(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    assert create(org_client, title="   ").status_code == 422


async def test_an_unreadable_jd_is_refused_with_a_reason(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    response = create(org_client, file=("jd.exe", b"MZ\x00\x00", "application/octet-stream"))
    assert response.status_code == 400
    assert response.json()["detail"]


async def test_only_bu_heads_raise_job_requests(org_client):
    for email in ("hr@mitra.local", "admin@mitra.local"):
        sign_in(org_client, email)
        assert create(org_client).status_code == 403


# ---- describing and approving ----------------------------------------------------------


async def test_confirming_the_summary_stores_the_understanding_and_its_assumptions(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    job = finish(org_client, job_id)

    assert job["spec_status"] == "ready"
    assert job["understanding"]["role_title"] == "AI Engineer"
    assert job["inferred"] == ASSUMED
    assert job["chat"][-2:] == [{"from": "you", "text": "confirm"}, {"from": "mitra", "text": "Ready."}]
    assert job["can_approve"] and job["can_edit"] and not job["can_chat"]

    # Opened again later, the assumptions are still there.
    assert org_client.get(f"/api/jobs/{job_id}").json()["inferred"] == ASSUMED


async def test_hr_sees_a_request_only_once_it_is_approved(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    finish(org_client, job_id)

    sign_in(org_client, "hr@mitra.local")
    assert org_client.get(f"/api/jobs/{job_id}").status_code == 404
    assert job_id not in [j["id"] for j in org_client.get("/api/jobs").json()]

    sign_in(org_client, "ai.head@mitra.local")
    approved = org_client.post(f"/api/jobs/{job_id}/approve")
    assert approved.status_code == 200 and approved.json()["request_status"] == "open"

    sign_in(org_client, "hr@mitra.local")
    seen = org_client.get(f"/api/jobs/{job_id}")
    assert seen.status_code == 200
    assert not (seen.json()["can_edit"] or seen.json()["can_approve"] or seen.json()["can_chat"])


async def test_an_org_admin_sees_drafts_too(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    sign_in(org_client, "admin@mitra.local")
    assert org_client.get(f"/api/jobs/{job_id}").status_code == 200


async def test_approval_needs_a_finished_understanding_and_happens_once(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    assert org_client.post(f"/api/jobs/{job_id}/approve").status_code == 409
    finish(org_client, job_id)
    assert org_client.post(f"/api/jobs/{job_id}/approve").status_code == 200
    assert org_client.post(f"/api/jobs/{job_id}/approve").status_code == 409


async def test_another_bu_head_cannot_see_or_touch_the_request(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    sign_in(org_client, "finance.head@mitra.local")
    assert org_client.get(f"/api/jobs/{job_id}").status_code == 404
    assert org_client.post(f"/api/jobs/{job_id}/chat", json={"message": "hi"}).status_code == 404
    assert org_client.post(f"/api/jobs/{job_id}/approve").status_code == 404


async def test_chatting_after_the_summary_needs_edit_first(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    finish(org_client, job_id)
    response = org_client.post(f"/api/jobs/{job_id}/chat", json={"message": "one more thing"})
    assert response.status_code == 409
    assert "Edit" in response.json()["detail"]


async def test_an_empty_reply_is_refused_without_calling_the_model(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    calls = len(StubChat.calls)
    assert org_client.post(f"/api/jobs/{job_id}/chat", json={"message": "  "}).status_code == 422
    assert len(StubChat.calls) == calls


# ---- editing ----------------------------------------------------------------------------


async def test_edit_reopens_the_same_conversation_and_a_revision_bumps_the_version(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    finish(org_client, job_id)
    org_client.post(f"/api/jobs/{job_id}/approve")

    reopened = org_client.post(f"/api/jobs/{job_id}/edit").json()
    assert reopened["spec_status"] == "awaiting_clarification"
    assert reopened["can_chat"] and reopened["understanding"] is None
    assert reopened["request_status"] == "open"  # still with HR while it is revised

    turns_before = len(reopened["chat"])
    revised = org_client.post(f"/api/jobs/{job_id}/chat", json={"message": "confirm"}).json()
    assert revised["spec_version"] == 2
    assert len(revised["chat"]) == turns_before + 2
    assert revised["propagation"] == {"regenerated": [], "skipped_refined": [], "skipped_interviewed": []}


async def test_edit_needs_a_finished_understanding(org_client):
    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    assert org_client.post(f"/api/jobs/{job_id}/edit").status_code == 409


async def test_each_step_is_audited(org_client):
    from sqlalchemy import select

    from app import db
    from hiring.models import AuditEvent

    sign_in(org_client, "ai.head@mitra.local")
    job_id = create(org_client).json()["id"]
    finish(org_client, job_id)
    org_client.post(f"/api/jobs/{job_id}/approve")
    org_client.post(f"/api/jobs/{job_id}/edit")

    async with db.get_sessionmaker()() as session:
        actions = [
            e.action
            for e in (await session.execute(select(AuditEvent).where(AuditEvent.subject_id == job_id))).scalars()
        ]
    assert actions == [
        "job_request_created", "job_request_understood", "job_request_approved", "job_request_edit_started",
    ]
