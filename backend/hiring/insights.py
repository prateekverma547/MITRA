"""What the console's dashboards, directory and search read. Track H.

Everything here is already scoped (`org.auth.job_scope`): a BU head's numbers
are their BU's, HR's are every approved job's, an admin's are everything. A
dashboard never filters for safety in the browser.

Stages are grouped for display into the six steps of the journey, in order:
screening, HR round, technical, with the BU head, shortlisted, closed. The
groups are what the charts show; the detail is one click away.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Query
from sqlalchemy import or_, select

from app.db import Candidate, Job, get_sessionmaker
from hiring.api import router
from hiring.models import DecisionRecord, Screening
from org.auth import current_user, job_scope
from org.models import BusinessUnit, User
from shared.screening import FitAnalysis
from shared.stages import CandidateStage as S
from shared.stages import Role

GROUPS: list[tuple[str, str, tuple[S, ...]]] = [
    ("screening", "Screening", (S.NEW, S.SCREENED)),
    ("hr", "HR round", (S.READY_FOR_HR, S.HR_BOOKED, S.HR_DONE)),
    ("technical", "Technical", (S.READY_FOR_TECHNICAL, S.TECHNICAL_BOOKED, S.TECHNICAL_DONE)),
    ("bu_head", "With BU head", (S.WITH_BU_HEAD, S.ON_HOLD)),
    ("shortlisted", "Shortlisted", (S.SHORTLISTED,)),
    ("closed", "Closed", (S.REJECTED, S.PARKED, S.WITHDREW)),
]
GROUP_OF = {stage.value: key for key, _, stages in GROUPS for stage in stages}

#: Who has to act next, by stage. Drives "Needs you" and the directory filter.
WAITS_ON: dict[str, tuple[Role, str]] = {
    S.SCREENED.value: (Role.HR, "Decide after screening"),
    S.READY_FOR_HR.value: (Role.HR, "Book the HR round"),
    S.HR_BOOKED.value: (Role.HR, "Run the HR round"),
    S.HR_DONE.value: (Role.HR, "Decide after the HR round"),
    S.READY_FOR_TECHNICAL.value: (Role.HR, "Book the technical round"),
    S.TECHNICAL_DONE.value: (Role.HR, "Send to the BU head"),
    S.WITH_BU_HEAD.value: (Role.BU_HEAD, "Make the final decision"),
    S.ON_HOLD.value: (Role.BU_HEAD, "Decide on hold"),
}

WEEKS = 8


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _week_starts(now: datetime) -> list[datetime]:
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return [monday - timedelta(weeks=i) for i in range(WEEKS - 1, -1, -1)]


def _bucket(weeks: list[datetime], at: datetime | None) -> int | None:
    if at is None or at < weeks[0]:
        return None
    for i in range(len(weeks) - 1, -1, -1):
        if at >= weeks[i]:
            return i
    return None


async def _rows(session, user: User, bu: str | None):
    """Candidates in scope with their job, BU, screening and last decision time."""
    query = (
        select(Candidate, Job, Screening)
        .join(Job, Candidate.job_id == Job.id)
        .outerjoin(Screening, Screening.candidate_id == Candidate.id)
        .where(job_scope(user))
    )
    if bu:
        query = query.where(Job.bu_id == bu)
    rows = (await session.execute(query)).all()
    ids = [c.id for c, _, _ in rows]
    last: dict[str, datetime] = {}
    if ids:
        for cid, at in (
            await session.execute(
                select(DecisionRecord.candidate_id, DecisionRecord.created_at).where(DecisionRecord.candidate_id.in_(ids))
            )
        ).all():
            at = _utc(at)
            if at and (cid not in last or at > last[cid]):
                last[cid] = at
    return rows, last


def _fit(screening: Screening | None) -> FitAnalysis | None:
    if screening is None or not screening.fit_analysis:
        return None
    try:
        return FitAnalysis.model_validate(screening.fit_analysis)
    except Exception:  # noqa: BLE001 - an old row must not break a dashboard
        return None


def _needs(role: Role, candidate: Candidate, screening: Screening | None) -> str | None:
    if candidate.stage == S.NEW.value and role is Role.HR:
        if screening is None or screening.status in ("waiting", "failed"):
            return "Confirm the CV"
        return None
    waits = WAITS_ON.get(candidate.stage)
    return waits[1] if waits and waits[0] is role else None


async def _bus(session, user: User) -> list[BusinessUnit]:
    query = select(BusinessUnit).where(BusinessUnit.org_id == user.org_id).order_by(BusinessUnit.name)
    if Role(user.role) is Role.BU_HEAD:
        query = query.where(BusinessUnit.id == user.bu_id)
    return list((await session.execute(query)).scalars())


# ---- analytics helpers ----------------------------------------------------------------------------

#: The journey's forward steps, in order. "Closed" is an outcome, not a step.
STEPS = ("screening", "hr", "technical", "bu_head", "shortlisted")
STEP_INDEX = {key: i for i, key in enumerate(STEPS)}
AGE_BUCKETS = ((0, 2, "0–2 days"), (3, 6, "3–6 days"), (7, None, "A week or more"))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _furthest_step(candidate: Candidate, history: list[DecisionRecord]) -> int:
    """The furthest forward step this candidate ever reached, closed or not."""
    stages = [candidate.stage] + [d.from_stage for d in history] + [d.to_stage for d in history]
    reached = [STEP_INDEX[g] for g in (GROUP_OF.get(st) for st in stages) if g in STEP_INDEX]
    return max(reached, default=0)


def _spans(candidate: Candidate, history: list[DecisionRecord]) -> dict[str, float]:
    """Days spent in each step this candidate has finished, from their decision history."""
    spent: dict[str, float] = {}
    group = "screening"
    entered = _utc(candidate.created_at)
    for d in sorted(history, key=lambda d: (_utc(d.created_at) or datetime.min.replace(tzinfo=UTC), d.id or 0)):
        at = _utc(d.created_at)
        to_group = GROUP_OF.get(d.to_stage, group)
        if to_group != group:
            if entered and at and group in STEP_INDEX and group != "shortlisted":
                spent[group] = spent.get(group, 0.0) + max(0.0, (at - entered).total_seconds() / 86400)
            group, entered = to_group, at
    return spent


# ---- the dashboard ------------------------------------------------------------------------------


@router.get("/overview")
async def overview(
    bu: str | None = Query(None, description="Limit to one business unit."),
    days: int = Query(90, ge=0, le=3650, description="Activity window; 0 means all time."),
    user: User = Depends(current_user),
) -> dict:
    now = datetime.now(UTC)
    since = now - timedelta(days=days) if days else None
    role = Role(user.role)
    async with get_sessionmaker()() as session:
        bus = await _bus(session, user)
        rows, last = await _rows(session, user, bu)
        jobs_query = select(Job).where(job_scope(user))
        if bu:
            jobs_query = jobs_query.where(Job.bu_id == bu)
        jobs = list((await session.execute(jobs_query)).scalars())
        cand_ids = [c.id for c, _, _ in rows]
        decisions = []
        if cand_ids:
            decisions = (
                await session.execute(
                    select(DecisionRecord).where(DecisionRecord.candidate_id.in_(cand_ids))
                    .order_by(DecisionRecord.created_at.desc(), DecisionRecord.id.desc())
                )
            ).scalars().all()
        actor_names = {
            u.id: u.name
            for u in (
                await session.execute(
                    select(User).where(User.id.in_({d.actor_user_id for d in decisions if d.actor_user_id}))
                )
            ).scalars()
        } if decisions else {}

    bu_names = {b.id: b.name for b in bus}
    by_id = {c.id: (c, j, s) for c, j, s in rows}
    weeks = _week_starts(now)

    # Pipeline: every candidate now, by group.
    pipeline = Counter(GROUP_OF.get(c.stage, "screening") for c, _, _ in rows)

    # Per business unit.
    per_bu: dict[str | None, Counter] = defaultdict(Counter)
    open_jobs_bu: Counter = Counter()
    for c, j, _ in rows:
        per_bu[j.bu_id][GROUP_OF.get(c.stage, "screening")] += 1
    for j in jobs:
        if j.request_status == "open":
            open_jobs_bu[j.bu_id] += 1

    # Needs you, oldest wait first.
    needs = []
    for c, j, s in rows:
        action = _needs(role, c, s)
        if action:
            since_at = last.get(c.id) or _utc(c.created_at) or now
            needs.append({
                "candidate_id": c.id, "name": c.name, "job_id": j.id, "job_title": j.title,
                "bu_name": bu_names.get(j.bu_id, j.business_unit), "stage": c.stage, "action": action,
                "waiting_days": max(0, (now - since_at).days),
            })
    needs.sort(key=lambda n: -n["waiting_days"])

    # Weekly flow: candidates added, and decisions people made.
    added = [0] * WEEKS
    decided = [0] * WEEKS
    for c, _, _ in rows:
        i = _bucket(weeks, _utc(c.created_at))
        if i is not None:
            added[i] += 1
    for d in decisions:
        if d.actor != "system":
            i = _bucket(weeks, _utc(d.created_at))
            if i is not None:
                decided[i] += 1

    # Screening scores, in tens, inside the window.
    histogram = [0] * 10
    screened = passed = 0
    threshold = None
    for c, _, s in rows:
        fit = _fit(s)
        at = _utc(s.finished_at) if s else None
        if fit is None or (since and at and at < since):
            continue
        screened += 1
        passed += fit.passes_screen
        threshold = fit.threshold_percent
        histogram[min(9, int(fit.score_percent // 10))] += 1

    # Recent activity.
    activity = []
    for d in decisions[:14]:
        c, j, _ = by_id.get(d.candidate_id, (None, None, None))
        if c is None:
            continue
        activity.append({
            "at": _utc(d.created_at).isoformat() if d.created_at else None,
            "actor": "MITRA" if d.actor == "system" else actor_names.get(d.actor_user_id, d.actor.replace("_", " ").title()),
            "candidate_id": c.id, "candidate": c.name, "job_title": j.title, "to": d.to_stage, "reason": d.reason,
        })

    active = sum(n for g, n in pipeline.items() if g not in ("closed", "shortlisted"))

    history: dict[str, list[DecisionRecord]] = defaultdict(list)
    for d in decisions:
        history[d.candidate_id].append(d)

    def in_window(c: Candidate) -> bool:
        created = _utc(c.created_at)
        return not since or (created is not None and created >= since)

    # Ageing of what waits on this person, step by step (HR and BU heads).
    ageing_rows: dict[str, dict] = {}
    for n in needs:
        row = ageing_rows.setdefault(n["action"], {"action": n["action"], "stage": n["stage"], "days": []})
        row["days"].append(n["waiting_days"])
    order = [a for _, a in WAITS_ON.values()]
    ageing = []
    for row in sorted(ageing_rows.values(), key=lambda r: order.index(r["action"]) if r["action"] in order else -1):
        days_list = row.pop("days")
        row["buckets"] = [
            sum(1 for d in days_list if d >= lo and (hi is None or d <= hi)) for lo, hi, _ in AGE_BUCKETS
        ]
        row["median_days"] = _median(days_list)
        row["count"] = len(days_list)
        ageing.append(row)

    # Each job: where its candidates stand and how strong the field is.
    per_job: dict[str, dict] = {}
    for j in jobs:
        created = _utc(j.created_at)
        per_job[j.id] = {
            "id": j.id, "title": j.title or "Untitled job", "bu_name": bu_names.get(j.bu_id, j.business_unit),
            "status": j.request_status, "open_days": max(0, (now - created).days) if created else None,
            "groups": {k: 0 for k, _, _ in GROUPS}, "total": 0, "scores": [], "threshold": None,
            "waiting_on_you": 0,
        }
    for c, j, s in rows:
        job = per_job.get(j.id)
        if job is None:
            continue
        job["groups"][GROUP_OF.get(c.stage, "screening")] += 1
        job["total"] += 1
        job["waiting_on_you"] += bool(_needs(role, c, s))
        fit = _fit(s)
        if fit is not None:
            job["threshold"] = fit.threshold_percent
            job["scores"].append({
                "candidate_id": c.id, "name": c.name, "score": round(fit.score_percent, 1),
                "enough_evidence": fit.enough_evidence, "group": GROUP_OF.get(c.stage, "screening"),
            })
    job_rows = sorted(
        per_job.values(),
        key=lambda r: (r["status"] != "open", -r["waiting_on_you"], -r["total"], r["title"].lower()),
    )

    # How far candidates get, and how long each step takes (candidates added in the window).
    windowed = [c for c, _, _ in rows if in_window(c)]
    reached = [0] * len(STEPS)
    step_days: dict[str, list[float]] = defaultdict(list)
    for c in windowed:
        furthest = _furthest_step(c, history[c.id])
        for i in range(furthest + 1):
            reached[i] += 1
        for group, days_spent in _spans(c, history[c.id]).items():
            step_days[group].append(days_spent)
    labels = {k: label for k, label, _ in GROUPS}
    conversion = [{"key": k, "label": labels[k], "reached": reached[i]} for i, k in enumerate(STEPS)]
    stage_time = [
        {"key": k, "label": labels[k], "median_days": _median(step_days[k]), "count": len(step_days[k])}
        for k in STEPS if k != "shortlisted"
    ]

    # "Proceed anyway": below the cut-off, sent on by HR with a reason. Where did they end up?
    anyway = Counter()
    for c, _, s in rows:
        fit = _fit(s)
        if fit is None or fit.passes_screen:
            continue
        if any(d.from_stage == S.SCREENED.value and d.to_stage == S.READY_FOR_HR.value for d in history[c.id]):
            anyway[GROUP_OF.get(c.stage, "screening")] += 1

    # This person's own decisions.
    mine = [d for d in decisions if d.actor_user_id == user.id]
    mine_by_week = [0] * WEEKS
    for d in mine:
        i = _bucket(weeks, _utc(d.created_at))
        if i is not None:
            mine_by_week[i] += 1
    my_outcomes = Counter(
        d.to_stage for d in mine if not since or ((_utc(d.created_at) or now) >= since)
    )

    return {
        "scope": {"business_units": [{"id": b.id, "name": b.name} for b in bus], "bu": bu, "days": days},
        "kpis": {
            "open_jobs": sum(1 for j in jobs if j.request_status == "open"),
            "draft_jobs": sum(1 for j in jobs if j.request_status == "draft"),
            "active_candidates": active,
            "needs_you": len(needs),
            "screened": screened,
            "passed_screen": passed,
            "added_by_week": added,
            "decided_by_week": decided,
        },
        "pipeline": [{"key": k, "label": label, "count": pipeline.get(k, 0)} for k, label, _ in GROUPS],
        "by_bu": [
            {
                "bu_id": b_id, "name": bu_names.get(b_id, "Unassigned"), "open_jobs": open_jobs_bu.get(b_id, 0),
                "groups": {k: per_bu[b_id].get(k, 0) for k, _, _ in GROUPS},
                "total": sum(per_bu[b_id].values()),
            }
            for b_id in ([b.id for b in bus] + ([None] if None in per_bu else []))
        ],
        "needs_you": needs[:12],
        "activity": activity,
        "scores": {"bins": histogram, "threshold": threshold, "screened": screened, "passed": passed},
        "weeks": [w.date().isoformat() for w in weeks],
        "role": role.value,
        "ageing": {"buckets": [label for _, _, label in AGE_BUCKETS], "rows": ageing},
        "jobs": job_rows,
        "conversion": conversion,
        "stage_time": stage_time,
        "proceed_anyway": {
            "total": sum(anyway.values()),
            "groups": {k: anyway.get(k, 0) for k, _, _ in GROUPS},
        },
        "mine": {"by_week": mine_by_week, "outcomes": dict(my_outcomes)},
    }


@router.get("/badges")
async def badges(user: User = Depends(current_user)) -> dict:
    """The sidebar's "N waiting for you". Cheap enough to read on every page change."""
    role = Role(user.role)
    async with get_sessionmaker()() as session:
        rows, _ = await _rows(session, user, None)
    return {"needs_you": sum(1 for c, _, s in rows if _needs(role, c, s))}


