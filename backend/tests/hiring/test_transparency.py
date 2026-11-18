"""Transparency: admins only, honest about followed and differed, searchable, and
a register that agrees with the outputs it is read from."""

from datetime import UTC, datetime

import pytest

from hiring.transparency import (
    DIFFERED,
    FOLLOWED,
    MITRA,
    NO_RECOMMENDATION,
    NOT_COMPARABLE,
    model_said,
    outcome,
)
from tests.console_support import org_client, sign_in  # noqa: F401

PASS = "Screening score 72%, at or above the 50% cut-off, judged on 90% of the job's weight."
FAIL = "Screening score 31%, below the 50% cut-off, judged on 85% of the job's weight."
THIN = FAIL + " Too little evidence to judge: the CV speaks to less than 75% of what the job asks for."
PROCEED = "HR round evaluation recommends: proceed to the technical round."
REJECT = "HR round evaluation recommends: reject."
DISCUSS = "HR round evaluation recommends: discuss: the evidence points neither way."


# ---- followed or differed, decided only when it can be ---------------------------------------


def test_what_the_model_said_is_read_only_from_sentences_that_point_one_way():
    assert model_said(PASS) == "go" and model_said(FAIL) == "stop"
    assert model_said(PROCEED) == "go" and model_said(REJECT) == "stop"
    assert model_said(THIN) is None
    assert model_said(DISCUSS) is None
    assert model_said("No evaluation: the recording could not be evaluated.") is None


@pytest.mark.parametrize("actor,to,rec,expected", [
    ("hr", "ready_for_hr", PASS, FOLLOWED),
    ("hr", "rejected", PASS, DIFFERED),
    ("hr", "ready_for_hr", FAIL, DIFFERED),  # "proceed anyway"
    ("hr", "rejected", FAIL, FOLLOWED),
    ("hr", "parked", PASS, NOT_COMPARABLE),  # "Not now" answers neither
    ("hr", "ready_for_hr", THIN, NOT_COMPARABLE),
    ("hr", "ready_for_technical", PROCEED, FOLLOWED),
    ("hr", "rejected", PROCEED, DIFFERED),
    ("hr", "rejected", DISCUSS, NOT_COMPARABLE),
    ("bu_head", "shortlisted", None, NO_RECOMMENDATION),
    ("system", "screened", PASS, MITRA),
])
def test_outcome(actor, to, rec, expected):
    assert outcome(actor, to, rec) == expected


# ---- the routes -------------------------------------------------------------------------------


@pytest.fixture
async def history(org_client):
    from app import db
    from app.db import Candidate
    from hiring.models import AuditEvent, DecisionRecord, HRRound, Screening

    demo = org_client.demo
    hr = demo.user_ids["hr@mitra.local"]
    job = demo.job_ids["AI Engineer"]
    async with db.get_sessionmaker()() as session:
        for cid, name in (("cand_t1", "Tara Iyer"), ("cand_t2", "Dev Malhotra"), ("cand_t3", "Kabir Rao")):
            session.add(Candidate(id=cid, job_id=job, name=name, cv_text="cv", org_id=demo.org_id, stage="hr_done"))
        await session.flush()
        session.add_all([
            DecisionRecord(candidate_id="cand_t1", from_stage="new", to_stage="screened", actor="system",
                           model_recommendation=PASS),
            DecisionRecord(candidate_id="cand_t1", from_stage="screened", to_stage="ready_for_hr", actor="hr",
                           actor_user_id=hr, model_recommendation=PASS),
            DecisionRecord(candidate_id="cand_t2", from_stage="screened", to_stage="ready_for_hr", actor="hr",
                           actor_user_id=hr, model_recommendation=FAIL, reason="Strong open-source work the CV undersells."),
            DecisionRecord(candidate_id="cand_t3", from_stage="screened", to_stage="parked", actor="hr",
                           actor_user_id=hr, model_recommendation=PASS, reason="Role on hold."),
        ])
        session.add(Screening(
            candidate_id="cand_t1", status="ready", finished_at=datetime.now(UTC),
            fit_analysis={"score_percent": 72, "assessed_weight_percent": 90, "summary": "Strong on retrieval systems.",
                          "ratings": [], "model": "gpt-6.1-sol", "prompt_version": "fit-2026-11-18"},
            hr_questions={"questions": [], "model": "gpt-6.1-sol", "prompt_version": "hr-questions-2026-11-18"},
        ))
        session.add(Screening(
            candidate_id="cand_t2", status="ready", finished_at=datetime.now(UTC),
            fit_analysis={"score_percent": 31, "assessed_weight_percent": 85, "summary": "Thin on deployment.",
                          "ratings": [], "model": "gpt-6.1-sol", "prompt_version": "fit-2026-11-18"},
        ))
        from app.db import Interview
        session.add(Interview(id="int_t1", candidate_id="cand_t1", round="hr", status="completed",
                              meeting_id="111", password="x", daily_room_name="r", daily_room_url="u", room_expires_at=datetime.now(UTC)))
        await session.flush()
        session.add(HRRound(
            interview_id="int_t1", candidate_id="cand_t1", eval_status="ready", evaluated_at=datetime.now(UTC),
            transcript=[
                {"index": 0, "speaker": "hr", "text": "What is your notice period?", "start": 0, "end": 3},
                {"index": 1, "speaker": "candidate", "text": "Sixty days, and I can negotiate it down.", "start": 4, "end": 8},
            ],
            attribution={"confidence": "clear"},
            evaluation={"recommendation": "proceed_to_technical", "summary": "Candid.", "findings_for_technical": [],
                        "model": "gpt-6.1-sol", "prompt_version": "hr-eval-2026-11-19"},
        ))
        session.add(AuditEvent(org_id=demo.org_id, actor_user_id=hr, action="candidate_added",
                               subject_type="candidate", subject_id="cand_t1"))
        await session.commit()
    return org_client


