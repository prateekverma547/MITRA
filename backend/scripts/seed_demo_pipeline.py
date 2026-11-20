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
    "AI, ML & Data Science": ["Applied LLM Engineer", "Computer Vision Engineer"],
    "Data Science": ["Senior Data Scientist", "Analytics Engineer"],
    "Software": ["Backend Engineer", "Frontend Engineer"],
    "Sales": ["Sales Executive"],
    "Business": ["Business Analyst", "Account Manager"],
}

COMPETENCIES = [("core", "Core craft", 0.35), ("depth", "Depth in the domain", 0.25),
                ("delivery", "Shipping and ownership", 0.2), ("comms", "Communication", 0.2)]

#: What each competency means, as a BU head would write it.
DESCRIPTIONS = {
    "core": "Does the core work of the role well, and can explain the choices behind it.",
    "depth": "Knows the domain beyond the basics: its failure modes, trade-offs and current practice.",
    "delivery": "Takes work from idea to production and owns what happens after it ships.",
    "comms": "Explains clearly to people who are not specialists, in writing and out loud.",
}

#: Lines a CV might hold, quoted as evidence for a rating.
EVIDENCE = {
    "core": ["Built the retrieval pipeline that serves 40,000 documents a month.",
             "Designed the evaluation set with the client: 300 documents, two labellers each."],
    "depth": ["Most misses were retrieval, not generation; rebuilt the chunking to fix it.",
              "Moved the model to a smaller fine-tuned variant and cut cost by half."],
    "delivery": ["Owned the rollback plan for the launch and the on-call runbook.",
                 "Shipped behind a feature flag and watched the error rate for a week."],
    "comms": ["Ran the weekly review with the client's product manager.",
              "Wrote the onboarding guide the support team uses."],
}

SUMMARIES = [
    "Hands-on builder with production experience; strongest on delivery, lighter on the domain's depth.",
    "Solid core skills and clear ownership of shipped work; communication evidence is thin in the CV.",
    "Relevant experience across the role, with specific, measured results in most areas.",
    "Early-career profile: adjacent experience, few specifics about what they owned.",
]

REASONS = {
    "rejected": ["The CV shows little of the core work the role needs.",
                 "Strong elsewhere, but the HR round showed a mismatch on location and notice period.",
                 "Did not meet the bar on the role's most important competency."],
    "parked": ["Promising, but the notice period is longer than this role can wait.",
               "Keep for the next opening in the same team."],
    "on_hold": ["Waiting for the second candidate's result before deciding."],
}

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
    "competencies": [{"id": c, "name": n, "description": DESCRIPTIONS[c], "weight": w} for c, n, w in COMPETENCIES],
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
                                        evidence=[rng.choice(EVIDENCE[cid])] if value else []))
    return FitAnalysis.from_ratings(candidate_id=candidate_id, job_id=job_id, ratings=ratings,
                                    summary=rng.choice(SUMMARIES)).model_dump(mode="json")


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
        heads = {"AI, ML & Data Science": demo.user_ids["ai.head@mitra.local"], "Sales": demo.user_ids["sales.head@mitra.local"]}

        jobs: list[Job] = []
        for bu_name, titles in JOBS.items():
            for title in titles:
                job = (await session.execute(select(Job).where(Job.title == title, Job.org_id == demo.org_id))).scalar_one_or_none()
                if job is None:
                    job = Job(
                        id=new_id("job"), title=title, business_unit=bu_name, jd_text="", org_id=demo.org_id,
                        bu_id=demo.bu_ids[bu_name], created_by=heads.get(bu_name), request_status="open",
                        spec_status=SpecStatus.READY, evaluation_spec=SPEC | {"role_title": title},
                        # Opened before its first candidate arrives (they arrive over 55 days).
                        created_at=now - timedelta(days=rng.uniform(56, 75)),
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
            candidate = Candidate(id=cid, job_id=job.id, name=name, cv_text="The CV text, as read from the uploaded file.",
                                  org_id=demo.org_id, stage=path[-1], created_at=added)
            session.add(candidate)
            await session.flush()
            strong = "rejected" not in path or len(path) > 3
            fit = None
            if "screened" in path:
                fit = fit_for(rng, cid, job.id, strong)
                session.add(Screening(candidate_id=cid, status="ready", fit_analysis=fit,
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
                    reason=rng.choice(REASONS[to]) if to in REASONS else None,
                    model_recommendation=_said(rng, frm, to, fit),
                ))
            made += 1
        await session.commit()
    await db.reset_engine()
    print(f"Added {made} example candidates across {len(jobs)} job requests in {len(JOBS)} business units.")
    return 0


def _said(rng: random.Random, frm: str, to: str, fit: dict | None) -> str | None:
    """What MITRA recommended at this move, in the sentences the real flows write.

    Screening gates carry the screening reading; decisions after the HR round
    carry an HR evaluation, which people usually but not always follow. Other
    moves carry none, as in the real system.
    """
    from hiring.candidates import recommendation

    if frm == "screened" and fit:
        return recommendation(FitAnalysis.model_validate(fit))
    if frm == "hr_done":
        agrees = rng.random() < 0.8
        go = to == "ready_for_technical"
        verdict = "proceed to the technical round" if go == agrees else "reject"
        if rng.random() < 0.1:
            verdict = "discuss: the evidence points neither way"
        return f"HR round evaluation recommends: {verdict}."
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", type=int, default=70)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.candidates, args.seed)))