# ---- the candidate directory ------------------------------------------------------------------------

SORTS = ("waiting", "recent", "score", "name")


@router.get("/candidates")
async def directory(
    q: str = "",
    bu: str | None = None,
    job: str | None = None,
    group: str | None = None,
    flag: str | None = Query(None, pattern="^(needs_me|below)$"),
    sort: str = Query("recent", pattern="^(waiting|recent|score|name)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    user: User = Depends(current_user),
) -> dict:
    """Every candidate this person may see, searchable, filterable and sortable."""
    now = datetime.now(UTC)
    role = Role(user.role)
    async with get_sessionmaker()() as session:
        bus = await _bus(session, user)
        rows, last = await _rows(session, user, bu)
    bu_names = {b.id: b.name for b in bus}
    needle = q.strip().lower()

    items = []
    for c, j, s in rows:
        if job and j.id != job:
            continue
        if needle and needle not in f"{c.name or ''} {j.title or ''}".lower():
            continue
        fit = _fit(s)
        needs = _needs(role, c, s)
        since_at = last.get(c.id) or _utc(c.created_at) or now
        items.append({
            "id": c.id, "name": c.name, "stage": c.stage, "group": GROUP_OF.get(c.stage, "screening"),
            "job": {"id": j.id, "title": j.title}, "bu_name": bu_names.get(j.bu_id, j.business_unit),
            "score_percent": fit.score_percent if fit else None,
            "threshold_percent": fit.threshold_percent if fit else None,
            "enough_evidence": fit.enough_evidence if fit else None,
            "below": bool(fit and not fit.passes_screen),
            "needs": needs,
            "waiting_days": max(0, (now - since_at).days),
            "added": _utc(c.created_at).isoformat() if c.created_at else None,
        })

    # Facet counts reflect the search and BU/job filters, before the group and flag.
    facets = {
        "groups": {k: sum(1 for i in items if i["group"] == k) for k, _, _ in GROUPS},
        "needs_me": sum(1 for i in items if i["needs"]),
        "below": sum(1 for i in items if i["below"]),
    }
    if group:
        items = [i for i in items if i["group"] == group]
    if flag == "needs_me":
        items = [i for i in items if i["needs"]]
    elif flag == "below":
        items = [i for i in items if i["below"]]

    keys = {
        "waiting": lambda i: -i["waiting_days"],
        "recent": lambda i: i["added"] or "",
        "score": lambda i: -(i["score_percent"] if i["score_percent"] is not None else -1),
        "name": lambda i: (i["name"] or "").lower(),
    }
    items.sort(key=keys[sort], reverse=sort == "recent")
    total = len(items)
    start = (page - 1) * page_size
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": items[start:start + page_size],
        "facets": facets,
        "business_units": [{"id": b.id, "name": b.name} for b in bus],
        "groups": [{"key": k, "label": label} for k, label, _ in GROUPS],
    }


