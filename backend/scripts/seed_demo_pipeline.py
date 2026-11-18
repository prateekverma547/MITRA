"""Fill the demo organisation with a realistic pipeline, for building dashboards.

    cd backend && uv run python scripts/seed_demo_pipeline.py
    cd backend && uv run python scripts/seed_demo_pipeline.py --candidates 120

Runs `seed_org.py`'s demo organisation first, then adds job requests across
every business unit and made-up candidates at every step, with screening
results and decisions spread over the last eight weeks. Names and numbers are
invented; nothing here is a real person. The same seed gives the same data, so
two engineers looking at the same chart see the same thing.

Refuses to run on a deployed instance.
"""

import argparse
import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import db  # noqa: E402
from app.db import Candidate, Job, SpecStatus  # noqa: E402
from hiring.models import DecisionRecord, Screening  # noqa: E402
from org.seed import seed_demo_org  # noqa: E402
from org.users import new_id  # noqa: E402
from shared.screening import CompetencyRating, FitAnalysis  # noqa: E402
from shared.stages import ALLOWED_MOVES, Actor  # noqa: E402

FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Ishaan", "Ananya", "Rohan", "Saanvi", "Arjun", "Kavya", "Vihaan",
         "Tara", "Nikhil", "Riya", "Aditya", "Pooja", "Sameer", "Neha", "Karan", "Ira", "Dev", "Zoya", "Farhan", "Lakshmi"]
LAST = ["Sharma", "Iyer", "Khan", "Menon", "Reddy", "Gupta", "Nair", "Das", "Bose", "Singh", "Kulkarni", "Joshi",
        "Pillai", "Chopra", "Rao", "Mehta", "Ali", "Fernandes", "Banerjee", "Verma"]

JOBS = {
    "AI": ["Applied LLM Engineer", "Computer Vision Engineer"],
    "Data Science": ["Senior Data Scientist", "Analytics Engineer"],
    "Software": ["Backend Engineer", "Frontend Engineer"],
    "Finance": ["Financial Analyst"],
    "Business": ["Business Analyst", "Account Manager"],
}

COMPETENCIES = [("core", "Core craft", 0.35), ("depth", "Depth in the domain", 0.25),
                ("delivery", "Shipping and ownership", 0.2), ("comms", "Communication", 0.2)]

# Where candidates end up, roughly: most are early, a few reach a decision.
PATHS = [
    (["new"], 6), (["new", "screened"], 14),
    (["new", "screened", "ready_for_hr"], 6), (["new", "screened", "ready_for_hr", "hr_booked"], 6),
    (["new", "screened", "ready_for_hr", "hr_booked", "hr_done"], 5),
    (["new", "screened", "ready_for_hr", "hr_booked", "hr_done", "ready_for_technical"], 4),
    (["new", "screened", "ready_for_hr", "hr_booked", "hr_done", "ready_for_technical", "technical_booked"], 3),
    (["new", "screened", "ready_for_hr", "hr_booked", "hr_done", "ready_for_technical", "technical_booked",
      "technical_done"], 3),
    (["new", "screened", "ready_for_hr", "hr_booked", "hr_done", "ready_for_technical", "technical_booked",
      "technical_done", "with_bu_head"], 4),
    (["new", "screened", "ready_for_hr", "hr_booked", "hr_done", "ready_for_technical", "technical_booked",
      "technical_done", "with_bu_head", "shortlisted"], 3),
    (["new", "screened", "rejected"], 8), (["new", "screened", "parked"], 3),
    (["new", "screened", "ready_for_hr", "hr_booked", "hr_done", "rejected"], 4),
]

SPEC = {
    "schema_version": 1, "role_title": "", "seniority": "Mid-level", "experience_expectation": "3+ years",
    "duration_minutes": 40, "overrun_grace_minutes": 5, "language": "en", "tone": "warm and direct",
    "competencies": [{"id": c, "name": n, "description": "Example competency.", "weight": w} for c, n, w in COMPETENCIES],
    "red_flags": [],
}


