"""CV screening: what the scorer may see, what counts as evidence, and the
upload -> proceed -> screened -> gate flow. The model is stubbed throughout;
what is under test is everything the code decides around it.
"""

from pathlib import Path

import pytest

from screening.fit import Screener, ScreeningError, build_fit, verify_quote
from screening.redact import redact
from shared.contracts import Competency, EvaluationSpec
from tests.console_support import org_client, sign_in  # noqa: F401

DOCUMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "documents"
CV = (DOCUMENTS / "cv_karandeep_aiml.txt").read_text()

SPEC = EvaluationSpec(
    role_title="AI Engineer",
    seniority="Mid",
    experience_expectation="3+ years shipping ML",
    duration_minutes=40,
    competencies=[
        Competency(id="voice", name="Real-time and voice", description="a", weight=0.5),
        Competency(id="eval", name="Evaluation", description="b", weight=0.3),
        Competency(id="client", name="Client communication", description="c", weight=0.2),
    ],
)


# ---- redaction ---------------------------------------------------------------------------


def test_identity_is_removed_and_the_work_is_kept():
    cv = (
        "Priya Sharma\nAI Engineer\npriya.sharma@gmail.com | +91 98765 43210 | linkedin.com/in/priya\n"
        "Date of Birth: 01/02/1996\nGender: Female\n"
        "EDUCATION\nB.Tech, Indian Institute of Technology Delhi, 2018\n"
        "EXPERIENCE\nInfosys, 2018 to present. Priya built a RAG system serving 2,000 analysts.\n"
    )
    result = redact(cv, candidate_name="Priya Sharma")
    for gone in ("Priya", "Sharma", "gmail", "98765", "linkedin", "1996", "Female", "Indian Institute of Technology"):
        assert gone not in result.text, gone
    for kept in ("Infosys", "RAG system serving 2,000 analysts", "B.Tech", "2018"):
        assert kept in result.text, kept
    assert set(result.removed) >= {"name", "email", "phone", "links", "personal details", "institution"}


def test_the_same_cv_under_another_name_and_gender_reads_identically():
    """The fairness property itself: what the scorer sees cannot depend on who
    the candidate is."""
    template = "{name}\nGender: {gender}\nBuilt six production voice agents on LiveKit.\n{name} led the work.\n"
    a = redact(template.format(name="Rahul Verma", gender="Male"), candidate_name="Rahul Verma")
    b = redact(template.format(name="Fatima Khan", gender="Female"), candidate_name="Fatima Khan")
    assert a.text == b.text


def test_a_name_shaped_first_line_is_removed_even_if_hr_typed_a_nickname():
    result = redact("KARANDEEP SALUJA\nAI/ML Engineer\n", candidate_name="KD")
    assert "KARANDEEP" not in result.text


# ---- what counts as evidence ----------------------------------------------------------------


def test_a_quote_must_really_be_in_the_cv():
    norm = __import__("screening.fit", fromlist=["_normalise"])._normalise(CV)
    assert verify_quote("Built six production voice agents on LiveKit Agents with OpenAI Realtime", norm)
    assert verify_quote("built six production voice agents, on LiveKit agents", norm)  # case and punctuation
    assert verify_quote("Built six production voice agents ... deployed on a shared Ubuntu VM", norm)
    assert not verify_quote("Built twelve production voice agents on LiveKit", norm)  # invented
    assert not verify_quote("LiveKit", norm)  # a keyword is not evidence


def test_an_invented_quote_turns_a_rating_into_no_evidence():
    payload = {
        "ratings": [
            {"competency_id": "voice", "rating": 4, "evidence": ["Built six production voice agents on LiveKit Agents"]},
            {"competency_id": "eval", "rating": 4, "evidence": ["Designed the evaluation suite used by every team"]},
            {"competency_id": "client", "rating": 9, "evidence": []},
        ],
        "summary": "s",
    }
    fit = build_fit(payload, spec=SPEC, cv_text=CV, candidate_id="c", job_id="j", removed=["name"], model="m")
    by_id = {r.competency_id: r for r in fit.ratings}
    assert by_id["voice"].rating == 4
    assert by_id["eval"].rating is None and "could not be found" in by_id["eval"].reasoning
    assert by_id["client"].rating is None  # out of range is not a rating
    assert fit.score_percent == 100.0 and fit.assessed_weight_percent == 50.0


