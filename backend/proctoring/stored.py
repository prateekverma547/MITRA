"""The STORED integrity report (Session D, Track H integration, D-93).

Until now the integrity report was worked out on every read (report.assemble). Track H's
console reads it as a record, so it is stored:
- written when the interview ends (the start-up loop, aftercall/runner.py `run_forever`,
  calls `write_pending` every pass, whatever the after-call switch);
- rewritten when something it depends on arrives later: an event received after it was
  built (the intake's late grace), or an after-call result written after it was built;
- served as stored (routes.py `GET .../integrity`): never worked out on read.
Codes only; signals for review, never a score; no password, room or transcript in it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select

from app.db import Interview, InterviewStatus, get_sessionmaker
from proctoring import report as report_mod
from proctoring.intake import LATE_GRACE_SECONDS
from proctoring.models import IntegrityEventRecord, IntegrityReportRecord
from shared.interview_contracts import (
    IntegrityRange,
    IntegrityReportStatus,
    IntegrityTypeTotal,
    StoredIntegrityReport,
)

#: Interviews that never ran: nothing to report.
NEVER_RAN = {InterviewStatus.SCHEDULED.value, InterviewStatus.IN_PROGRESS.value, InterviewStatus.EXPIRED.value,
             InterviewStatus.NO_SHOW.value}


def _aware(d: datetime | None) -> datetime | None:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=UTC))


def _status_code(s) -> str:
    return str(s.value if hasattr(s, "value") else s)


def by_type(events) -> list[IntegrityTypeTotal]:
    groups: dict = {}
    for e in events:
        groups.setdefault(e.kind, []).append(e)
    out = []
    for kind, es in sorted(groups.items(), key=lambda kv: kv[0].value):
        closed = [e for e in es if e.end_seconds is not None]
        out.append(IntegrityTypeTotal(
            kind=kind, count=len(es),
            total_seconds=round(sum(e.end_seconds - e.start_seconds for e in closed), 3),
            open_events=len(es) - len(closed),
            ranges=[IntegrityRange(event_id=e.event_id, source=e.source, start_seconds=e.start_seconds,
                                   end_seconds=e.end_seconds, confidence=e.confidence)
                    for e in sorted(es, key=lambda x: (x.start_seconds, x.event_id))]))
    return out


def _report_status(interview, status, now: datetime) -> IntegrityReportStatus:
    from aftercall import settings as ac_settings

    ended = _aware(interview.ended_at)
    if ended is not None and (now - ended).total_seconds() <= LATE_GRACE_SECONDS:
        return IntegrityReportStatus.AWAITING_LATE_EVENTS
    if ac_settings.enabled() and (status is None or any(c.state.value in ("pending", "running") for c in status.checks)):
        return IntegrityReportStatus.AWAITING_AFTER_CALL
    return IntegrityReportStatus.FINAL


async def build(interview_id: str, now: datetime | None = None) -> StoredIntegrityReport | None:
    """The report for an interview that has ended; None for one that has not (or never ran)."""
    now = now or datetime.now(UTC)
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
    if interview is None or _status_code(interview.status) in NEVER_RAN:
        return None
    full = await report_mod.assemble(interview_id)
    return StoredIntegrityReport(
        interview_id=interview_id, status=_report_status(interview, full.after_call_status, now), built_at=now,
        interview_status=_status_code(interview.status), by_type=by_type(full.events),
        after_call_status=full.after_call_status, second_voice=full.second_voice, report=full)


async def store(interview_id: str, now: datetime | None = None) -> StoredIntegrityReport | None:
    out = await build(interview_id, now)
    if out is None:
        return None
    async with get_sessionmaker()() as session:
        row = await session.get(IntegrityReportRecord, interview_id)
        if row is None:
            row = IntegrityReportRecord(interview_id=interview_id)
            session.add(row)
        row.status, row.built_at, row.report = out.status.value, out.built_at, out.model_dump(mode="json")
        await session.commit()
    logger.info(f"[{interview_id}] integrity report stored ({out.status.value}, {len(out.report.events)} events)")
    return out


async def load(interview_id: str) -> StoredIntegrityReport | None:
    async with get_sessionmaker()() as session:
        row = await session.get(IntegrityReportRecord, interview_id)
    return None if row is None else StoredIntegrityReport.model_validate(row.report)


async def pending() -> list[str]:
    """Ended interviews whose stored report is missing, not final, or older than something it
    depends on (an event received, an after-call status written). Three queries."""
    from aftercall import store as ac_store

    async with get_sessionmaker()() as session:
        ended = [r[0] for r in await session.execute(
            select(Interview.id).where(Interview.status.notin_(NEVER_RAN)))]
        if not ended:
            return []
        rows = {r[0]: (r[1], r[2]) for r in await session.execute(
            select(IntegrityReportRecord.interview_id, IntegrityReportRecord.built_at, IntegrityReportRecord.status)
            .where(IntegrityReportRecord.interview_id.in_(ended)))}
        latest = {r[0]: r[1] for r in await session.execute(
            select(IntegrityEventRecord.interview_id, func.max(IntegrityEventRecord.received_at))
            .where(IntegrityEventRecord.interview_id.in_(ended)).group_by(IntegrityEventRecord.interview_id))}
    out = []
    for iid in ended:
        if iid not in rows:
            out.append(iid)
            continue
        built, status = _aware(rows[iid][0]), rows[iid][1]
        if status != IntegrityReportStatus.FINAL.value:
            out.append(iid)
            continue
        if latest.get(iid) is not None and _aware(latest[iid]) > built:
            out.append(iid)
            continue
        try:
            ac = ac_store.read_status(iid)
        except Exception:  # noqa: BLE001  (an unreadable result: the report shows none)
            ac = None
        if ac is not None and ac.updated_at > built:
            out.append(iid)
    return out


async def write_pending() -> int:
    n = 0
    for iid in await pending():
        try:
            if await store(iid) is not None:
                n += 1
        except Exception as exc:  # noqa: BLE001  (one interview's problem never stops the others)
            logger.error(f"[{iid}] integrity report not stored ({type(exc).__name__})")
    return n