async def test_only_the_org_admin_can_read_any_of_it(history):
    for email in ("hr@mitra.local", "ai.head@mitra.local"):
        sign_in(history, email)
        for path in ("decisions", "records", "ai", "audit"):
            assert history.get(f"/api/transparency/{path}").status_code == 403, (email, path)
        assert history.get("/api/transparency/recordings/int_t1").status_code == 403


async def test_the_decision_log_says_who_followed_the_model_and_filters(history):
    sign_in(history, "admin@mitra.local")
    data = history.get("/api/transparency/decisions").json()
    s = data["summary"]
    assert (s["by_people"], s["followed"], s["differed"], s["not_comparable"]) == (3, 1, 1, 1)
    assert s["differed_percent"] == 50
    by_name = {(i["candidate"]["name"], i["to"]): i for i in data["items"]}
    proceed_anyway = by_name[("Dev Malhotra", "ready_for_hr")]
    assert proceed_anyway["outcome"] == "differed" and proceed_anyway["actor"]["name"] == "HR Partner"
    assert proceed_anyway["model_recommendation"] == FAIL and "open-source" in proceed_anyway["reason"]
    assert by_name[("Tara Iyer", "screened")]["actor"]["name"] == "MITRA"

    differed = history.get("/api/transparency/decisions?result=differed").json()
    assert [i["candidate"]["name"] for i in differed["items"]] == ["Dev Malhotra"]
    assert differed["summary"]["by_people"] == 3  # the summary ignores the outcome filter
    assert history.get("/api/transparency/decisions?actor=mitra").json()["total"] == 1
    assert history.get("/api/transparency/decisions?q=kabir").json()["total"] == 1


async def test_records_are_searched_by_what_was_said(history):
    sign_in(history, "admin@mitra.local")
    data = history.get("/api/transparency/records?q=negotiate").json()
    assert data["total"] == 1
    hit = data["items"][0]
    assert hit["kind"] == "hr_round" and hit["matches"][0]["speaker"] == "candidate"
    assert hit["open"] == "/candidates/cand_t1/hr-round"
    screening = history.get("/api/transparency/records?q=deployment&kind=screening").json()
    assert [i["candidate"]["name"] for i in screening["items"]] == ["Dev Malhotra"]
    assert history.get("/api/transparency/records?q=nothing-like-this").json()["total"] == 0


async def test_the_register_groups_by_purpose_model_and_prompt_version(history):
    sign_in(history, "admin@mitra.local")
    data = history.get("/api/transparency/ai").json()
    groups = {(g["purpose"], g["model"], g["prompt_version"]): g["count"] for g in data["register"]}
    assert groups[("cv_fit", "gpt-6.1-sol", "fit-2026-11-18")] == 2
    assert groups[("hr_questions", "gpt-6.1-sol", "hr-questions-2026-11-18")] == 1
    assert groups[("hr_evaluation", "gpt-6.1-sol", "hr-eval-2026-11-19")] == 1
    drill = history.get("/api/transparency/ai?purpose=cv_fit").json()
    assert drill["total"] == 2 and {i["candidate"]["name"] for i in drill["items"]} == {"Tara Iyer", "Dev Malhotra"}


async def test_the_audit_trail_is_searchable_and_filterable(history):
    sign_in(history, "admin@mitra.local")
    data = history.get("/api/transparency/audit").json()
    assert any(i["action"] == "signed_in" for i in data["items"])
    added = history.get("/api/transparency/audit?action=candidate_added").json()
    assert added["total"] == 1 and added["items"][0]["label"] == "Added a candidate"
    assert added["items"][0]["actor"]["name"] == "HR Partner"
    assert history.get("/api/transparency/audit?q=cand_t1").json()["total"] == 1
