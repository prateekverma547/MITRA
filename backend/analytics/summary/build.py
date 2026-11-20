"""The summaries, from existing records only (D_architecture 9: the metric dictionary).

Loading is a fixed number of queries per summary, whatever the number of interviews
(no query per interview): the job, its candidates, their interviews, their events (only
the kinds read here), and the capacity refusals. Columns that hold secrets or words are
never selected (meeting password, transcript, section outcomes, feedback report); the
JSON columns that are read for numbers (session_metrics, cleaned_transcript, the plan)
are reduced to codes, times and numbers before anything leaves this module.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, case, func, select

from analytics.summary import decisions as dec
from analytics.summary.categories import CATEGORIES, categorise
from analytics.summary.shapes import (
    AfterCall,
    AreaRow,
    Areas,
    Basis,
    Connection,
    CostPart,
    Ending,
    Figure,
    InterviewCost,
    InterviewSummary,
    JobList,
    JobListItem,
    JobSummary,
    OperationsSummary,
    PartTotal,
    RecentInterview,
)

#: Event kinds read here. Never transcript_turn, time_note_sent or note_added (words).
EVENT_KINDS = ("interview_ended", "tool_called", "withdrawal", "silence_prompt", "area_changed",
               "question_labelled", "rejoin_limit_reached", "candidate_dropped", "candidate_returned")
PARTS = ("realtime", "auditor", "cleaner", "scoring", "plan_generation", "plan_refinement", "plan_goals", "daily")
#: The plan cost records' job labels (C-158).
GENERATION_JOB, REFINEMENT_JOB, GOALS_JOB = "blueprint", "blueprint_refine", "blueprint_goals"
#: Statuses where no call took place, so the call's parts cannot have a cost.
_NEVER_RAN = {"scheduled", "no_show", "expired"}


@dataclass
class Loaded:
    job: dict | None
    candidates: dict[str, dict]
    interviews: list[dict]
    events: dict[str, list[dict]] = field(default_factory=dict)
    refusals: dict[str, int] = field(default_factory=dict)


# -- loading: a fixed number of queries ---------------------------------------------------------------


async def load(session, *, job_id: str | None = None, interview_id: str | None = None) -> Loaded:
    from app.db import Candidate, Interview, Job
    from live.models import CapacityEventRecord, InterviewEventRecord

    if interview_id is not None:
        row = (await session.execute(
            select(Candidate.job_id).join(Interview, Interview.candidate_id == Candidate.id)
            .where(Interview.id == interview_id))).first()
        if row is None:
            return Loaded(job=None, candidates={}, interviews=[])
        job_id = row[0]
    job = (await session.execute(select(Job.id, Job.title).where(Job.id == job_id))).first()
    if job is None:
        return Loaded(job=None, candidates={}, interviews=[])
    cands = {r.id: dict(r._mapping) for r in await session.execute(
        select(Candidate.id, Candidate.name, Candidate.created_at, Candidate.blueprint, Candidate.text_model_calls)
        .where(Candidate.job_id == job_id))}
    q = (select(Interview.id, Interview.candidate_id, Interview.created_at, Interview.status,
                Interview.failure_reason, Interview.started_at, Interview.ended_at, Interview.session_metrics,
                Interview.cleaned_transcript, Interview.booked_duration_minutes)
         .join(Candidate, Interview.candidate_id == Candidate.id).where(Candidate.job_id == job_id))
    if interview_id is not None:
        q = q.where(Interview.id == interview_id)
    interviews = [dict(r._mapping) for r in await session.execute(q)]
    ids_q = select(Interview.id).join(Candidate, Interview.candidate_id == Candidate.id).where(
        Candidate.job_id == job_id)
    if interview_id is not None:
        ids_q = ids_q.where(Interview.id == interview_id)
    events: dict[str, list[dict]] = {}
    for r in await session.execute(
            select(InterviewEventRecord.interview_id, InterviewEventRecord.event_id, InterviewEventRecord.at_seconds,
                   InterviewEventRecord.source, InterviewEventRecord.kind, InterviewEventRecord.payload)
            .where(and_(InterviewEventRecord.interview_id.in_(ids_q), InterviewEventRecord.kind.in_(EVENT_KINDS)))
            .order_by(InterviewEventRecord.seq)):
        env = r.payload or {}
        inner = env.get("payload") if isinstance(env.get("payload"), dict) else env
        events.setdefault(r.interview_id, []).append(
            {"event_id": r.event_id, "at_seconds": r.at_seconds, "source": r.source, "kind": r.kind, "payload": inner})
    refusals = {r[0]: r[1] for r in await session.execute(
        select(CapacityEventRecord.interview_id, func.count())
        .where(and_(CapacityEventRecord.kind == "refused", CapacityEventRecord.interview_id.in_(ids_q)))
        .group_by(CapacityEventRecord.interview_id))}
    return Loaded(job=dict(job._mapping), candidates=cands, interviews=interviews, events=events, refusals=refusals)


# -- one interview ------------------------------------------------------------------------------------------


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def _calls_part(part: str, calls, ran: bool) -> CostPart:
    """A part made of text-model calls (each with cost_usd, or None when unpriced)."""
    if not isinstance(calls, list) or not calls:
        return CostPart(part=part, status="not_recorded" if ran else "not_applicable")
    priced = [_num(c.get("cost_usd")) for c in calls if isinstance(c, dict)]
    known = [p for p in priced if p is not None]
    unpriced = len(priced) - len(known)
    if not known:
        return CostPart(part=part, status="not_recorded", calls_without_price=unpriced)
    return CostPart(part=part, status="incomplete" if unpriced else "recorded", usd=round(sum(known), 6),
                    calls_without_price=unpriced)


def plan_parts(candidate: dict | None) -> list[CostPart]:
    """Generation (job "blueprint") and refinement (job "blueprint_refine", C-158).
    Refinement with no record: not_applicable when the plan was never refined with HR
    findings; not_recorded when it was (before C-158 its cost was stored as "blueprint",
    so on those rows it is inside generation)."""
    calls = [c for c in ((candidate or {}).get("text_model_calls") or []) if isinstance(c, dict)]
    gen = _calls_part("plan_generation", [c for c in calls if c.get("job") == GENERATION_JOB], True)
    ref_calls = [c for c in calls if c.get("job") == REFINEMENT_JOB]
    refined = isinstance(((candidate or {}).get("blueprint") or {}).get("hr_findings"), dict)
    ref = _calls_part("plan_refinement", ref_calls, refined)
    # Goal writing at booking (round 18): expected when the plan carries any written goal;
    # plans made before goals existed have none, so it does not apply to them.
    plans = (((candidate or {}).get("blueprint") or {}).get("competency_plans") or [])
    has_goals = any(g for p in plans if isinstance(p, dict) for g in (p.get("question_goals") or []))
    goals = _calls_part("plan_goals", [c for c in calls if c.get("job") == GOALS_JOB], has_goals)
    return [gen, ref, goals]


def cost(iv: dict, metrics: dict, candidate: dict | None, plan_here: bool) -> InterviewCost:
    status = iv["status"]
    ran = status not in _NEVER_RAN and metrics.get("ended_because") != "failed_to_start"
    parts: list[CostPart] = []
    # Mitra's realtime model
    usd = _num((metrics.get("usage") or {}).get("estimated_usd")) if isinstance(metrics.get("usage"), dict) else None
    parts.append(CostPart(part="realtime", status="recorded", usd=usd) if usd is not None
                 else CostPart(part="realtime", status="not_recorded" if ran else "not_applicable"))
    # the auditor
    aud = metrics.get("auditor") if isinstance(metrics.get("auditor"), dict) else None
    a_usd = _num(((aud or {}).get("stats") or {}).get("usd"))
    if a_usd is not None:
        parts.append(CostPart(part="auditor", status="recorded", usd=a_usd))
    elif aud is not None and aud.get("status") == "disabled":
        parts.append(CostPart(part="auditor", status="not_applicable"))
    else:
        parts.append(CostPart(part="auditor", status="not_recorded" if ran else "not_applicable"))
    # the cleaner: its own column, else the old place in session_metrics (C-131)
    ct = iv.get("cleaned_transcript") if isinstance(iv.get("cleaned_transcript"), dict) else None
    ct = ct or (metrics.get("cleaned_transcript") if isinstance(metrics.get("cleaned_transcript"), dict) else None)
    c_usd = _num((ct or {}).get("usd"))
    parts.append(CostPart(part="cleaner", status="recorded", usd=c_usd) if c_usd is not None
                 else CostPart(part="cleaner", status="not_recorded" if ran else "not_applicable"))
    # the report's scoring
    calls = [c for c in (metrics.get("text_model_calls") or []) if isinstance(c, dict) and c.get("job") == "feedback"]
    parts.append(_calls_part("scoring", calls, ran))
    # the plan, per candidate, counted on its first interview: generation and refinement
    if not plan_here:
        parts.append(CostPart(part="plan_generation", status="counted_elsewhere"))
        parts.append(CostPart(part="plan_refinement", status="counted_elsewhere"))
        parts.append(CostPart(part="plan_goals", status="counted_elsewhere"))
    else:
        parts += plan_parts(candidate)
    # Daily: not stored anywhere (D-77)
    parts.append(CostPart(part="daily", status="not_recorded" if ran else "not_applicable"))
    recorded = round(sum(p.usd for p in parts if p.status in ("recorded", "incomplete")), 6)
    missing = [p.part for p in parts if p.status in ("not_recorded", "incomplete")]
    return InterviewCost(parts=parts, recorded_usd=recorded, complete=not missing, missing_parts=missing)


def _area_ids(blueprint: dict | None) -> tuple[dict[int, str], dict[str, int]]:
    plans = (blueprint or {}).get("competency_plans") or []
    by_number = {1: "opening", **{i + 2: p.get("competency_id") for i, p in enumerate(plans)},
                 len(plans) + 2: "closing"}
    planned = {p.get("competency_id"): len(p.get("question_ids") or p.get("seed_questions") or []) for p in plans}
    return by_number, planned


def areas(metrics: dict, events: list[dict], blueprint: dict | None) -> Areas:
    by_number, planned = _area_ids(blueprint)
    labels = [e["payload"] for e in events if e["kind"] == "question_labelled"]
    source = "events"
    if not labels:
        aud = metrics.get("auditor") if isinstance(metrics.get("auditor"), dict) else {}
        raw = aud.get("labels") if isinstance(aud.get("labels"), dict) else {}
        labels = [{"label": v.get("label"), "question_id": v.get("question_id"),
                   "area_id": by_number.get(v.get("area"))} for v in raw.values() if isinstance(v, dict)]
        source = "session_metrics" if labels else "none"
    coverage_raw = ((metrics.get("auditor") or {}).get("coverage") or {}) if isinstance(metrics.get("auditor"), dict) else {}
    coverage = {by_number.get(int(k)) if str(k).isdigit() else k: v for k, v in coverage_raw.items()}
    rows: dict[str, AreaRow] = {aid: AreaRow(area_id=aid, planned_questions=n) for aid, n in planned.items()}
    asked: dict[str, set] = {}
    off_plan = 0
    for lab in labels:
        aid, kind = lab.get("area_id"), lab.get("label")
        if kind == "off_plan":
            off_plan += 1
            continue
        if aid in (None, "opening", "closing") or kind in ("opening", "closing"):
            continue
        row = rows.setdefault(aid, AreaRow(area_id=aid))
        if kind == "plan" and lab.get("question_id"):
            asked.setdefault(aid, set()).add(lab["question_id"])
        elif kind == "follow_up":
            row.follow_ups += 1
        elif kind == "clarification":
            row.clarifications += 1
        elif kind == "easier_variant":
            row.easier_variants += 1
    for aid, qs in asked.items():
        rows[aid].plan_questions_asked = len(qs)
    for aid, v in coverage.items():
        if aid in rows and isinstance(v, str):
            rows[aid].coverage = v
    return Areas(labels_from=source, rows=list(rows.values()), off_plan_questions=off_plan,
                 plan_areas=len(planned) if blueprint else None,
                 plan_areas_with_a_plan_question=sum(1 for aid in planned if asked.get(aid)))


def connection(metrics: dict, events: list[dict]) -> Connection:
    rec = metrics.get("connection") if isinstance(metrics.get("connection"), dict) else None
    if rec is not None and isinstance(rec.get("drops"), list):
        gaps = [_num(d.get("gap_seconds")) for d in rec["drops"] if isinstance(d, dict)]
        gaps = [g for g in gaps if g is not None]
        return Connection(recorded=True, drops=len(rec["drops"]), counted_rejoins=rec.get("rejoin_count"),
                          longest_gap_seconds=max(gaps) if gaps else None, limit_reached=rec.get("limit_reached"))
    dropped = [e for e in events if e["kind"] == "candidate_dropped"]
    returned = [e for e in events if e["kind"] == "candidate_returned"]
    if dropped or returned:
        gaps = [_num(e["payload"].get("gap_seconds")) for e in returned]
        gaps = [g for g in gaps if g is not None]
        return Connection(recorded=True, drops=len(dropped),
                          counted_rejoins=sum(1 for e in returned if e["payload"].get("counted")),
                          longest_gap_seconds=max(gaps) if gaps else None,
                          limit_reached=any(e["kind"] == "rejoin_limit_reached" for e in events))
    return Connection(recorded=False)


def after_call(interview_id: str) -> AfterCall:
    from aftercall import settings, store

    try:
        status, signal = store.read_status(interview_id), store.read_signal(interview_id)
    except Exception:  # noqa: BLE001  (an unreadable result shows as none, as C's report does)
        status, signal = None, None
    return AfterCall(enabled_now=settings.enabled(),
                     checks={c.check.value: c.state.value for c in status.checks} if status else None,
                     second_voice_ranges=len(signal.ranges) if signal else (0 if status else None))


def summarise_interview(iv: dict, data: Loaded, plan_here: bool) -> InterviewSummary:
    metrics = iv.get("session_metrics") if isinstance(iv.get("session_metrics"), dict) else {}
    events = data.events.get(iv["id"], [])
    cand = data.candidates.get(iv["candidate_id"])
    blueprint = (cand or {}).get("blueprint") if isinstance((cand or {}).get("blueprint"), dict) else None
    status = str(iv["status"].value if hasattr(iv["status"], "value") else iv["status"])
    end_reason = metrics.get("ended_because") if isinstance(metrics.get("ended_because"), str) else None
    ending = metrics.get("ending") if isinstance(metrics.get("ending"), dict) else {}
    category, cat_from = categorise(status, end_reason)
    ended_ev = [e for e in events if e["kind"] == "interview_ended"]
    duration, dfrom = None, "not_recorded"
    if status not in ("scheduled", "in_progress") and ended_ev and _num(ended_ev[-1]["payload"].get("duration_seconds")) is not None:
        duration, dfrom = round(float(ended_ev[-1]["payload"]["duration_seconds"]), 2), "interview_ended_event"
    elif status not in ("scheduled", "in_progress") and iv.get("started_at") and iv.get("ended_at"):
        a, b = _aware(iv["started_at"]), _aware(iv["ended_at"])
        if b >= a:
            duration, dfrom = round((b - a).total_seconds(), 2), "timestamps"
    return InterviewSummary(
        interview_id=iv["id"], job_id=data.job["id"], candidate_id=iv["candidate_id"],
        candidate_name=(cand or {}).get("name"), created_at=iv.get("created_at"), started_at=iv.get("started_at"),
        ended_at=iv.get("ended_at"), duration_seconds=duration, duration_from=dfrom,
        booked_minutes=iv.get("booked_duration_minutes"),
        ending=Ending(status=status, end_reason=end_reason, ending=ending.get("ending"),
                      hang_up_by=ending.get("hang_up_by"), category=category, category_from=cat_from),
        areas=areas(metrics, events, blueprint), connection=connection(metrics, events),
        after_call=after_call(iv["id"]), decisions=dec.build(status, metrics, events, blueprint),
        cost=cost(iv, metrics, cand, plan_here), capacity_refusals=data.refusals.get(iv["id"], 0),
    )


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _plan_owner(interviews: list[dict]) -> dict[str, str]:
    """Each candidate's plan cost is counted on its first interview (by creation, then id)."""
    first: dict[str, tuple] = {}
    for iv in interviews:
        key = (_aware(iv["created_at"]) if iv.get("created_at") else datetime.max.replace(tzinfo=UTC), iv["id"])
        if iv["candidate_id"] not in first or key < first[iv["candidate_id"]]:
            first[iv["candidate_id"]] = key
    return {cid: k[1] for cid, k in first.items()}


