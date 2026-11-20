"""The BU head's results page, the job comparison, and who may decide."""

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.console_support import org_client, sign_in  # noqa: F401

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"


async def prepared(client, stage: str = "with_bu_head", *, label: str = "ai") -> str:
    """A candidate with a real fit analysis and HR round evaluation, at `stage`."""
    from app import db
    from app.db import Candidate, Interview
    from hiring.models import HRRound, Screening

    cid = client.demo.candidate_ids[label]
    fit = json.loads((FIXTURES / "fit_analysis.json").read_text()) | {"candidate_id": cid}
    evaluation = json.loads((FIXTURES / "hr_round_evaluation.json").read_text()) | {"candidate_id": cid}
    async with db.get_sessionmaker()() as session:
        candidate = await session.get(Candidate, cid)
        candidate.stage = stage
        session.add(Screening(candidate_id=cid, status="ready", fit_analysis=fit))
        interview = Interview(id=f"int_{cid}", candidate_id=cid, round="hr", status="completed",
                              meeting_id=f"m{cid[-6:]}", password="x", daily_room_name="r", daily_room_url="u",
                              room_expires_at=datetime.now(UTC))
        session.add(interview)
        await session.flush()
        session.add(HRRound(interview_id=interview.id, candidate_id=cid, eval_status="ready", evaluation=evaluation,
                            notes="Clear and candid."))
        await session.commit()
    return cid


async def test_the_results_page_is_built_from_the_evidence(org_client):
    cid = await prepared(org_client)
    sign_in(org_client, "ai.head@mitra.local")
    r = org_client.get(f"/api/candidates/{cid}/results").json()
    assert r["jd_match"]["score_percent"] > 0
    assert r["rubric"] and r["rubric"][0]["weight_percent"] >= r["rubric"][-1]["weight_percent"]
    assert r["hr_round"]["recommendation"] in ("proceed_to_technical", "reject", "discuss")
    assert all(s["source"] in ("cv", "hr_round") for s in r["strengths"] + r["gaps"])
    assert all((s["rating"] or 0) >= 3 for s in r["strengths"])
    # No report exists, so nothing technical is shown: the slot stays empty.
    assert r["technical"]["report"] is None
    assert r["can_decide"] and set(r["next"]) == {"shortlisted", "on_hold", "rejected"}


async def test_hr_and_admins_read_but_do_not_decide(org_client):
    cid = await prepared(org_client)
    for email in ("hr@mitra.local", "admin@mitra.local"):
        sign_in(org_client, email)
        r = org_client.get(f"/api/candidates/{cid}/results").json()
        assert r["can_decide"] is False and r["next"] == []
    sign_in(org_client, "hr@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/move", json={"to": "shortlisted"}).status_code == 403


async def test_another_bus_head_cannot_see_or_decide(org_client):
    cid = await prepared(org_client)
    sign_in(org_client, "sales.head@mitra.local")
    assert org_client.get(f"/api/candidates/{cid}/results").status_code == 404
    assert org_client.post(f"/api/candidates/{cid}/move", json={"to": "shortlisted"}).status_code == 404


async def test_the_bu_head_decides_with_a_reason_where_one_is_needed(org_client):
    cid = await prepared(org_client)
    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/move", json={"to": "on_hold"}).status_code == 422
    held = org_client.post(f"/api/candidates/{cid}/move", json={"to": "on_hold", "reason": "Waiting on budget."})
    assert held.status_code == 200
    r = org_client.get(f"/api/candidates/{cid}/results").json()
    assert set(r["next"]) == {"shortlisted", "rejected"}
    assert org_client.post(f"/api/candidates/{cid}/move", json={"to": "shortlisted"}).status_code == 200
    r = org_client.get(f"/api/candidates/{cid}/results").json()
    assert r["candidate"]["stage"] == "shortlisted" and not r["can_decide"]
    assert r["decisions"][-1]["actor_name"]


async def test_the_comparison_shows_screened_candidates_on_one_yardstick(org_client):
    cid = await prepared(org_client, stage="hr_done")
    job_id = org_client.demo.job_ids["AI Engineer"]
    sign_in(org_client, "ai.head@mitra.local")
    c = org_client.get(f"/api/jobs/{job_id}/compare").json()
    assert c["competencies"] and c["soft_skills"]
    row = next(x for x in c["candidates"] if x["id"] == cid)
    assert row["score_percent"] is not None and row["hr_round"]["recommendation"]
    assert set(row["ratings"]) <= {comp["id"] for comp in c["competencies"]} | set(row["ratings"])


async def test_closed_candidates_are_hidden_unless_asked_for(org_client):
    cid = await prepared(org_client, stage="rejected")
    job_id = org_client.demo.job_ids["AI Engineer"]
    sign_in(org_client, "hr@mitra.local")
    default = org_client.get(f"/api/jobs/{job_id}/compare").json()
    assert cid not in [x["id"] for x in default["candidates"]] and default["hidden"]["closed"] == 1
    everyone = org_client.get(f"/api/jobs/{job_id}/compare?include_closed=true").json()
    assert cid in [x["id"] for x in everyone["candidates"]]


async def test_the_comparison_is_scoped_to_the_bu(org_client):
    job_id = org_client.demo.job_ids["AI Engineer"]
    sign_in(org_client, "sales.head@mitra.local")
    assert org_client.get(f"/api/jobs/{job_id}/compare").status_code == 404


async def test_a_real_technical_report_shows_its_headline_and_is_recorded_beside_decisions(org_client):
    from app import db
    from app.db import Interview

    cid = await prepared(org_client, stage="technical_done")
    report = {"recommendation": "some_evidence_for", "summary": "Solid on evaluation.",
              "competency_scores": [{"name": "Evaluation", "score": 4.0, "insufficient_signal": False}]}
    async with db.get_sessionmaker()() as session:
        session.add(Interview(id=f"tech_{cid}", candidate_id=cid, round="technical", status="completed",
                              meeting_id=f"t{cid[-6:]}", password="x", daily_room_name="r", daily_room_url="u",
                              room_expires_at=datetime.now(UTC), feedback_report=report))
        await session.commit()
    sign_in(org_client, "hr@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/send-to-bu-head", json={}).status_code == 200
    sign_in(org_client, "ai.head@mitra.local")
    r = org_client.get(f"/api/candidates/{cid}/results").json()
    assert r["technical"]["report"]["recommendation"] == "some_evidence_for"
    assert r["decisions"][-1]["model_recommendation"] == "MITRA interview report: some evidence for."
    org_client.post(f"/api/candidates/{cid}/move", json={"to": "shortlisted"})
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["actor"] == "bu_head" and last["model_recommendation"].startswith("MITRA interview report")