# ---- quick search ----------------------------------------------------------------------------------


@router.get("/search")
async def search(q: str = "", user: User = Depends(current_user)) -> dict:
    """The command palette: candidates, jobs and business units matching `q`."""
    needle = q.strip()
    if len(needle) < 2:
        return {"candidates": [], "jobs": [], "business_units": []}
    like = f"%{needle}%"
    async with get_sessionmaker()() as session:
        candidates = (
            await session.execute(
                select(Candidate, Job).join(Job, Candidate.job_id == Job.id)
                .where(job_scope(user), Candidate.name.ilike(like))
                .order_by(Candidate.created_at.desc()).limit(6)
            )
        ).all()
        jobs = (
            await session.execute(
                select(Job).where(job_scope(user), or_(Job.title.ilike(like), Job.business_unit.ilike(like)))
                .order_by(Job.created_at.desc()).limit(6)
            )
        ).scalars().all()
        bus = [b for b in await _bus(session, user) if needle.lower() in b.name.lower()][:4]
    return {
        "candidates": [
            {"id": c.id, "name": c.name, "stage": c.stage, "job_title": j.title} for c, j in candidates
        ],
        "jobs": [{"id": j.id, "title": j.title, "bu_name": j.business_unit, "request_status": j.request_status} for j in jobs],
        "business_units": [{"id": b.id, "name": b.name} for b in bus],
    }