# -- one job -------------------------------------------------------------------------------------------------


def summarise_job(data: Loaded, *, include_rows: bool = True) -> JobSummary:
    owners = _plan_owner(data.interviews)
    rows = [summarise_interview(iv, data, owners.get(iv["candidate_id"]) == iv["id"]) for iv in data.interviews]
    n = len(rows)
    by_status: dict[str, int] = {}
    by_category = {c: 0 for c in CATEGORIES}
    for r in rows:
        by_status[r.ending.status] = by_status.get(r.ending.status, 0) + 1
        by_category[r.ending.category] = by_category.get(r.ending.category, 0) + 1

    # average length: completed interviews with a recorded duration
    lengths, excl = [], {}
    for r in rows:
        if r.ending.status != "completed":
            excl[f"status_{r.ending.status}"] = excl.get(f"status_{r.ending.status}", 0) + 1
        elif r.duration_seconds is None:
            excl["duration_not_recorded"] = excl.get("duration_not_recorded", 0) + 1
        else:
            lengths.append(r.duration_seconds)
    avg = Figure(value=round(statistics.fmean(lengths), 2) if lengths else None, unit="seconds",
                 basis=Basis(counted=len(lengths), excluded=excl))

    # cost: the sum of the interviews' recorded parts, never a guess
    total = round(sum(r.cost.recorded_usd for r in rows), 6)
    with_cost = [r for r in rows if r.cost.recorded_usd > 0 or any(p.status in ("recorded", "incomplete")
                                                                    for p in r.cost.parts)]
    incomplete = sum(1 for r in rows if not r.cost.complete and r in with_cost)
    cost_excl = {"no_cost_recorded": n - len(with_cost)} if n - len(with_cost) else {}
    total_fig = Figure(value=total if with_cost else None, unit="usd",
                       basis=Basis(counted=len(with_cost), excluded=cost_excl))
    per = Figure(value=round(total / len(with_cost), 6) if with_cost else None, unit="usd",
                 basis=Basis(counted=len(with_cost), excluded={**cost_excl, **(
                     {"counted_but_incomplete": incomplete} if incomplete else {})}))
    by_part = []
    for part in PARTS:
        ps = [next(p for p in r.cost.parts if p.part == part) for r in rows]
        by_part.append(PartTotal(
            part=part, usd=round(sum(p.usd for p in ps if p.status in ("recorded", "incomplete")), 6),
            interviews_recorded=sum(1 for p in ps if p.status == "recorded"),
            interviews_missing=sum(1 for p in ps if p.status == "not_recorded"),
            interviews_incomplete=sum(1 for p in ps if p.status == "incomplete")))

    # plan costs of candidates without an interview (not in the total)
    lone = [c for cid, c in data.candidates.items() if cid not in owners]
    lone_usd, lone_missing = 0.0, 0
    for c in lone:
        ps = [p for p in plan_parts(c) if p.usd is not None]
        if not ps:
            lone_missing += 1
        else:
            lone_usd += sum(p.usd for p in ps)
    lone_fig = Figure(value=round(lone_usd, 6) if len(lone) > lone_missing else None, unit="usd",
                      basis=Basis(counted=len(lone) - lone_missing,
                                  excluded={"plan_cost_not_recorded": lone_missing} if lone_missing else {}))
    refusals = Figure(value=float(sum(r.capacity_refusals for r in rows)), unit="count", basis=Basis(counted=n))
    ac_rows = [r for r in rows if r.after_call.checks is not None]
    signals = Figure(value=float(sum(1 for r in ac_rows if (r.after_call.second_voice_ranges or 0) > 0))
                     if ac_rows else None, unit="interviews",
                     basis=Basis(counted=len(ac_rows), excluded={"after_call_not_run": n - len(ac_rows)}
                                 if n - len(ac_rows) else {}))
    owners_count: dict[str, int] = {}
    for r in rows:
        for d in r.decisions:
            owners_count[d.owner] = owners_count.get(d.owner, 0) + 1
    return JobSummary(job_id=data.job["id"], title=data.job.get("title"), generated_at=datetime.now(UTC),
                      interviews=n, by_status=by_status, by_category=by_category, average_length_seconds=avg,
                      total_cost_usd=total_fig, cost_per_interview_usd=per, cost_by_part=by_part,
                      plan_cost_without_interview_usd=lone_fig, capacity_refusals=refusals,
                      after_call_signals=signals, decisions_by_owner=owners_count,
                      interview_rows=rows if include_rows else None)


