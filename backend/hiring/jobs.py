"""Job requests: the BU head describes a role until MITRA understands it.

The flow, from docs/TRACK_H.md phase 1:

1. A BU head creates a request. The title is required; the JD file is optional.
2. They chat with the model (`blueprint/clarify.py`, unchanged in its rules: it
   never attributes a position the employer did not state, and it lists what
   it assumed).
3. When they confirm the summary, the spec is "what MITRA understood". They can
   Edit (the chat reopens where it left off) or Approve.
4. Approve sends it to HR: until then the request is a draft that HR cannot
   see (`org.auth.job_scope`).

Editing an approved request is a revision. When the revised spec is confirmed,
it reaches candidates exactly as it did in the old panel (`_propagate_spec_change`):
interviewed candidates are never touched, hand-refined plans are flagged
stale, and the rest are regenerated.
"""

from datetime import UTC, datetime

from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.db import Candidate, ClarificationTurn, Job, SpecStatus, get_sessionmaker
from blueprint.clarify import (
    EMPLOYER_ROLE,
    INTERVIEWER_ROLE,
    ClarificationChat,
    ClarificationError,
)
from blueprint.documents import DocumentError, extract_text
from bot.config import Settings
from hiring.api import router
from hiring.stages import audit
from org.auth import current_user, job_scope, require_role, visible_job_or_404
from org.models import BusinessUnit, User
from org.users import new_id
from shared.stages import Role

DRAFT, OPEN = "draft", "open"

_bu_head = require_role(Role.BU_HEAD)

#: The longest title a request can have; the column holds 256.
MAX_TITLE = 200


def _chat() -> ClarificationChat:
    settings = Settings.load()
    return ClarificationChat(api_key=settings.openai_api_key, model=settings.blueprint_model)


class ChatMessage(BaseModel):
    message: str


