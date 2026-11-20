"""The rejoin pass (phase 3, wave 1).

After a fatal connection error the candidate app rejoins by itself: it swaps
the pass it was given at join (held in memory only, never stored by the app)
for a fresh candidate meeting token for the SAME room. The meeting password is
never involved and never in a pass.

A pass is a purpose-bound signed token (live/tokens.py, purpose "rejoin",
subject "<interview id>:<pass id>"), plus a row that makes it single-use and
binds it to the interview's candidate. It expires at the interview's hard stop
plus REJOIN_PASS_MARGIN_MINUTES, never after the room. Each exchange uses the
pass up and returns the next one.

Refusals carry a reason code: invalid_pass (wrong, expired or used: one
answer), interview_ended, rejoin_limit_reached (the bot's own
rejoin_limit_reached event: the bot decides counting), interviewer_gone (no
bot alive here), rate_limited. Rate limits reuse the credential limits
(live/credential_limits.py), keyed by the interview.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from app.db import Candidate, Interview, InterviewStatus, get_sessionmaker
from live import tokens
from live.models import InterviewEventRecord, RejoinPassRecord
from live.watch import hard_stop_minutes
from shared.contracts import REJOIN_LIMIT

PURPOSE = "rejoin"
REJOIN_PASS_MARGIN_MINUTES = 10


class RejoinRefused(Exception):
    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status, self.reason = status, reason


@dataclass(frozen=True)
class IssuedPass:
    value: str
    expires_at: datetime


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def pass_expiry(interview: Interview, blueprint: dict | None, now: datetime) -> datetime:
    """The hard stop plus the margin, never after the room's own expiry."""
    started = _aware(interview.started_at) or now
    expires = started + timedelta(minutes=hard_stop_minutes(interview, blueprint) + REJOIN_PASS_MARGIN_MINUTES)
    room = _aware(interview.room_expires_at)
    return min(expires, room) if room is not None else expires


async def issue_pass(interview: Interview, *, now: datetime | None = None) -> IssuedPass:
    now = now or datetime.now(UTC)
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, interview.candidate_id)
        expires = pass_expiry(interview, candidate.blueprint if candidate else None, now)
        pass_id = secrets.token_hex(12)
        value, _ = tokens.issue(PURPOSE, f"{interview.id}:{pass_id}", max(1, int((expires - now).total_seconds())))
        session.add(RejoinPassRecord(pass_id=pass_id, interview_id=interview.id, candidate_id=interview.candidate_id,
                                     issued_at=now, expires_at=expires))
        await session.commit()
    return IssuedPass(value=value, expires_at=expires)


async def rejoin_counts(interview_id: str) -> tuple[int, bool]:
    """(counted rejoins, limit reached), from the bot's own events."""
    async with get_sessionmaker()() as session:
        rows = list(await session.scalars(
            select(InterviewEventRecord.payload).where(
                InterviewEventRecord.interview_id == interview_id,
                InterviewEventRecord.kind.in_(["candidate_returned", "rejoin_limit_reached"]))))
    counted = sum(1 for r in rows if r.get("kind") == "candidate_returned" and r["payload"].get("counted"))
    limit = any(r.get("kind") == "rejoin_limit_reached" for r in rows)
    return counted, limit


async def use_pass(interview_id: str, value: str, *, now: datetime | None = None) -> tuple[Interview, Candidate | None]:
    """Check a pass and use it up. Raises RejoinRefused; returns the interview."""
    from app.capacity import registry

    now = now or datetime.now(UTC)
    subject = tokens.verify(value, PURPOSE)
    if subject is None or ":" not in subject or subject.split(":", 1)[0] != interview_id:
        raise RejoinRefused(401, "invalid_pass", "This rejoin pass is not valid.")
    pass_id = subject.split(":", 1)[1]
    async with get_sessionmaker()() as session:
        record = await session.get(RejoinPassRecord, pass_id)
        interview = await session.get(Interview, interview_id)
        if record is None or record.used_at is not None or interview is None \
                or record.candidate_id != interview.candidate_id or _aware(record.expires_at) < now:
            raise RejoinRefused(401, "invalid_pass", "This rejoin pass is not valid.")
        if interview.status != InterviewStatus.IN_PROGRESS:
            from app.startup import was_interrupted

            if was_interrupted(interview):
                raise RejoinRefused(410, "interrupted_by_restart", "This interview was interrupted by a problem on "
                                    "our side (our server restarted) and can't continue.")
            raise RejoinRefused(410, "interview_ended", "This interview has ended.")
        _, limit_reached = await rejoin_counts(interview_id)
        if limit_reached:
            raise RejoinRefused(409, "rejoin_limit_reached", "This interview has ended: the rejoin limit was reached.")
        if not registry.is_running(interview_id):
            raise RejoinRefused(409, "interviewer_gone", "This interview is no longer running.")
        record.used_at = now
        await session.commit()
        candidate = await session.get(Candidate, interview.candidate_id)
        logger.info(f"[{interview_id}] rejoin pass used")  # never the pass
        return interview, candidate


async def issue_pass_for(interview_id: str) -> IssuedPass | None:
    """A pass for this interview as it stands now (after the bot was started, so
    the hard stop is known). None if the interview is gone."""
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
    return None if interview is None else await issue_pass(interview)


#: The join route's answers for a reload into a running interview (wave 1).
INTERVIEWER_GONE_DETAIL = ("This interview can't be rejoined: the interviewer has left, so it has ended. "
                           "Please contact the person who invited you.")
LIMIT_REACHED_DETAIL = ("This interview has ended: the connection dropped more times than an interview allows. "
                        "Please contact the person who invited you.")


async def reload_refusal(interview_id: str) -> tuple[int, str, str] | None:
    """For a candidate re-entering the credentials while the interview runs:
    (status, reason, detail) when it cannot be rejoined, else None. Never
    starts anything: the bot already in the room is the only one."""
    from app.capacity import registry

    if not registry.is_running(interview_id):
        return 409, "interviewer_gone", INTERVIEWER_GONE_DETAIL
    _, limit_reached = await rejoin_counts(interview_id)
    if limit_reached:
        return 409, "rejoin_limit_reached", LIMIT_REACHED_DETAIL
    return None
