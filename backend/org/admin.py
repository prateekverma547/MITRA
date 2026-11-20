"""The org admin's own screens: people and business units, and privacy. Track H.

People: invite someone with a role (and a BU, for a BU head), change their role
or BU, deactivate them, reset their password. There is no email (decided
2026-11-18), so a new or reset password is shown once, to the admin, who passes
it on.

Privacy, under the DPDP Act 2023:
- consent records, read-only, from the interviews themselves;
- retention periods for each kind of personal data, counted from the day a
  candidate's process ended, with the number of candidates now past each one;
- deletion requests from candidates, completed or refused with a reason.

Nothing is deleted on a timer. A person runs each deletion from the screen,
after a confirmation, and every run is in the audit log. Decisions about a
candidate are never deleted: who decided what, and when, is the record the
organisation has to be able to account for.
"""

import os
import secrets
from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Candidate, Interview, Job, RecordingStatus, get_sessionmaker
from hiring.models import DecisionRecord, HRRound, Screening
from hiring.stages import audit
from org.auth import hash_password, job_scope, require_role
from org.models import BusinessUnit, DeletionRequest, Organisation, User
from org.routes import router
from org.users import UserError, create_user, new_id
from shared.stages import TERMINAL, Role

ADMIN = require_role(Role.ORG_ADMIN)

ERASED_NAME = "Erased candidate"

#: The kinds of personal data with a retention period, in the order shown.
CATEGORIES = {
    "cv": ("CVs and screening", "retention_cv_days",
           "The CV text, the fit analysis built from it, the HR questions and MITRA's interview plan."),
    "recording": ("Recordings", "retention_recording_days",
                  "Video and audio of the HR and technical rounds."),
    "transcript": ("Transcripts and evaluations", "retention_transcript_days",
                   "Transcripts, HR notes, the HR round evaluation and the technical report."),
}

_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def temporary_password() -> str:
    """Readable over the phone: no 0/o or 1/l/i. Three groups of four."""
    return "-".join("".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(4)) for _ in range(3))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ---- people -------------------------------------------------------------------------------------


def _user_view(user: User, bu_names: dict[str, str], me: User) -> dict:
    return {
        "id": user.id, "name": user.name, "email": user.email, "role": user.role,
        "bu_id": user.bu_id, "bu_name": bu_names.get(user.bu_id) if user.bu_id else None,
        "active": user.active, "last_sign_in_at": _iso(user.last_sign_in_at),
        "created_at": _iso(user.created_at), "is_you": user.id == me.id,
    }


async def _bu_names(session: AsyncSession, org_id: str) -> dict[str, str]:
    rows = await session.execute(select(BusinessUnit).where(BusinessUnit.org_id == org_id))
    return {b.id: b.name for b in rows.scalars()}