async def job_summary(session, job_id: str, include_rows: bool = True) -> JobSummary | None:
    data = await load(session, job_id=job_id)
    return None if data.job is None else summarise_job(data, include_rows=include_rows)


async def interview_summary(session, interview_id: str) -> InterviewSummary | None:
    from app.db import Candidate, Interview

    data = await load(session, interview_id=interview_id)
    if data.job is None or not data.interviews:
        return None
    # whether this interview carries its candidate's plan cost needs the candidate's other interviews
    iv = data.interviews[0]
    first = (await session.execute(
        select(Interview.id).join(Candidate, Interview.candidate_id == Candidate.id)
        .where(Interview.candidate_id == iv["candidate_id"]).order_by(Interview.created_at, Interview.id)
        .limit(1))).scalar()
    return summarise_interview(iv, data, first == iv["id"])


async def operations_summary(session) -> OperationsSummary:
    from app.db import Interview
    from live.models import CapacityEventRecord

    refused = (await session.execute(select(func.count(), func.sum(
        case((CapacityEventRecord.interview_id.is_(None), 1), else_=0)))
        .where(CapacityEventRecord.kind == "refused"))).first()
    agg = (await session.execute(select(func.max(CapacityEventRecord.live_bots), func.max(CapacityEventRecord.limit),
                                        func.min(CapacityEventRecord.at), func.max(CapacityEventRecord.at)))).first()
    by_cat = {c: 0 for c in CATEGORIES}
    for status, metrics in await session.execute(select(Interview.status, Interview.session_metrics)):
        s = str(status.value if hasattr(status, "value") else status)
        reason = (metrics or {}).get("ended_because") if isinstance(metrics, dict) else None
        cat, _ = categorise(s, reason if isinstance(reason, str) else None)
        by_cat[cat] += 1
    return OperationsSummary(generated_at=datetime.now(UTC), capacity_refusals_total=int(refused[0] or 0),
                             capacity_refusals_without_interview=int(refused[1] or 0), peak_live_bots=agg[0],
                             bot_limit_seen=agg[1], window_start=agg[2], window_end=agg[3],
                             interviews_by_category=by_cat)


