"""Moving a candidate from one stage to the next. The only way to do it.

`move()` checks the move against `shared.stages.ALLOWED_MOVES`, checks the
person is allowed to make it and may see this candidate, insists on a reason
where one is required, then writes the decision row and the new stage
together. Nothing else in the codebase should assign `Candidate.stage`.

The caller commits, so a move and whatever caused it (a booking, an
evaluation) succeed or fail as one.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Candidate, Job
from hiring.models import AuditEvent, DecisionRecord
from org.auth import can_see_job
from org.models import User
from shared.stages import ALLOWED_MOVES, REASON_REQUIRED, Actor, CandidateStage, Role


@dataclass
class StageError(Exception):
    """A refused move. `status` is the HTTP status the API should answer with."""

    status: int
    message: str

    def __str__(self) -> str:
        return self.message


def actor_for(user: User | None) -> Actor:
    if user is None:
        return Actor.SYSTEM
    role = Role(user.role)
    if role is Role.HR:
        return Actor.HR
    if role is Role.BU_HEAD:
        return Actor.BU_HEAD
    raise StageError(403, "Org admins can see every candidate but do not move them.")


async def move(
    session: AsyncSession,
    candidate: Candidate,
    to: CandidateStage | str,
    *,
    by: User | None,
    reason: str | None = None,
    model_recommendation: str | None = None,
) -> DecisionRecord:
    """Move `candidate` to `to`. `by=None` means the system is recording a fact."""
    to = CandidateStage(to)
    frm = CandidateStage(candidate.stage)
    actor = actor_for(by)

    who = ALLOWED_MOVES.get((frm, to))
    if who is None:
        raise StageError(409, f"A candidate who is {frm.value} cannot move to {to.value}.")
    if actor not in who:
        raise StageError(403, f"Moving a candidate from {frm.value} to {to.value} is not yours to do.")

    if by is not None:
        job = await session.get(Job, candidate.job_id)
        if job is None or not can_see_job(by, job):
            # Same answer as a missing candidate: existence in another BU is not
            # something a BU head should be able to learn.
            raise StageError(404, "No such candidate.")

    reason = (reason or "").strip() or None
    if to in REASON_REQUIRED and reason is None:
        raise StageError(422, f"Moving a candidate to {to.value} needs a written reason.")

    record = DecisionRecord(
        org_id=candidate.org_id or (by.org_id if by else None),
        candidate_id=candidate.id,
        from_stage=frm.value,
        to_stage=to.value,
        actor=actor.value,
        actor_user_id=by.id if by else None,
        reason=reason,
        model_recommendation=model_recommendation,
    )
    session.add(record)
    candidate.stage = to.value
    await session.flush()
    return record


async def audit(
    session: AsyncSession,
    action: str,
    *,
    by: User | None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    detail: dict | None = None,
) -> None:
    """Record something a person did that someone may later need to account for."""
    session.add(
        AuditEvent(
            org_id=by.org_id if by else None,
            actor_user_id=by.id if by else None,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            detail=detail,
        )
    )
