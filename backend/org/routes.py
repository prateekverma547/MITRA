"""Console API: signing in, who you are, and your home screen's data.

Every route here is under `/api`. The console app (frontend/console) calls
them; the old `/admin` panel does not.
"""

import os
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app.db import Candidate, Job, get_sessionmaker, is_deployed
from hiring.stages import audit
from org.auth import (
    COOKIE_NAME,
    SessionSecretMissing,
    SignedIn,
    cookie_settings,
    current_user,
    issue_session,
    job_scope,
)
from org.models import BusinessUnit, User
from org.seed import DEMO_PASSWORD, PEOPLE
from org.users import authenticate, normalise_email
from shared.stages import Role

router = APIRouter(prefix="/api", tags=["console: org"])


class LoginRequest(BaseModel):
    email: str
    password: str


def _me(user: User, bu_name: str | None = None) -> dict:
    me = SignedIn.of(user).__dict__ | {"bu_name": bu_name}
    return me


async def _bu_name(session, user: User) -> str | None:
    if not user.bu_id:
        return None
    bu = await session.get(BusinessUnit, user.bu_id)
    return bu.name if bu else None


@router.post("/auth/login")
async def login(body: LoginRequest, response: Response) -> dict:
    """Sign in. A wrong email and a wrong password get the same answer."""
    async with get_sessionmaker()() as session:
        user = await authenticate(session, body.email, body.password)
        if user is None:
            await audit(
                session, "sign_in_failed", by=None, subject_type="email",
                subject_id=normalise_email(body.email)[:64],
            )
            await session.commit()
            raise HTTPException(status_code=401, detail="That email and password do not match an account.")
        try:
            token = issue_session(user.id)
        except SessionSecretMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        await audit(session, "signed_in", by=user, subject_type="user", subject_id=user.id)
        await session.commit()
        bu_name = await _bu_name(session, user)
    response.set_cookie(value=token, **cookie_settings())
    return _me(user, bu_name)


@router.post("/auth/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"signed_out": True}


@router.get("/auth/demo")
async def demo_accounts() -> dict:
    """The demo accounts a developer can sign in with, on their own machine only.

    Public, because it is read by the sign-in screen. On a deployed instance it
    is always empty: a known password on a public URL is worse than none, and
    the seed scripts refuse to create these accounts there anyway. Locally it
    lists only demo accounts that exist, are active, and really do sign in with
    the demo password, so a button never offers a sign-in that fails.
    """
    if is_deployed() or os.environ.get("DEMO_LOGIN", "1").strip().lower() in ("0", "false", "no", "off"):
        return {"enabled": False, "accounts": []}
    password = os.environ.get("DEMO_PASSWORD") or DEMO_PASSWORD
    accounts = []
    async with get_sessionmaker()() as session:
        for email, _, role, bu in PEOPLE:
            user = await authenticate(session, email, password)
            if user is not None:
                accounts.append({"email": user.email, "name": user.name, "role": user.role, "bu_name": bu})
    return {"enabled": bool(accounts), "accounts": accounts, "password": password if accounts else None}


@router.get("/auth/me")
async def me(user: User = Depends(current_user)) -> dict:
    async with get_sessionmaker()() as session:
        return _me(user, await _bu_name(session, user))


@router.get("/business-units")
async def business_units(user: User = Depends(current_user)) -> list[dict]:
    """The BUs this user works with: their own for a BU head, all of them otherwise."""
    async with get_sessionmaker()() as session:
        query = select(BusinessUnit).where(BusinessUnit.org_id == user.org_id)
        if Role(user.role) is Role.BU_HEAD:
            query = query.where(BusinessUnit.id == user.bu_id)
        rows = (await session.execute(query.order_by(BusinessUnit.name))).scalars().all()
    return [{"id": bu.id, "name": bu.name} for bu in rows]


@router.get("/home")
async def home(user: User = Depends(current_user)) -> dict:
    """What the signed-in person's home screen shows, already scoped to them.

    Grouped by BU, each with its job requests and how many candidates sit at
    each stage. Old /admin jobs with no BU appear under "Unassigned" for HR and
    admins; a BU head never sees them.
    """
    async with get_sessionmaker()() as session:
        bus_query = select(BusinessUnit).where(BusinessUnit.org_id == user.org_id)
        if Role(user.role) is Role.BU_HEAD:
            bus_query = bus_query.where(BusinessUnit.id == user.bu_id)
        bus = (await session.execute(bus_query.order_by(BusinessUnit.name))).scalars().all()

        jobs = (
            await session.execute(select(Job).where(job_scope(user)).order_by(Job.created_at.desc()))
        ).scalars().all()

        counts: dict[str, dict[str, int]] = defaultdict(dict)
        if jobs:
            rows = await session.execute(
                select(Candidate.job_id, Candidate.stage, func.count())
                .where(Candidate.job_id.in_([j.id for j in jobs]))
                .group_by(Candidate.job_id, Candidate.stage)
            )
            for job_id, stage, n in rows:
                counts[job_id][stage] = n

        bu_name = await _bu_name(session, user)

    def job_view(job: Job) -> dict:
        return {
            "id": job.id,
            "title": job.title or "Untitled job",
            "request_status": job.request_status,
            "candidates_by_stage": counts.get(job.id, {}),
        }

    groups = [
        {"id": bu.id, "name": bu.name, "jobs": [job_view(j) for j in jobs if j.bu_id == bu.id]}
        for bu in bus
    ]
    unassigned = [job_view(j) for j in jobs if j.bu_id is None]
    if unassigned:
        groups.append({"id": None, "name": "Unassigned", "jobs": unassigned})

    return {"me": _me(user, bu_name), "business_units": groups}


from org import admin  # noqa: E402,F401  (registers the admin routes on this router)