def test_a_competency_the_model_skipped_is_no_evidence_not_zero():
    fit = build_fit({"ratings": []}, spec=SPEC, cv_text=CV, candidate_id="c", job_id="j", removed=[], model="m")
    assert all(r.rating is None for r in fit.ratings)
    assert (fit.score_percent, fit.assessed_weight_percent) == (0.0, 0.0)


def test_the_cut_off_is_a_setting(monkeypatch):
    monkeypatch.setenv("SCREENING_THRESHOLD_PERCENT", "65")
    fit = build_fit({"ratings": []}, spec=SPEC, cv_text=CV, candidate_id="c", job_id="j", removed=[], model="m")
    assert fit.threshold_percent == 65.0


# ---- HR questions ---------------------------------------------------------------------------


async def test_the_hr_questions_are_the_pre_screening_template_in_order(monkeypatch):
    """Product owner, 2026-11-20: the same ten, in order. Code owns the fixed wording;
    the model writes the four that come from the CV, and a general question stands in
    for any it leaves empty."""
    from screening.fit import HR_TEMPLATE

    screener = Screener(api_key="sk-test", model="gpt-4.1")
    answers = iter([
        {"hr2": {"employer": "Acme", "since": "2024-05"},
         "hr6": "Where are you based? You are hybrid now, so are you open to working fully from the office?",
         "hr7": "Your CV covers NLP and MLOps. Which one do you want your next role to focus on?", "hr8": ""},
        {},
    ])

    async def fake_json(system, user, **_):
        return next(answers), None, 0.0

    async def no_record(**_):
        return None

    monkeypatch.setattr(screener, "_json", fake_json)
    monkeypatch.setattr("hiring.usage.record", no_record)
    fit = build_fit({"ratings": []}, spec=SPEC, cv_text=CV, candidate_id="c", job_id="j", removed=[], model="m")
    questions = (await screener.hr_questions(candidate_id="c", job_id="j", spec=SPEC, jd_text="", redacted_cv=CV,
                                             fit=fit)).questions
    assert [q.id for q in questions] == [f"hr{i}" for i in range(1, 11)]
    texts = [q.text for q in questions]
    assert texts[0] == "Walk me through your profile in two minutes."
    assert texts[1].startswith("You have been at Acme for about ") and texts[1].endswith(
        "Why are you looking for a change now?")
    assert texts[2] == "What is your current CTC, and how is it split between fixed and variable?"
    assert texts[3] == "What is your expected CTC?"
    assert texts[4].startswith("What is your notice period?")
    assert texts[7] == HR_TEMPLATE[7][3]  # nothing in the dates to clarify: the general question
    assert texts[8] == "Do you have other offers or interviews in progress?"
    assert texts[9] == "What do you know about our company, and why does this role interest you?"
    assert all(q.why for q in questions)

    # The model wrote nothing usable: every CV question falls back, still ten, still in order.
    fallback = (await screener.hr_questions(candidate_id="c", job_id="j", spec=SPEC, jd_text="", redacted_cv=CV,
                                            fit=fit)).questions
    assert [q.text for q in fallback][1] == "Why are you looking for a change now?"
    assert len(fallback) == 10


def test_tenure_is_counted_in_code_not_by_the_model():
    from datetime import date

    from screening.fit import tenure_words

    today = date(2026, 11, 20)
    assert tenure_words("2024-05", today) == "about two and a half years"
    assert tenure_words("2026-05", today) == "about six months"
    assert tenure_words("2025-11", today) == "about one year"
    assert tenure_words("not a date", today) is None and tenure_words("2026-12", today) is None


# ---- the flow, through the API -------------------------------------------------------------