def fit_for(rng: random.Random, candidate_id: str, job_id: str, strong: bool) -> dict:
    ratings = []
    for cid, name, weight in COMPETENCIES:
        if rng.random() < (0.08 if strong else 0.2):
            ratings.append(CompetencyRating(competency_id=cid, competency_name=name, weight=weight, rating=None))
            continue
        value = min(4, max(0, round(rng.gauss(3.0 if strong else 1.8, 0.8))))
        ratings.append(CompetencyRating(competency_id=cid, competency_name=name, weight=weight, rating=value,
                                        evidence=["An example line from an invented CV."] if value else []))
    return FitAnalysis.from_ratings(candidate_id=candidate_id, job_id=job_id, ratings=ratings,
                                    summary="Example screening for demo data.").model_dump(mode="json")


async def main(count: int, seed: int) -> int:
    if db.is_deployed():
        print("Refusing: this is a deployed instance, and this data is invented.")
        return 2
    rng = random.Random(seed)
    now = datetime.now(UTC)
    await db.prepare_schema()
    async with db.get_sessionmaker()() as session:
        demo = await seed_demo_org(session, password="mitra-demo-password")
        hr_id = demo.user_ids["hr@mitra.local"]
        heads = {"AI": demo.user_ids["ai.head@mitra.local"], "Finance": demo.user_ids["finance.head@mitra.local"]}

        jobs: list[Job] = []
        for bu_name, titles in JOBS.items():
            for title in titles:
                job = (await session.execute(select(Job).where(Job.title == title, Job.org_id == demo.org_id))).scalar_one_or_none()
                if job is None:
                    job = Job(
                        id=new_id("job"), title=title, business_unit=bu_name, jd_text="", org_id=demo.org_id,
                        bu_id=demo.bu_ids[bu_name], created_by=heads.get(bu_name), request_status="open",
                        spec_status=SpecStatus.READY, evaluation_spec=SPEC | {"role_title": title},
                    )
                    session.add(job)
                    await session.flush()
                jobs.append(job)

        weights = [w for _, w in PATHS]
        made = 0
        for _ in range(count):
            job = rng.choice(jobs)
            path = rng.choices([p for p, _ in PATHS], weights=weights)[0]
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            added = now - timedelta(days=rng.uniform(0, 55), hours=rng.uniform(0, 12))
            cid = new_id("cand")
            candidate = Candidate(id=cid, job_id=job.id, name=name, cv_text="An invented CV for demo data.",
                                  org_id=demo.org_id, stage=path[-1], created_at=added)
            session.add(candidate)
            await session.flush()
            strong = "rejected" not in path or len(path) > 3
            if "screened" in path:
                session.add(Screening(candidate_id=cid, status="ready", fit_analysis=fit_for(rng, cid, job.id, strong),
                                      started_at=added, finished_at=added + timedelta(minutes=1)))
            else:
                session.add(Screening(candidate_id=cid, status="waiting"))
            # Spread the moves between their arrival and now, so the activity
            # feed and the weekly chart show a history rather than a pile-up.
            span = (now - added).total_seconds()
            moments = sorted(rng.uniform(0.05, 0.95) * span for _ in path[1:])
            for (frm, to), offset in zip(zip(path, path[1:]), moments):
                at = added + timedelta(seconds=offset)
                who = ALLOWED_MOVES[(frm, to)]
                actor = Actor.SYSTEM if Actor.SYSTEM in who and len(who) == 1 else (
                    Actor.BU_HEAD if Actor.BU_HEAD in who else Actor.HR)
                user = None if actor is Actor.SYSTEM else (heads.get(job.business_unit) if actor is Actor.BU_HEAD else hr_id)
                session.add(DecisionRecord(
                    org_id=demo.org_id, candidate_id=cid, from_stage=frm, to_stage=to, actor=actor.value,
                    actor_user_id=user, created_at=at,
                    reason="Example reason." if to in ("rejected", "parked", "on_hold") else None,
                ))
            made += 1
        await session.commit()
    await db.reset_engine()
    print(f"Added {made} example candidates across {len(jobs)} job requests in {len(JOBS)} business units.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", type=int, default=70)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.candidates, args.seed)))
