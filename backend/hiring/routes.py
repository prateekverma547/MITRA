"""Console API for the hiring flow.

This module holds the route every gate uses, moving a candidate, plus the
history behind their current stage. The other route modules register on the
same router (`hiring/api.py`) and are imported at the bottom of this file.
"""

from fastapi import BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db import Candidate, Job, get_sessionmaker
from hiring.api import router
from hiring.models import DecisionRecord
from hiring.stages import StageError, move
from org.auth import job_scope, require_role
from org.models import User
from shared.stages import Actor, CandidateStage, Role, allowed_next

_hr_or_bu_head = require_role(Role.HR, Role.BU_HEAD)
_anyone_signed_in = require_role(Role.HR, Role.BU_HEAD, Role.ORG_ADMIN)


class MoveRequest(BaseModel):
    to: CandidateStage
    reason: str | None = None


async def _visible_candidate(session, user: User, candidate_id: str) -> Candidate:
    candidate = (
        await session.execute(
            select(Candidate).join(Job, Candidate.job_id == Job.id)
            .where(Candidate.id == candidate_id, job_scope(user))
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such candidate.")
    return candidate


@router.post("/candidates/{candidate_id}/move")
async def move_candidate(
    candidate_id: str, body: MoveRequest, background: BackgroundTasks, user: User = Depends(_hr_or_bu_head)
) -> dict:
    async with get_sessionmaker()() as session:
        candidate = await _visible_candidate(session, user, candidate_id)
        try:
            recommendation = await gate_rules(session, candidate, body.to, body.reason)
            record = await move(
                session, candidate, body.to, by=user, reason=body.reason,
                model_recommendation=recommendation,
            )
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await session.commit()
    if body.to is CandidateStage.READY_FOR_TECHNICAL:
        # MITRA's plan for the technical round is built as soon as HR says go.
        from hiring.technical import prepare_plan

        background.add_task(prepare_plan, candidate.id)
    return {"candidate_id": candidate.id, "stage": candidate.stage, "decision_id": record.id}


@router.get("/candidates/{candidate_id}/decisions")
async def candidate_decisions(candidate_id: str, user: User = Depends(_anyone_signed_in)) -> dict:
    """The candidate's stage, every move that led there, and what this user may do next."""
    async with get_sessionmaker()() as session:
        candidate = await _visible_candidate(session, user, candidate_id)
        rows = (
            await session.execute(
                select(DecisionRecord)
                .where(DecisionRecord.candidate_id == candidate.id)
                .order_by(DecisionRecord.id)
            )
        ).scalars().all()

    role = Role(user.role)
    actor = {Role.HR: Actor.HR, Role.BU_HEAD: Actor.BU_HEAD}.get(role)
    return {
        "candidate_id": candidate.id,
        "stage": candidate.stage,
        "next": [s.value for s in allowed_next(CandidateStage(candidate.stage), actor)] if actor else [],
        "decisions": [
            {
                "from": r.from_stage, "to": r.to_stage, "actor": r.actor,
                "actor_user_id": r.actor_user_id, "reason": r.reason,
                "model_recommendation": r.model_recommendation,
                "at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# Route modules that register on the same router. Imported for their effect.
from hiring import (  # noqa: E402,F401
    candidates,
    diagnostics,
    hr_round,
    insights,
    jobs,
    results,
    room,
    technical,
    transparency,
    usage,
)
from hiring.candidates import gate_rules  # noqa: E402