async def _load(session, user: User, job_id: str) -> Job:
    job = (
        await session.execute(
            select(Job)
            .where(Job.id == job_id, job_scope(user))
            .options(selectinload(Job.clarification_turns))
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="No such job request.")
    return job


async def _view(session, user: User, job: Job) -> dict:
    bu = await session.get(BusinessUnit, job.bu_id) if job.bu_id else None
    creator = await session.get(User, job.created_by) if job.created_by else None
    candidates = await session.scalar(
        select(func.count()).select_from(Candidate).where(Candidate.job_id == job.id)
    )
    is_owner = Role(user.role) is Role.BU_HEAD and user.bu_id is not None and user.bu_id == job.bu_id
    ready = job.spec_status == SpecStatus.READY
    return {
        "id": job.id,
        "title": job.title or "Untitled job",
        "bu_id": job.bu_id,
        "bu_name": bu.name if bu else job.business_unit,
        "request_status": job.request_status,
        "spec_status": job.spec_status,
        "spec_version": job.spec_version,
        "source_filename": job.source_filename,
        "has_jd": bool((job.jd_text or "").strip()),
        "created_by": creator.name if creator else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "candidates": candidates or 0,
        "understanding": job.evaluation_spec if ready else None,
        "inferred": (job.spec_inferred or []) if ready else [],
        "chat": [
            {"from": "mitra" if t.role == INTERVIEWER_ROLE else "you", "text": t.content}
            for t in sorted(job.clarification_turns, key=lambda t: t.index)
        ],
        "can_chat": is_owner and not ready,
        "can_edit": is_owner and ready,
        "can_approve": is_owner and ready and job.request_status == DRAFT,
    }


@router.get("/jobs")
async def list_jobs(user: User = Depends(current_user)) -> list[dict]:
    """The job requests this user may see, newest first."""
    async with get_sessionmaker()() as session:
        jobs = (
            await session.execute(
                select(Job)
                .where(job_scope(user))
                .options(selectinload(Job.clarification_turns))
                .order_by(Job.created_at.desc())
            )
        ).scalars().all()
        views = [await _view(session, user, job) for job in jobs]
    # The list does not need every chat turn.
    return [{k: v for k, v in view.items() if k != "chat"} for view in views]


@router.post("/jobs", status_code=201)
async def create_job_request(
    title: str = Form(...),
    file: UploadFile | None = File(None),
    user: User = Depends(_bu_head),
) -> dict:
    """Start a job request in the BU head's own BU, and ask the first question."""
    title = title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Give the job a title.")
    if len(title) > MAX_TITLE:
        raise HTTPException(status_code=422, detail=f"Keep the title under {MAX_TITLE} characters.")

    jd_text, filename = "", None
    if file is not None and file.filename:
        try:
            jd_text = extract_text(filename=file.filename, data=await file.read())
        except DocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        filename = file.filename

    try:
        turn = await _chat().next_turn(jd_text=jd_text, history=[], role_title=title)
    except ClarificationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async with get_sessionmaker()() as session:
        bu = await session.get(BusinessUnit, user.bu_id)
        job = Job(
            id=new_id("job"),
            title=title,
            business_unit=bu.name if bu else None,
            source_filename=filename,
            jd_text=jd_text,
            spec_status=SpecStatus.AWAITING_CLARIFICATION,
            org_id=user.org_id,
            bu_id=user.bu_id,
            created_by=user.id,
            request_status=DRAFT,
        )
        job.clarification_turns.append(
            ClarificationTurn(index=0, role=INTERVIEWER_ROLE, content=turn.reply)
        )
        session.add(job)
        await audit(session, "job_request_created", by=user, subject_type="job", subject_id=job.id)
        await session.commit()
        job = await _load(session, user, job.id)
        view = await _view(session, user, job)

    logger.info(f"[{view['id']}] job request created by {user.id} ({'with' if jd_text else 'without'} a JD)")
    return view


@router.get("/jobs/{job_id}")
async def get_job_request(job_id: str, user: User = Depends(current_user)) -> dict:
    async with get_sessionmaker()() as session:
        return await _view(session, user, await _load(session, user, job_id))


@router.post("/jobs/{job_id}/chat")
async def chat(
    job_id: str, body: ChatMessage, background: BackgroundTasks, user: User = Depends(_bu_head)
) -> dict:
    """Answer MITRA's question; get the next one, or the finished understanding."""
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Write a reply first.")

    async with get_sessionmaker()() as session:
        job = await _load(session, user, job_id)
        if job.spec_status == SpecStatus.READY:
            raise HTTPException(
                status_code=409,
                detail="MITRA has already summarised this role. Choose Edit to change it.",
            )
        turns = sorted(job.clarification_turns, key=lambda t: t.index)
        history = [{"role": t.role, "content": t.content} for t in turns]
        history.append({"role": EMPLOYER_ROLE, "content": message})

        try:
            turn = await _chat().next_turn(jd_text=job.jd_text, history=history, role_title=job.title or "")
        except ClarificationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        next_index = len(turns)
        job.clarification_turns.append(ClarificationTurn(index=next_index, role=EMPLOYER_ROLE, content=message))
        job.clarification_turns.append(ClarificationTurn(index=next_index + 1, role=INTERVIEWER_ROLE, content=turn.reply))

        is_revision = job.evaluation_spec is not None
        finished = turn.done and turn.spec is not None
        if finished:
            job.evaluation_spec = turn.spec.model_dump(mode="json")
            job.spec_inferred = turn.inferred
            job.spec_status = SpecStatus.READY
            if is_revision:
                job.spec_version += 1
            await audit(
                session, "job_request_understood", by=user, subject_type="job", subject_id=job.id,
                detail={"spec_version": job.spec_version, "inferred": len(turn.inferred)},
            )
        await session.commit()
        job = await _load(session, user, job_id)
        view = await _view(session, user, job)

    if finished and is_revision:
        from app.main import _propagate_spec_change

        view["propagation"] = await _propagate_spec_change(job_id, background)
    return view


@router.post("/jobs/{job_id}/edit")
async def edit(job_id: str, user: User = Depends(_bu_head)) -> dict:
    """Reopen the chat. It continues where it left off; MITRA keeps what was agreed."""
    async with get_sessionmaker()() as session:
        job = await _load(session, user, job_id)
        if job.spec_status != SpecStatus.READY:
            raise HTTPException(status_code=409, detail="This request is still being described.")
        job.spec_status = SpecStatus.AWAITING_CLARIFICATION
        await audit(session, "job_request_edit_started", by=user, subject_type="job", subject_id=job.id)
        await session.commit()
        return await _view(session, user, await _load(session, user, job_id))


@router.post("/jobs/{job_id}/approve")
async def approve(job_id: str, user: User = Depends(_bu_head)) -> dict:
    """Send the request to HR. Only a finished understanding can be approved."""
    async with get_sessionmaker()() as session:
        job = await _load(session, user, job_id)
        if job.spec_status != SpecStatus.READY:
            raise HTTPException(status_code=409, detail="Finish describing the role before approving it.")
        if job.request_status != DRAFT:
            raise HTTPException(status_code=409, detail="This request is already with HR.")
        job.request_status = OPEN
        await audit(
            session, "job_request_approved", by=user, subject_type="job", subject_id=job.id,
            detail={"spec_version": job.spec_version, "at": datetime.now(UTC).isoformat()},
        )
        await session.commit()
        logger.info(f"[{job_id}] job request approved and sent to HR by {user.id}")
        return await _view(session, user, await _load(session, user, job_id))
