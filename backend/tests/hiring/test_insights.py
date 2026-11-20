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
    assert [b["name"] for b in data["by_bu"]] == ["AI, ML & Data Science"]
    assert [n["name"] for n in data["needs_you"]] == ["Ravi Kumar"]
    assert data["needs_you"][0]["waiting_days"] >= 9
    assert data["needs_you"][0]["action"] == "Make the final decision"

    sign_in(populated, "sales.head@mitra.local")
    assert sum(p["count"] for p in populated.get("/api/overview").json()["pipeline"]) == 1


async def test_the_bu_filter_scopes_everything(populated):
    sign_in(populated, "hr@mitra.local")
    finance = populated.demo.bu_ids["Sales"]
    data = populated.get(f"/api/overview?bu={finance}").json()
    assert sum(p["count"] for p in data["pipeline"]) == 1
    assert all(n["bu_name"] == "Sales" for n in data["needs_you"])


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
    sign_in(populated, "sales.head@mitra.local")
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
    assert {j["bu_name"] for j in data["jobs"]} == {"AI, ML & Data Science"}


async def test_badges_count_what_waits_on_you(populated):
    sign_in(populated, "hr@mitra.local")
    hr = populated.get("/api/badges").json()["needs_you"]
    assert hr == populated.get("/api/overview").json()["kpis"]["needs_you"]
    sign_in(populated, "ai.head@mitra.local")
    assert populated.get("/api/badges").json()["needs_you"] == 1  # Ravi, with the BU head


# ---- the organisation's insights -------------------------------------------------------------------

from types import SimpleNamespace as NS  # noqa: E402

from hiring.insights import LONG_WAIT_DAYS, MIN_N, org_insights  # noqa: E402

NOW = datetime(2026, 11, 19, 12, tzinfo=UTC)

def _fit_row(passes: bool):
    return NS(status="ready", fit_analysis=fit("c", [(1.0, 4 if passes else 1)]))


def org(n_bus=2):
    bus = [NS(id=f"bu{i}", name=f"Unit {i}") for i in range(n_bus)]
    return bus


class World:
    """A small organisation built by hand, so each rule's numbers are exact."""

    def __init__(self, bus):
        self.bus, self.rows, self.jobs, self.decisions = bus, [], [], []
        self._n = 0

    def job(self, bu, *, opened_days=10, title="Engineer", status="open"):
        j = NS(id=f"j{len(self.jobs)}", bu_id=bu.id, title=title, request_status=status,
               created_at=NOW - timedelta(days=opened_days), business_unit=bu.name)
        self.jobs.append(j)
        return j

    def candidate(self, job, stage, *, added_days=5, passes=True, screened=True):
        self._n += 1
        c = NS(id=f"c{self._n}", stage=stage, created_at=NOW - timedelta(days=added_days), name=f"C{self._n}")
        self.rows.append((c, job, _fit_row(passes) if screened else None))
        return c

    def move(self, c, frm, to, *, days_ago, actor="hr", recommendation=None):
        d = NS(id=len(self.decisions) + 1, candidate_id=c.id, from_stage=frm, to_stage=to, actor=actor,
               created_at=NOW - timedelta(days=days_ago), model_recommendation=recommendation, actor_user_id=None)
        self.decisions.append(d)
        return d

    def run(self, days=30):
        return org_insights(self.rows, self.jobs, self.bus, self.decisions, now=NOW, days=days)


def kinds(result):
    return [i["kind"] for i in result["insights"]]


def test_a_slow_unit_is_named_only_with_enough_finished_spells():
    a, b = org()
    w = World([a, b])
    ja, jb = w.job(a), w.job(b)
    # Unit 0: HR rounds of 2 days; Unit 1: HR rounds of 9 days.
    for unit_job, spell in ((ja, 2), (jb, 9)):
        for _ in range(MIN_N):
            c = w.candidate(unit_job, "ready_for_technical", added_days=20)
            w.move(c, "screened", "ready_for_hr", days_ago=15)
            w.move(c, "hr_done", "ready_for_technical", days_ago=15 - spell)
    result = w.run(days=0)
    slow = [i for i in result["insights"] if i["kind"] == "slow_step"]
    assert slow and "Unit 1's HR rounds take 9.0 days" in slow[0]["text"]
    assert slow[0]["link"]["to"].startswith("/candidates?bu=bu1")

    # With one spell fewer in the slow unit, nothing is claimed.
    w.decisions = [d for d in w.decisions if not (d.candidate_id == w.rows[-1][0].id)]
    w.rows = w.rows[:-1]
    assert "slow_step" not in kinds(w.run(days=0))