class StubScreener:
    """Rates from canned answers using real lines of the fixture CV."""

    ratings = {"voice": 4, "eval": 1, "client": None}
    fail = False

    async def analyse_fit(self, *, candidate_id, job_id, spec, jd_text, cv_text, candidate_name):
        if StubScreener.fail:
            raise ScreeningError("The screening model call failed: timeout")
        red = redact(cv_text, candidate_name=candidate_name)
        quote = {"voice": "Built six production voice agents on LiveKit Agents with OpenAI Realtime",
                 "eval": "Reached 97.08 percent decision accuracy against the GPT-4o reference ceiling",
                 "client": "Recommended the client fix the labelling process rather than tune the number upward"}
        payload = {"ratings": [
            {"competency_id": cid, "rating": r, "evidence": [quote[cid]] if r else []}
            for cid, r in StubScreener.ratings.items()
        ], "summary": "Strong on voice."}
        return build_fit(payload, spec=spec, cv_text=red.text, candidate_id=candidate_id, job_id=job_id,
                         removed=red.removed, model="stub"), red.text

    async def hr_questions(self, *, candidate_id, job_id, spec, jd_text, redacted_cv, fit):
        from shared.screening import HRQuestion, HRQuestionSet

        return HRQuestionSet(candidate_id=candidate_id, job_id=job_id,
                             questions=[HRQuestion(id=f"hr{i}", text=f"Question {i}?", why="w") for i in range(1, 11)])


@pytest.fixture(autouse=True)
def stub_screener(monkeypatch):
    import hiring.candidates as candidates

    StubScreener.ratings = {"voice": 4, "eval": 1, "client": None}
    StubScreener.fail = False
    monkeypatch.setattr(candidates, "_screener", lambda: StubScreener())


async def ready_job(client, *, status="open", spec_status="ready") -> str:
    """Give the seeded AI Engineer job a finished understanding."""
    from app import db
    from app.db import Job

    job_id = client.demo.job_ids["AI Engineer"]
    async with db.get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        job.evaluation_spec = SPEC.model_dump(mode="json")
        job.spec_status = spec_status
        job.request_status = status
        await session.commit()
    return job_id


def upload(client, job_id, name="Karandeep Saluja", text=CV):
    return client.post(
        f"/api/jobs/{job_id}/candidates",
        data={"name": name},
        files={"file": ("cv.txt", text.encode(), "text/plain")},
    )


async def test_hr_uploads_proceeds_and_gets_an_evidenced_screening(org_client):
    job_id = await ready_job(org_client)
    sign_in(org_client, "hr@mitra.local")

    added = upload(org_client, job_id)
    assert added.status_code == 201, added.text
    candidate = added.json()
    assert candidate["stage"] == "new" and candidate["screening_status"] == "waiting"
    assert candidate["can_proceed"] and candidate["can_reupload"]
    assert "six production voice agents" in candidate["cv_text"]

    started = org_client.post(f"/api/candidates/{candidate['id']}/proceed")
    assert started.status_code == 202
    # TestClient runs background tasks before returning, so screening is done.
    done = org_client.get(f"/api/candidates/{candidate['id']}").json()
    assert done["screening_status"] == "ready"
    assert done["stage"] == "screened"
    assert done["score_percent"] == 71.9  # (0.5*4 + 0.3*1) / (4 * 0.8) = 2.3 / 3.2
    assert done["assessed_weight_percent"] == 80.0
    assert "name" in done["fit"]["identity_fields_removed"]
    assert len(done["hr_questions"]["questions"]) == 10
    assert done["next"] == ["ready_for_hr", "parked", "rejected"]

    history = org_client.get(f"/api/candidates/{candidate['id']}/decisions").json()["decisions"]
    assert history[0]["actor"] == "system" and history[0]["to"] == "screened"
    assert "71.9%" in history[0]["model_recommendation"]


async def test_below_the_cut_off_proceeding_needs_a_reason(org_client):
    StubScreener.ratings = {"voice": 1, "eval": 1, "client": None}
    job_id = await ready_job(org_client)
    sign_in(org_client, "hr@mitra.local")
    cid = upload(org_client, job_id).json()["id"]
    org_client.post(f"/api/candidates/{cid}/proceed")
    assert org_client.get(f"/api/candidates/{cid}").json()["below_threshold"]

    refused = org_client.post(f"/api/candidates/{cid}/move", json={"to": "ready_for_hr"})
    assert refused.status_code == 422
    allowed = org_client.post(
        f"/api/candidates/{cid}/move",
        json={"to": "ready_for_hr", "reason": "Strong referral; the CV undersells the voice work."},
    )
    assert allowed.status_code == 200
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["reason"].startswith("Strong referral")
    assert "below the 50% cut-off" in last["model_recommendation"]


