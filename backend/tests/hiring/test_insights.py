"""Dashboards, the candidate directory and search: right numbers, right scope."""

from datetime import UTC, datetime, timedelta

import pytest

from shared.screening import CompetencyRating, FitAnalysis
from tests.console_support import org_client, sign_in  # noqa: F401


def fit(candidate_id: str, ratings: list[tuple[float, int | None]]) -> dict:
    return FitAnalysis.from_ratings(
        candidate_id=candidate_id, job_id="j", summary="",
        ratings=[
            CompetencyRating(competency_id=f"c{i}", competency_name=f"C{i}", weight=w, rating=r,
                             evidence=["a line from the CV"] if r else [])
            for i, (w, r) in enumerate(ratings)
        ],
    ).model_dump(mode="json")


@pytest.fixture
async def populated(org_client):
    """Several AI candidates across stages, one Finance candidate, screenings and decisions."""
    from app import db
    from app.db import Candidate
    from hiring.models import DecisionRecord, Screening

    demo = org_client.demo
    ai_job = demo.job_ids["AI Engineer"]
    async with db.get_sessionmaker()() as session:
        rows = [
            ("cand_a1", "Asha Menon", "screened", [(0.5, 4), (0.5, 3)]),          # 87.5, passes
            ("cand_a2", "Vikram Shah", "screened", [(0.2, 4), (0.8, None)]),      # too little evidence
            ("cand_a3", "Neha Gupta", "hr_booked", [(1.0, 2)]),
            ("cand_a4", "Ravi Kumar", "with_bu_head", [(1.0, 3)]),
            ("cand_a5", "Imran Ali", "rejected", [(1.0, 1)]),
        ]
        for cid, name, stage, ratings in rows:
            session.add(Candidate(id=cid, job_id=ai_job, name=name, cv_text="cv", org_id=demo.org_id, stage=stage))
            await session.flush()
            session.add(Screening(candidate_id=cid, status="ready", fit_analysis=fit(cid, ratings),
                                  finished_at=datetime.now(UTC)))
        old = datetime.now(UTC) - timedelta(days=9)
        session.add(DecisionRecord(candidate_id="cand_a4", from_stage="technical_done", to_stage="with_bu_head",
                                   actor="hr", actor_user_id=demo.user_ids["hr@mitra.local"], created_at=old))
        session.add(DecisionRecord(candidate_id="cand_a1", from_stage="new", to_stage="screened", actor="system"))
        await session.commit()
    return org_client


async def test_hr_sees_the_whole_pipeline_and_what_waits_on_them(populated):
    sign_in(populated, "hr@mitra.local")
    data = populated.get("/api/overview").json()
    pipeline = {p["key"]: p["count"] for p in data["pipeline"]}
    # Seeded: one screened AI and one screened Finance candidate, plus the five above.
    assert pipeline == {"screening": 4, "hr": 1, "technical": 0, "bu_head": 1, "shortlisted": 0, "closed": 1}
    actions = {n["name"]: n["action"] for n in data["needs_you"]}
    assert actions["Asha Menon"] == "Decide after screening"
    assert actions["Neha Gupta"] == "Run the HR round"
    assert "Ravi Kumar" not in actions  # with the BU head now
    assert data["kpis"]["active_candidates"] == 6


async def test_the_bu_head_sees_their_bu_and_their_own_decisions(populated):
    sign_in(populated, "ai.head@mitra.local")
    data = populated.get("/api/overview").json()
    assert [b["name"] for b in data["by_bu"]] == ["AI"]
    assert [n["name"] for n in data["needs_you"]] == ["Ravi Kumar"]
    assert data["needs_you"][0]["waiting_days"] >= 9
    assert data["needs_you"][0]["action"] == "Make the final decision"

    sign_in(populated, "finance.head@mitra.local")
    assert sum(p["count"] for p in populated.get("/api/overview").json()["pipeline"]) == 1


async def test_the_bu_filter_scopes_everything(populated):
    sign_in(populated, "hr@mitra.local")
    finance = populated.demo.bu_ids["Finance"]
    data = populated.get(f"/api/overview?bu={finance}").json()
    assert sum(p["count"] for p in data["pipeline"]) == 1
    assert all(n["bu_name"] == "Finance" for n in data["needs_you"])


async def test_scores_are_binned_and_the_pass_count_respects_evidence(populated):
    sign_in(populated, "hr@mitra.local")
    scores = populated.get("/api/overview").json()["scores"]
    assert scores["screened"] == 5
    # 87.5 passes; 100 on 20% of the weight does not; 50, 75 and 25 are judged on all of it.
    assert scores["passed"] == 3
    assert scores["bins"][8] == 1 and scores["bins"][9] == 1 and scores["threshold"] == 50.0