def test_long_waits_for_bu_heads_are_counted_and_attributed():
    a, b = org()
    w = World([a, b])
    ja, jb = w.job(a), w.job(b)
    for job in (ja, ja, jb):
        c = w.candidate(job, "with_bu_head", added_days=40)
        w.move(c, "technical_done", "with_bu_head", days_ago=LONG_WAIT_DAYS + 3)
    one = w.candidate(jb, "with_bu_head", added_days=40)
    w.move(one, "technical_done", "with_bu_head", days_ago=1)  # recent: not counted
    i = next(i for i in w.run()["insights"] if i["kind"] == "bu_head_waits")
    assert i["text"].startswith("3 candidates have waited over 7 days") and "(Unit 0: 2)" in i["text"]

    # A single long wait is not an insight.
    w2 = World([a])
    c = w2.candidate(w2.job(a), "with_bu_head", added_days=40)
    w2.move(c, "technical_done", "with_bu_head", days_ago=20)
    assert "bu_head_waits" not in kinds(w2.run())


def test_disagreement_needs_enough_comparable_decisions_and_reports_the_shift():
    a = org(1)[0]
    w = World([a])
    j = w.job(a)
    go = "Screening score 80%, at or above the 50% cut-off, judged on 100% of the job's weight."
    for k in range(MIN_N):  # this period: 2 of 5 differ (40%)
        c = w.candidate(j, "ready_for_hr")
        w.move(c, "screened", "rejected" if k < 2 else "ready_for_hr", days_ago=3, recommendation=go)
    for _ in range(MIN_N):  # previous period: all followed
        c = w.candidate(j, "ready_for_hr", added_days=45)
        w.move(c, "screened", "ready_for_hr", days_ago=45, recommendation=go)
    result = w.run(days=30)
    i = next(i for i in result["insights"] if i["kind"] == "disagreement")
    assert "40% of the decisions" in i["text"] and "up from 0%" in i["text"]
    k = result["kpis"]["agreement"]
    assert k["value"] == 60.0 and k["previous"] == 100.0 and k["change_percent"] == -40.0

    # Four comparable decisions are too few to state a rate.
    w.decisions = w.decisions[:MIN_N - 1]
    assert "disagreement" not in kinds(w.run(days=30))


def test_a_screening_outlier_unit_is_flagged():
    a, b = org()
    w = World([a, b])
    ja, jb = w.job(a), w.job(b)
    for _ in range(MIN_N):
        w.candidate(ja, "screened", passes=True)
        w.candidate(jb, "screened", passes=False)
    texts = [i["text"] for i in w.run()["insights"] if i["kind"] == "pass_rate"]
    assert any("Unit 1 passes 0%" in t and "Check its jobs' descriptions" in t for t in texts)


def test_a_stale_job_needs_age_and_candidates_and_no_shortlist():
    a = org(1)[0]
    w = World([a])
    old = w.job(a, opened_days=63, title="Backend Engineer")
    for _ in range(MIN_N):
        w.candidate(old, "screened", added_days=30)
    i = next(i for i in w.run()["insights"] if i["kind"] == "stale_job")
    assert i["text"].startswith("No shortlist yet for Backend Engineer after 63 days open and 5 candidates.")

    w.candidate(old, "shortlisted", added_days=30)
    assert "stale_job" not in kinds(w.run())


def test_kpis_compare_with_the_previous_period():
    a = org(1)[0]
    w = World([a])
    j = w.job(a, opened_days=100)
    # Time to decision: 10 days now, 20 days in the previous 30.
    for _ in range(MIN_N):
        c = w.candidate(j, "shortlisted", added_days=15)
        w.move(c, "with_bu_head", "shortlisted", days_ago=5, actor="bu_head")
        c = w.candidate(j, "shortlisted", added_days=55)
        w.move(c, "with_bu_head", "shortlisted", days_ago=35, actor="bu_head")
    k = w.run(days=30)["kpis"]["time_to_decision"]
    assert k["value"] == 10.0 and k["previous"] == 20.0 and k["change_percent"] == -50.0

    # Below the minimum, no median is stated at all.
    w.decisions = w.decisions[2:]
    thin = w.run(days=30)["kpis"]["time_to_decision"]
    assert thin["n"] == MIN_N - 1 and thin["value"] is None and thin["change_percent"] is None
    # All time has nothing to compare with.
    assert w.run(days=0)["kpis"]["time_to_decision"]["change_percent"] is None


async def test_only_an_admin_reads_the_organisations_insights(populated):
    sign_in(populated, "hr@mitra.local")
    assert populated.get("/api/org/insights").status_code == 403
    sign_in(populated, "admin@mitra.local")
    body = populated.get("/api/org/insights?days=30").json()
    assert set(body) >= {"insights", "kpis", "units", "computed_at", "min_n"}
    assert {u["name"] for u in body["units"]} >= {"AI, ML & Data Science", "Sales"}


async def test_the_directory_can_keep_business_units_together(populated):
    sign_in(populated, "hr@mitra.local")
    data = populated.get("/api/candidates?by_bu=true&page_size=100").json()
    names = [i["bu_name"] for i in data["items"]]
    # Each unit's candidates are contiguous, and the counts add up.
    seen: list[str] = []
    for n in names:
        if not seen or seen[-1] != n:
            assert n not in seen, f"{n} appears in two places"
            seen.append(n)
    assert sum(data["bu_counts"].values()) == data["total"]