#: The most recent interviews the job list can carry (?recent=N).
RECENT_MAX = 50


async def job_list(session, recent: int = 0) -> JobList:
    """The console home: jobs that have interviews, newest first (one query), and, if asked,
    the most recent interviews across all jobs (one more query, at most RECENT_MAX rows).
    "Newest" is the interview's start, else its booking time. Reads ids, titles, names,
    statuses and times; the session_metrics of the recent rows only for the end reason."""
    from app.db import Candidate, Interview, Job

    latest = func.max(func.coalesce(Interview.started_at, Interview.created_at))
    rows = await session.execute(
        select(Job.id, Job.title, func.count(Interview.id), latest)
        .join(Candidate, Candidate.job_id == Job.id).join(Interview, Interview.candidate_id == Candidate.id)
        .group_by(Job.id, Job.title).order_by(latest.desc(), Job.id))
    jobs = [JobListItem(job_id=r[0], title=r[1], interviews=r[2], latest_interview_at=r[3]) for r in rows]
    recent_rows = None
    if recent > 0:
        when = func.coalesce(Interview.started_at, Interview.created_at)
        recent_rows = []
        for r in await session.execute(
                select(Interview.id, Candidate.job_id, Candidate.name, Interview.status, Interview.session_metrics,
                       Interview.started_at, Interview.ended_at, Interview.created_at)
                .join(Candidate, Interview.candidate_id == Candidate.id)
                .order_by(when.desc(), Interview.id).limit(min(recent, RECENT_MAX))):
            status = str(r.status.value if hasattr(r.status, "value") else r.status)
            reason = r.session_metrics.get("ended_because") if isinstance(r.session_metrics, dict) else None
            reason = reason if isinstance(reason, str) else None
            recent_rows.append(RecentInterview(
                interview_id=r.id, job_id=r.job_id, candidate_name=r.name, status=status, end_reason=reason,
                category=categorise(status, reason)[0], started_at=r.started_at, ended_at=r.ended_at,
                created_at=r.created_at))
    return JobList(generated_at=datetime.now(UTC), jobs=jobs, recent_interviews=recent_rows)