async def test_the_directory_searches_filters_sorts_and_pages(populated):
    sign_in(populated, "hr@mitra.local")
    everyone = populated.get("/api/candidates").json()
    assert everyone["total"] == 7
    # Neha's 50% is at the cut-off, which passes; Vikram has too little evidence.
    assert everyone["facets"]["needs_me"] >= 2 and everyone["facets"]["below"] == 2

    assert [i["name"] for i in populated.get("/api/candidates?q=asha").json()["items"]] == ["Asha Menon"]
    below = populated.get("/api/candidates?flag=below").json()["items"]
    assert {i["name"] for i in below} == {"Vikram Shah", "Imran Ali"}
    by_score = [i["score_percent"] for i in populated.get("/api/candidates?sort=score").json()["items"]][:2]
    assert by_score == [100.0, 87.5]
    hr_group = populated.get("/api/candidates?group=hr").json()
    assert [i["name"] for i in hr_group["items"]] == ["Neha Gupta"]

    first = populated.get("/api/candidates?page_size=5&page=1&sort=name").json()
    second = populated.get("/api/candidates?page_size=5&page=2&sort=name").json()
    assert len(first["items"]) == 5 and len(second["items"]) == 2
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


async def test_search_is_scoped_like_everything_else(populated):
    sign_in(populated, "finance.head@mitra.local")
    found = populated.get("/api/search?q=a").json()
    assert found["candidates"] == []  # one letter is not a search
    found = populated.get("/api/search?q=Asha").json()
    assert found["candidates"] == []
    sign_in(populated, "hr@mitra.local")
    found = populated.get("/api/search?q=Asha").json()
    assert [c["name"] for c in found["candidates"]] == ["Asha Menon"]
    assert [j["title"] for j in populated.get("/api/search?q=engineer").json()["jobs"]] == ["AI Engineer"]


# ---- what each role's home adds ------------------------------------------------------------------


async def test_conversion_counts_the_furthest_step_even_for_closed_candidates(populated):
    from app import db
    from hiring.models import DecisionRecord

    # Imran was rejected after the HR round: he reached the HR step.
    async with db.get_sessionmaker()() as session:
        session.add(DecisionRecord(candidate_id="cand_a5", from_stage="hr_done", to_stage="rejected", actor="hr",
                                   reason="Not a fit"))
        await session.commit()
    sign_in(populated, "admin@mitra.local")
    data = populated.get("/api/overview?days=0").json()
    reached = {s["key"]: s["reached"] for s in data["conversion"]}
    assert reached["screening"] >= reached["hr"] >= reached["technical"] >= reached["bu_head"]
    # Neha (hr_booked), Ravi (with_bu_head) and Imran (rejected after HR) reached HR.
    assert reached["hr"] == 3
    assert reached["bu_head"] == 1


async def test_time_in_step_comes_from_the_decision_history(populated):
    from app import db
    from app.db import Candidate
    from hiring.models import DecisionRecord

    start = datetime.now(UTC) - timedelta(days=20)
    async with db.get_sessionmaker()() as session:
        (await session.get(Candidate, "cand_a4")).created_at = start
        session.add(DecisionRecord(candidate_id="cand_a4", from_stage="screened", to_stage="ready_for_hr", actor="hr",
                                   created_at=start + timedelta(days=4)))
        session.add(DecisionRecord(candidate_id="cand_a4", from_stage="hr_done", to_stage="ready_for_technical",
                                   actor="hr", created_at=start + timedelta(days=10)))
        await session.commit()
    sign_in(populated, "admin@mitra.local")
    steps = {s["key"]: s for s in populated.get("/api/overview?days=0").json()["stage_time"]}
    assert steps["screening"]["median_days"] == pytest.approx(4, abs=0.01)
    assert steps["hr"]["median_days"] == pytest.approx(6, abs=0.01)


async def test_hr_sees_how_long_their_queue_has_waited(populated):
    sign_in(populated, "hr@mitra.local")
    data = populated.get("/api/overview").json()
    rows = {r["action"]: r for r in data["ageing"]["rows"]}
    assert rows["Decide after screening"]["count"] >= 1
    assert sum(rows["Run the HR round"]["buckets"]) == rows["Run the HR round"]["count"]
    jobs = {j["title"]: j for j in data["jobs"]}
    assert jobs["AI Engineer"]["total"] >= 5
    assert any(s["name"] == "Asha Menon" for s in jobs["AI Engineer"]["scores"])


async def test_the_bu_head_sees_only_their_own_jobs(populated):
    sign_in(populated, "ai.head@mitra.local")
    data = populated.get("/api/overview").json()
    assert {j["bu_name"] for j in data["jobs"]} == {"AI"}


async def test_badges_count_what_waits_on_you(populated):
    sign_in(populated, "hr@mitra.local")
    hr = populated.get("/api/badges").json()["needs_you"]
    assert hr == populated.get("/api/overview").json()["kpis"]["needs_you"]
    sign_in(populated, "ai.head@mitra.local")
    assert populated.get("/api/badges").json()["needs_you"] == 1  # Ravi, with the BU head