async def _org_user(session: AsyncSession, me: User, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None or user.org_id != me.org_id:
        raise HTTPException(status_code=404, detail="No such person in this organisation.")
    return user


@router.get("/admin/users")
async def list_users(
    q: str = "",
    role: Role | None = None,
    bu: str | None = None,
    status: str = Query("all", pattern="^(all|active|inactive)$"),
    me: User = Depends(ADMIN),
) -> dict:
    async with get_sessionmaker()() as session:
        query = select(User).where(User.org_id == me.org_id)
        if role:
            query = query.where(User.role == role.value)
        if bu:
            query = query.where(User.bu_id == bu)
        if status != "all":
            query = query.where(User.active.is_(status == "active"))
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(or_(User.name.ilike(like), User.email.ilike(like)))
        users = (await session.execute(query.order_by(User.name))).scalars().all()
        everyone = (await session.execute(select(User.role, User.active).where(User.org_id == me.org_id))).all()
        names = await _bu_names(session, me.org_id)
    counts = Counter(r for r, active in everyone if active)
    return {
        "users": [_user_view(u, names, me) for u in users],
        "counts": {"total": len(everyone), "active": sum(1 for _, a in everyone if a),
                   "by_role": {r.value: counts.get(r.value, 0) for r in Role}},
    }


class InviteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    email: str
    role: Role
    bu_id: str | None = None


@router.post("/admin/users")
async def invite_user(body: InviteRequest, me: User = Depends(ADMIN)) -> dict:
    """Create an account. The temporary password is in this answer and nowhere else."""
    password = temporary_password()
    async with get_sessionmaker()() as session:
        try:
            user = await create_user(
                session, org_id=me.org_id, email=body.email, name=body.name, role=body.role,
                password=password, bu_id=body.bu_id or None,
            )
        except UserError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(session, "user_invited", by=me, subject_type="user", subject_id=user.id,
                    detail={"email": user.email, "role": user.role, "bu_id": user.bu_id})
        await session.commit()
        names = await _bu_names(session, me.org_id)
    return {"user": _user_view(user, names, me), "temporary_password": password}


class UserChange(BaseModel):
    role: Role | None = None
    bu_id: str | None = None
    active: bool | None = None


async def _active_admins(session: AsyncSession, org_id: str) -> int:
    return await session.scalar(
        select(func.count()).select_from(User)
        .where(User.org_id == org_id, User.role == Role.ORG_ADMIN.value, User.active.is_(True))
    ) or 0


@router.post("/admin/users/{user_id}")
async def change_user(user_id: str, body: UserChange, me: User = Depends(ADMIN)) -> dict:
    """Change a role, a BU or whether the account works. The BU rule from
    `org.users` holds here too: BU heads have exactly one BU, nobody else has one."""
    async with get_sessionmaker()() as session:
        user = await _org_user(session, me, user_id)
        before = {"role": user.role, "bu_id": user.bu_id, "active": user.active}
        role = body.role or Role(user.role)
        fields = body.model_fields_set
        bu_id = (body.bu_id or None) if "bu_id" in fields else user.bu_id
        if role is not Role.BU_HEAD:
            bu_id = None
        active = user.active if body.active is None else body.active

        if user.id == me.id and (not active or role is not Role.ORG_ADMIN):
            raise HTTPException(status_code=409, detail="You cannot deactivate yourself or change your own role. Ask another admin.")
        removes_admin = Role(user.role) is Role.ORG_ADMIN and user.active and (not active or role is not Role.ORG_ADMIN)
        if removes_admin and await _active_admins(session, me.org_id) <= 1:
            raise HTTPException(status_code=409, detail="This is the last active org admin. Make someone else an admin first.")
        if role is Role.BU_HEAD:
            if not bu_id:
                raise HTTPException(status_code=422, detail="A BU head needs a business unit; without one they would see nothing.")
            bu = await session.get(BusinessUnit, bu_id)
            if bu is None or bu.org_id != me.org_id:
                raise HTTPException(status_code=422, detail="That business unit is not in this organisation.")

        user.role, user.bu_id, user.active = role.value, bu_id, active
        after = {"role": user.role, "bu_id": user.bu_id, "active": user.active}
        changed = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
        if changed:
            await audit(session, "user_changed", by=me, subject_type="user", subject_id=user.id, detail=changed)
        await session.commit()
        names = await _bu_names(session, me.org_id)
        return {"user": _user_view(user, names, me)}


@router.post("/admin/users/{user_id}/reset-password")
async def reset_password(user_id: str, me: User = Depends(ADMIN)) -> dict:
    password = temporary_password()
    async with get_sessionmaker()() as session:
        user = await _org_user(session, me, user_id)
        user.password_hash = hash_password(password)
        await audit(session, "user_password_reset", by=me, subject_type="user", subject_id=user.id)
        await session.commit()
    return {"temporary_password": password}


# ---- business units --------------------------------------------------------------------------------


@router.get("/admin/business-units")
async def list_business_units(me: User = Depends(ADMIN)) -> dict:
    async with get_sessionmaker()() as session:
        bus = (await session.execute(
            select(BusinessUnit).where(BusinessUnit.org_id == me.org_id).order_by(BusinessUnit.name)
        )).scalars().all()
        heads = Counter(
            bu for (bu,) in (await session.execute(
                select(User.bu_id).where(User.org_id == me.org_id, User.role == Role.BU_HEAD.value, User.active.is_(True))
            )).all()
        )
        head_names: dict[str, list[str]] = {}
        for name, bu in (await session.execute(
            select(User.name, User.bu_id).where(User.org_id == me.org_id, User.role == Role.BU_HEAD.value, User.active.is_(True))
        )).all():
            head_names.setdefault(bu, []).append(name)
        open_jobs = Counter(
            bu for (bu,) in (await session.execute(
                select(Job.bu_id).where(Job.org_id == me.org_id, Job.request_status == "open")
            )).all()
        )
        candidates = Counter(
            bu for (bu,) in (await session.execute(
                select(Job.bu_id).join(Candidate, Candidate.job_id == Job.id).where(Job.org_id == me.org_id)
            )).all()
        )
    return {"business_units": [
        {"id": b.id, "name": b.name, "heads": heads.get(b.id, 0), "head_names": sorted(head_names.get(b.id, [])),
         "open_jobs": open_jobs.get(b.id, 0), "candidates": candidates.get(b.id, 0), "created_at": _iso(b.created_at)}
        for b in bus
    ]}


class BUName(BaseModel):
    name: str = Field(min_length=1, max_length=128)


async def _name_taken(session: AsyncSession, org_id: str, name: str, *, other_than: str | None = None) -> bool:
    query = select(BusinessUnit).where(BusinessUnit.org_id == org_id, func.lower(BusinessUnit.name) == name.lower())
    if other_than:
        query = query.where(BusinessUnit.id != other_than)
    return (await session.execute(query)).first() is not None


@router.post("/admin/business-units")
async def create_business_unit(body: BUName, me: User = Depends(ADMIN)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A business unit needs a name.")
    async with get_sessionmaker()() as session:
        if await _name_taken(session, me.org_id, name):
            raise HTTPException(status_code=409, detail=f"There is already a business unit called {name}.")
        bu = BusinessUnit(id=new_id("bu"), org_id=me.org_id, name=name)
        session.add(bu)
        await audit(session, "bu_created", by=me, subject_type="business_unit", subject_id=bu.id, detail={"name": name})
        await session.commit()
    return {"id": bu.id, "name": bu.name}


@router.post("/admin/business-units/{bu_id}")
async def rename_business_unit(bu_id: str, body: BUName, me: User = Depends(ADMIN)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A business unit needs a name.")
    async with get_sessionmaker()() as session:
        bu = await session.get(BusinessUnit, bu_id)
        if bu is None or bu.org_id != me.org_id:
            raise HTTPException(status_code=404, detail="No such business unit in this organisation.")
        if await _name_taken(session, me.org_id, name, other_than=bu.id):
            raise HTTPException(status_code=409, detail=f"There is already a business unit called {name}.")
        old = bu.name
        bu.name = name
        # Jobs carry the BU's name as text too (the old /admin field); keep it in step.
        for job in (await session.execute(select(Job).where(Job.bu_id == bu.id))).scalars():
            job.business_unit = name
        await audit(session, "bu_renamed", by=me, subject_type="business_unit", subject_id=bu.id,
                    detail={"from": old, "to": name})
        await session.commit()
    return {"id": bu.id, "name": name}


# ---- consent records ----------------------------------------------------------------------------------


@router.get("/admin/consent")
async def consent_records(
    q: str = "",
    show: str = Query("all", pattern="^(all|consented|missing)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    me: User = Depends(ADMIN),
) -> dict:
    """Every interview, and whether the candidate's consent was recorded before it."""
    async with get_sessionmaker()() as session:
        query = (
            select(Interview, Candidate, Job)
            .join(Candidate, Interview.candidate_id == Candidate.id)
            .join(Job, Candidate.job_id == Job.id)
            .where(job_scope(me))
        )
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(or_(Candidate.name.ilike(like), Job.title.ilike(like)))
        rows = (await session.execute(query.order_by(Interview.created_at.desc()))).all()
    consented = sum(1 for i, _, _ in rows if i.consent_accepted_at)
    everyone = len(rows)
    if show == "consented":
        rows = [r for r in rows if r[0].consent_accepted_at]
    elif show == "missing":
        rows = [r for r in rows if not r[0].consent_accepted_at]
    start = (page - 1) * page_size
    return {
        "total": len(rows), "page": page, "page_size": page_size,
        "summary": {"consented": consented, "missing": everyone - consented},
        "items": [
            {"interview_id": i.id, "candidate_id": c.id, "candidate": c.name, "job_title": j.title,
             "round": i.round, "status": i.status, "booked_at": _iso(i.created_at),
             "consent_accepted_at": _iso(i.consent_accepted_at)}
            for i, c, j in rows[start:start + page_size]
        ],
    }


# ---- retention ---------------------------------------------------------------------------------------


async def _closed_at(session: AsyncSession, org_id: str) -> dict[str, tuple[Candidate, datetime]]:
    """Candidates whose process has ended, with when it ended (their last decision)."""
    candidates = (await session.execute(
        select(Candidate).join(Job, Candidate.job_id == Job.id)
        .where(or_(Job.org_id == org_id, Job.org_id.is_(None)),
               Candidate.stage.in_([s.value for s in TERMINAL]))
    )).scalars().all()
    if not candidates:
        return {}
    last: dict[str, datetime] = {}
    for cid, at in (await session.execute(
        select(DecisionRecord.candidate_id, DecisionRecord.created_at)
        .where(DecisionRecord.candidate_id.in_([c.id for c in candidates]))
    )).all():
        at = _aware(at)
        if at and (cid not in last or at > last[cid]):
            last[cid] = at
    return {c.id: (c, last.get(c.id) or _aware(c.created_at)) for c in candidates}


async def _holds(session: AsyncSession, ids: list[str]) -> dict[str, dict[str, bool]]:
    """Which kinds of personal data each candidate still has stored."""
    held = {cid: {"cv": False, "recording": False, "transcript": False} for cid in ids}
    if not ids:
        return held
    for c in (await session.execute(select(Candidate).where(Candidate.id.in_(ids)))).scalars():
        if c.cv_text or c.blueprint or c.source_filename:
            held[c.id]["cv"] = True
    for s in (await session.execute(select(Screening).where(Screening.candidate_id.in_(ids)))).scalars():
        if s.fit_analysis or s.hr_questions:
            held[s.candidate_id]["cv"] = True
    for i in (await session.execute(select(Interview).where(Interview.candidate_id.in_(ids)))).scalars():
        if i.recording_status in (RecordingStatus.STORED, RecordingStatus.RECORDING) or i.recording_path:
            held[i.candidate_id]["recording"] = True
        if i.transcript or i.section_outcomes or i.feedback_report or i.focus_events:
            held[i.candidate_id]["transcript"] = True
    for h in (await session.execute(select(HRRound).where(HRRound.candidate_id.in_(ids)))).scalars():
        if h.transcript or h.attribution or h.evaluation or h.notes:
            held[h.candidate_id]["transcript"] = True
    return held


async def _due(session: AsyncSession, org: Organisation, now: datetime) -> dict[str, list[str]]:
    closed = await _closed_at(session, org.id)
    held = await _holds(session, list(closed))
    due: dict[str, list[str]] = {}
    for key, (_, column, _) in CATEGORIES.items():
        cutoff = now - timedelta(days=getattr(org, column))
        due[key] = [cid for cid, (_, at) in closed.items() if at and at <= cutoff and held[cid][key]]
    return due


async def _org(session: AsyncSession, me: User) -> Organisation:
    org = await session.get(Organisation, me.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="No organisation.")
    return org


@router.get("/admin/retention")
async def retention(me: User = Depends(ADMIN)) -> dict:
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        org = await _org(session, me)
        due = await _due(session, org, now)
        closed = len(await _closed_at(session, org.id))
    return {
        "closed_candidates": closed,
        "categories": [
            {"key": key, "label": label, "covers": covers, "days": getattr(org, column), "due": len(due[key])}
            for key, (label, column, covers) in CATEGORIES.items()
        ],
    }


class RetentionChange(BaseModel):
    cv: int | None = Field(None, ge=1, le=3650)
    recording: int | None = Field(None, ge=1, le=3650)
    transcript: int | None = Field(None, ge=1, le=3650)


@router.post("/admin/retention")
async def change_retention(body: RetentionChange, me: User = Depends(ADMIN)) -> dict:
    async with get_sessionmaker()() as session:
        org = await _org(session, me)
        changed = {}
        for key, (_, column, _) in CATEGORIES.items():
            value = getattr(body, key)
            if value is not None and value != getattr(org, column):
                changed[key] = [getattr(org, column), value]
                setattr(org, column, value)
        if changed:
            await audit(session, "retention_changed", by=me, subject_type="organisation", subject_id=org.id, detail=changed)
        await session.commit()
    return await retention(me)


# ---- erasure --------------------------------------------------------------------------------------------


async def _remove_recordings(candidate_ids: list[str], status: RecordingStatus) -> int:
    """Delete stored recordings for real, files included. Raises if one cannot be removed."""
    from app.recordings import remove_recording

    async with get_sessionmaker()() as session:
        interviews = list((await session.execute(
            select(Interview).where(Interview.candidate_id.in_(candidate_ids))
        )).scalars())
    interview_ids = [
        i.id for i in interviews
        if i.recording_status in (RecordingStatus.STORED, RecordingStatus.RECORDING) or i.recording_path
    ]
    for interview_id in interview_ids:
        await remove_recording(interview_id, status=status, api_key=os.environ.get("DAILY_API_KEY", ""))
    _remove_audio_captures([i.id for i in interviews])
    return len(interview_ids)


def _remove_audio_captures(interview_ids: list[str]) -> None:
    """The candidate's own audio track and the after-call check's files.

    MITRA's interviewer saves the candidate's audio for the after-call check
    (bot/capture.py, `capture/<id>`), and the check writes its results beside
    it (`aftercall/<id>`). They are recordings of the candidate too, so they go
    with the recording. Raises if a folder survives, like `remove_recording`.
    """
    import shutil

    from app.recordings import AFTERCALL_FOLDERS, recordings_root

    root = recordings_root().resolve()
    for interview_id in interview_ids:
        for name in AFTERCALL_FOLDERS:
            folder = (root / name / interview_id).resolve()
            if not folder.is_relative_to(root) or not folder.exists():
                continue
            shutil.rmtree(folder, ignore_errors=True)
            if folder.exists():
                raise RuntimeError(f"{folder} is still on disk after being deleted.")


async def _clear(session: AsyncSession, candidate_ids: list[str], kinds: set[str]) -> None:
    """Blank the stored personal data of these kinds. Decisions are left alone."""
    if not candidate_ids:
        return
    if "cv" in kinds:
        for c in (await session.execute(select(Candidate).where(Candidate.id.in_(candidate_ids)))).scalars():
            c.cv_text = ""
            c.source_filename = None
            c.blueprint = None
            c.blueprint_refinements = None
        for s in (await session.execute(select(Screening).where(Screening.candidate_id.in_(candidate_ids)))).scalars():
            s.fit_analysis = None
            s.hr_questions = None
    if "transcript" in kinds:
        for i in (await session.execute(select(Interview).where(Interview.candidate_id.in_(candidate_ids)))).scalars():
            i.transcript = None
            i.section_outcomes = None
            i.feedback_report = None
            i.focus_events = None
        for h in (await session.execute(select(HRRound).where(HRRound.candidate_id.in_(candidate_ids)))).scalars():
            h.transcript = None
            h.attribution = None
            h.evaluation = None
            h.notes = None


@router.post("/admin/retention/{category}/run")
async def run_retention(category: str, me: User = Depends(ADMIN)) -> dict:
    """Delete one kind of data for every candidate now past its retention period."""
    if category not in CATEGORIES:
        raise HTTPException(status_code=404, detail="No such kind of data.")
    async with get_sessionmaker()() as session:
        org = await _org(session, me)
        ids = (await _due(session, org, datetime.now(UTC)))[category]
    if category == "recording":
        try:
            await _remove_recordings(ids, RecordingStatus.EXPIRED)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"A recording could not be deleted, so the run stopped: {exc}") from exc
    async with get_sessionmaker()() as session:
        await _clear(session, ids, {category})
        await audit(session, "retention_run", by=me, subject_type="organisation", subject_id=me.org_id,
                    detail={"category": category, "candidates": len(ids), "candidate_ids": ids})
        await session.commit()
    return {"category": category, "erased": len(ids), "retention": await retention(me)}


async def erase_candidate(candidate_id: str) -> None:
    """Full erasure for a deletion request: every kind of data, and the name."""
    await _remove_recordings([candidate_id], RecordingStatus.DELETED)
    async with get_sessionmaker()() as session:
        await _clear(session, [candidate_id], set(CATEGORIES))
        candidate = await session.get(Candidate, candidate_id)
        if candidate is not None:
            candidate.name = ERASED_NAME
        for i in (await session.execute(select(Interview).where(Interview.candidate_id == candidate_id))).scalars():
            i.candidate_timezone = None
        await session.commit()


# ---- deletion requests ----------------------------------------------------------------------------------


def _request_view(r: DeletionRequest, names: dict[str, str]) -> dict:
    return {
        "id": r.id, "candidate_id": r.candidate_id, "candidate_name": r.candidate_name,
        "contact_note": r.contact_note, "received_at": _iso(r.received_at),
        "received_by": names.get(r.received_by), "status": r.status, "resolution": r.resolution,
        "resolved_by": names.get(r.resolved_by), "resolved_at": _iso(r.resolved_at),
    }


async def _user_names(session: AsyncSession, org_id: str) -> dict[str, str]:
    return {u.id: u.name for u in (await session.execute(select(User).where(User.org_id == org_id))).scalars()}


@router.get("/admin/deletion-requests")
async def list_deletion_requests(me: User = Depends(ADMIN)) -> dict:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(DeletionRequest).where(DeletionRequest.org_id == me.org_id)
            .order_by(DeletionRequest.received_at.desc())
        )).scalars().all()
        names = await _user_names(session, me.org_id)
    # Open requests first, oldest first: they are the ones with a clock running.
    items = sorted((_request_view(r, names) for r in rows),
                   key=lambda r: (r["status"] != "open", r["received_at"] if r["status"] == "open" else ""))
    return {"requests": items, "open": sum(1 for r in rows if r.status == "open")}


class NewDeletionRequest(BaseModel):
    candidate_id: str | None = None
    candidate_name: str = Field("", max_length=256)
    contact_note: str | None = Field(None, max_length=2000)


@router.post("/admin/deletion-requests")
async def log_deletion_request(body: NewDeletionRequest, me: User = Depends(ADMIN)) -> dict:
    async with get_sessionmaker()() as session:
        name = body.candidate_name.strip()
        if body.candidate_id:
            candidate = (await session.execute(
                select(Candidate).join(Job, Candidate.job_id == Job.id)
                .where(Candidate.id == body.candidate_id, job_scope(me))
            )).scalar_one_or_none()
            if candidate is None:
                raise HTTPException(status_code=404, detail="No such candidate in this organisation.")
            name = name or candidate.name or ""
        if not name:
            raise HTTPException(status_code=422, detail="Say who made the request: pick the candidate or type their name.")
        request = DeletionRequest(
            id=new_id("del"), org_id=me.org_id, candidate_id=body.candidate_id or None,
            candidate_name=name, contact_note=(body.contact_note or "").strip() or None,
            received_by=me.id, status="open", received_at=datetime.now(UTC),
        )
        session.add(request)
        await audit(session, "deletion_request_logged", by=me, subject_type="deletion_request", subject_id=request.id,
                    detail={"candidate_id": request.candidate_id})
        await session.commit()
        names = await _user_names(session, me.org_id)
        return _request_view(request, names)


class Resolution(BaseModel):
    resolution: str = Field(min_length=1, max_length=2000)


async def _open_request(session: AsyncSession, me: User, request_id: str) -> DeletionRequest:
    request = await session.get(DeletionRequest, request_id)
    if request is None or request.org_id != me.org_id:
        raise HTTPException(status_code=404, detail="No such deletion request.")
    if request.status != "open":
        raise HTTPException(status_code=409, detail=f"This request is already {request.status}.")
    return request


@router.post("/admin/deletion-requests/{request_id}/complete")
async def complete_deletion_request(request_id: str, body: Resolution, me: User = Depends(ADMIN)) -> dict:
    """Erase the candidate's personal data, then close the request."""
    if not body.resolution.strip():
        raise HTTPException(status_code=422, detail="Write what was done; it is kept with the request.")
    async with get_sessionmaker()() as session:
        request = await _open_request(session, me, request_id)
        candidate_id = request.candidate_id
    if candidate_id:
        try:
            await erase_candidate(candidate_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"The data could not all be erased, so the request is still open: {exc}",
            ) from exc
    async with get_sessionmaker()() as session:
        request = await _open_request(session, me, request_id)
        request.status = "completed"
        request.resolution = body.resolution.strip()
        request.resolved_by = me.id
        request.resolved_at = datetime.now(UTC)
        await audit(session, "deletion_request_completed", by=me, subject_type="deletion_request",
                    subject_id=request.id, detail={"candidate_id": candidate_id, "erased": bool(candidate_id)})
        await session.commit()
        return _request_view(request, await _user_names(session, me.org_id))


@router.post("/admin/deletion-requests/{request_id}/refuse")
async def refuse_deletion_request(request_id: str, body: Resolution, me: User = Depends(ADMIN)) -> dict:
    if not body.resolution.strip():
        raise HTTPException(status_code=422, detail="A refusal needs a reason, such as a legal hold.")
    async with get_sessionmaker()() as session:
        request = await _open_request(session, me, request_id)
        request.status = "refused"
        request.resolution = body.resolution.strip()
        request.resolved_by = me.id
        request.resolved_at = datetime.now(UTC)
        await audit(session, "deletion_request_refused", by=me, subject_type="deletion_request",
                    subject_id=request.id, detail={"candidate_id": request.candidate_id})
        await session.commit()
        return _request_view(request, await _user_names(session, me.org_id))


@router.get("/admin/candidates")
async def find_candidates(q: str = "", me: User = Depends(ADMIN)) -> dict:
    """Pick the candidate a deletion request is about."""
    needle = q.strip()
    if len(needle) < 2:
        return {"candidates": []}
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Candidate, Job).join(Job, Candidate.job_id == Job.id)
            .where(job_scope(me), Candidate.name.ilike(f"%{needle}%"), Candidate.name != ERASED_NAME)
            .order_by(Candidate.created_at.desc()).limit(8)
        )).all()
    return {"candidates": [{"id": c.id, "name": c.name, "job_title": j.title, "stage": c.stage} for c, j in rows]}

