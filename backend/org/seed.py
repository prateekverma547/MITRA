"""A demo organisation for local development and tests.

Used by `scripts/seed_org.py` and by the Track H tests, so the people and jobs
you click through locally are the same ones the tests check. Safe to run
twice: everything is looked up before it is created.

Never run against a real organisation's database. The script refuses to on a
deployed instance unless told otherwise.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Candidate, Job, SpecStatus
from org.models import User
from org.users import (
    create_user,
    get_or_create_bu,
    get_or_create_org,
    new_id,
    normalise_email,
)
from shared.stages import Role

ORG_NAME = "Demo Organisation"

#: The well-known password `scripts/seed_org.py` gives demo accounts. Known on
#: purpose, which is why neither the seed nor the demo sign-in runs deployed.
DEMO_PASSWORD = "mitra-demo-password"
BUSINESS_UNITS = ("AI", "Business", "Data Science", "Finance", "Software")

#: (email, name, role, BU name or None)
PEOPLE = (
    ("admin@mitra.local", "Org Admin", Role.ORG_ADMIN, None),
    ("hr@mitra.local", "HR Partner", Role.HR, None),
    ("ai.head@mitra.local", "AI BU Head", Role.BU_HEAD, "AI"),
    ("finance.head@mitra.local", "Finance BU Head", Role.BU_HEAD, "Finance"),
)

_DOCUMENTS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "documents"

_FINANCE_JD = """Financial Analyst

You will build and maintain the monthly forecast, explain variances to budget
owners, and turn messy operational data into numbers leadership can act on.
2-4 years in FP&A or a similar role. Strong Excel, working SQL.
"""


def _read(name: str, fallback: str) -> str:
    path = _DOCUMENTS / name
    return path.read_text() if path.exists() else fallback


@dataclass
class DemoOrg:
    org_id: str
    bu_ids: dict[str, str]
    user_ids: dict[str, str]  # email -> id
    job_ids: dict[str, str]  # title -> id
    candidate_ids: dict[str, str]  # label -> id


async def seed_demo_org(session: AsyncSession, *, password: str) -> DemoOrg:
    org = await get_or_create_org(session, ORG_NAME)
    bus = {name: await get_or_create_bu(session, org, name) for name in BUSINESS_UNITS}

    users: dict[str, User] = {}
    for email, name, role, bu_name in PEOPLE:
        existing = (
            await session.execute(select(User).where(User.email == normalise_email(email)))
        ).scalar_one_or_none()
        users[email] = existing or await create_user(
            session, org_id=org.id, email=email, name=name, role=role, password=password,
            bu_id=bus[bu_name].id if bu_name else None,
        )

    async def job(title: str, bu: str, creator: str, jd_text: str) -> Job:
        found = (
            await session.execute(select(Job).where(Job.title == title, Job.org_id == org.id))
        ).scalar_one_or_none()
        if found:
            return found
        made = Job(
            id=new_id("job"), title=title, business_unit=bu, jd_text=jd_text,
            spec_status=SpecStatus.AWAITING_CLARIFICATION, org_id=org.id,
            bu_id=bus[bu].id, created_by=users[creator].id, request_status="open",
        )
        session.add(made)
        await session.flush()
        return made

    ai_job = await job(
        "AI Engineer", "AI", "ai.head@mitra.local",
        _read("JD-C-senior-aiml-engineer.md", "AI Engineer: build LLM and voice systems."),
    )
    finance_job = await job("Financial Analyst", "Finance", "finance.head@mitra.local", _FINANCE_JD)

    async def candidate(label: str, job: Job, cv_text: str, stage: str) -> Candidate:
        found = (
            await session.execute(select(Candidate).where(Candidate.job_id == job.id, Candidate.name == label))
        ).scalar_one_or_none()
        if found:
            return found
        made = Candidate(
            id=new_id("cand"), job_id=job.id, name=label, cv_text=cv_text,
            org_id=org.id, stage=stage,
        )
        session.add(made)
        await session.flush()
        return made

    ai_cand = await candidate(
        "Sample AI candidate", ai_job,
        _read("cv_karandeep_aiml.txt", "An AI/ML engineer's CV."), "screened",
    )
    fin_cand = await candidate(
        "Sample finance candidate", finance_job, "A financial analyst's CV.", "screened",
    )

    return DemoOrg(
        org_id=org.id,
        bu_ids={name: bu.id for name, bu in bus.items()},
        user_ids={email: u.id for email, u in users.items()},
        job_ids={ai_job.title: ai_job.id, finance_job.title: finance_job.id},
        candidate_ids={"ai": ai_cand.id, "finance": fin_cand.id},
    )