async def test_reupload_replaces_the_cv_until_screening_starts(org_client):
    job_id = await ready_job(org_client)
    sign_in(org_client, "hr@mitra.local")
    cid = upload(org_client, job_id).json()["id"]

    replaced = org_client.post(
        f"/api/candidates/{cid}/cv", files={"file": ("cv2.txt", (CV + "\nExtra line about Kafka.").encode(), "text/plain")}
    )
    assert replaced.status_code == 200 and replaced.json()["source_filename"] == "cv2.txt"

    org_client.post(f"/api/candidates/{cid}/proceed")
    assert org_client.post(f"/api/candidates/{cid}/proceed").status_code == 409
    again = org_client.post(f"/api/candidates/{cid}/cv", files={"file": ("cv3.txt", CV.encode(), "text/plain")})
    assert again.status_code == 409


async def test_a_failed_screening_is_recorded_and_can_be_retried(org_client):
    StubScreener.fail = True
    job_id = await ready_job(org_client)
    sign_in(org_client, "hr@mitra.local")
    cid = upload(org_client, job_id).json()["id"]
    org_client.post(f"/api/candidates/{cid}/proceed")
    failed = org_client.get(f"/api/candidates/{cid}").json()
    assert failed["screening_status"] == "failed" and "timeout" in failed["screening_error"]
    assert failed["stage"] == "new" and failed["can_proceed"]

    StubScreener.fail = False
    org_client.post(f"/api/candidates/{cid}/proceed")
    assert org_client.get(f"/api/candidates/{cid}").json()["stage"] == "screened"


async def test_candidates_go_only_on_an_approved_finished_job(org_client):
    sign_in(org_client, "hr@mitra.local")
    job_id = await ready_job(org_client, spec_status="awaiting_clarification")
    assert upload(org_client, job_id).status_code == 409
    job_id = await ready_job(org_client, status="draft")
    assert upload(org_client, job_id).status_code == 404  # HR cannot see drafts


async def test_only_hr_adds_candidates_and_bu_heads_see_their_own(org_client):
    job_id = await ready_job(org_client)
    sign_in(org_client, "ai.head@mitra.local")
    assert upload(org_client, job_id).status_code == 403
    listed = org_client.get(f"/api/jobs/{job_id}/candidates").json()
    assert not listed["can_add"] and len(listed["candidates"]) == 1

    sign_in(org_client, "sales.head@mitra.local")
    assert org_client.get(f"/api/jobs/{job_id}/candidates").status_code == 404


async def test_a_candidate_needs_a_name_and_a_readable_cv(org_client):
    job_id = await ready_job(org_client)
    sign_in(org_client, "hr@mitra.local")
    assert upload(org_client, job_id, name="  ").status_code == 422
    assert upload(org_client, job_id, text="too short").status_code == 400


async def test_a_cv_that_says_too_little_cannot_pass_quietly(org_client):
    """Found live: a product manager's CV scored 50% for an LLM job on one vague
    line, judged on 10% of the weight. Too little evidence is treated at the gate
    like a low score: proceeding needs a reason."""
    StubScreener.ratings = {"voice": None, "eval": None, "client": 4}  # 4 on 20% of the weight
    job_id = await ready_job(org_client)
    sign_in(org_client, "hr@mitra.local")
    cid = upload(org_client, job_id).json()["id"]
    org_client.post(f"/api/candidates/{cid}/proceed")
    detail = org_client.get(f"/api/candidates/{cid}").json()
    assert detail["score_percent"] == 100.0 and detail["assessed_weight_percent"] == 20.0
    assert detail["enough_evidence"] is False and detail["below_threshold"] is True

    assert org_client.post(f"/api/candidates/{cid}/move", json={"to": "ready_for_hr"}).status_code == 422
    org_client.post(f"/api/candidates/{cid}/move", json={"to": "ready_for_hr", "reason": "Referred for the client side."})
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert "Too little evidence to judge" in last["model_recommendation"]
