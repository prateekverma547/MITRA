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
from org.auth import current_user, job_scope, require_role
from org.models import BusinessUnit, User
from shared.screening import FitAnalysis
from shared.stages import CandidateStage as S
from shared.stages import Role

GROUPS: list[tuple[str, str, tuple[S, ...]]] = [
    ("screening", "Screening", (S.NEW, S.SCREENED)),
    ("hr", "HR round", (S.READY_FOR_HR, S.HR_BOOKED, S.HR_DONE)),
    ("technical", "MITRA interview", (S.READY_FOR_TECHNICAL, S.TECHNICAL_BOOKED, S.TECHNICAL_DONE)),
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
    S.READY_FOR_TECHNICAL.value: (Role.HR, "Book the MITRA interview round"),
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
    by_bu: bool = False,
    user: User = Depends(current_user),
) -> dict:
    """Every candidate this person may see, searchable, filterable and sortable.

    `by_bu` keeps each business unit's candidates together (units by name, the
    chosen sort within each), so a list grouped by unit stays grouped across
    pages; `bu_counts` gives each unit's total for its heading.
    """
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
    if by_bu:
        items.sort(key=lambda i: (i["bu_name"] or "~").lower())  # stable: keeps the sort within each unit
    bu_counts: dict[str, int] = {}
    for i in items:
        bu_counts[i["bu_name"] or ""] = bu_counts.get(i["bu_name"] or "", 0) + 1
    total = len(items)
    start = (page - 1) * page_size
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": items[start:start + page_size],
        "facets": facets,
        "bu_counts": bu_counts,
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


# ---- the organisation's own insights -------------------------------------------------------------
#
# What an org admin should look at, said as sentences with the number and the
# comparison that makes it mean something. Every sentence is computed here from
# stages, decisions, screenings and jobs; no model writes them. A rate or a
# median is only ever stated over enough cases (`MIN_N`), so a unit with two
# candidates never reads as "twice as slow".

#: Fewest cases behind any rate or median an insight states.
MIN_N = 5
#: A unit's step is called slow at this multiple of the organisation's median...
SLOW_RATIO = 1.5
#: ...and only when that is also at least this many days more.
SLOW_MIN_EXTRA_DAYS = 2.0
#: How long a person may keep someone waiting before it is worth saying.
LONG_WAIT_DAYS = 7
#: A screening pass rate this many points from the organisation's is an outlier.
PASS_RATE_GAP = 20.0
#: An open job with no shortlist after this long, and at least MIN_N candidates.
STALE_JOB_DAYS = 45
#: Disagreement with MITRA worth raising: at or above this rate, or moved by this many points.
DIFFER_RATE = 15.0
DIFFER_SHIFT = 5.0

STEP_NAMES = {"screening": "screening", "hr": "HR rounds", "technical": "MITRA interview rounds", "bu_head": "decisions with the BU head"}
BU_DECIDES = {S.SHORTLISTED.value, S.REJECTED.value, S.ON_HOLD.value}
WAITS_ON_PEOPLE = set(WAITS_ON)  # stages where a person, not MITRA, has to act


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _change(now_value: float | None, before: float | None) -> float | None:
    """Relative change in percent, or None when there is nothing to compare with."""
    if now_value is None or before in (None, 0):
        return None
    return round(100.0 * (now_value - before) / before, 1)


def _first_bu_decision(history: list[DecisionRecord]) -> DecisionRecord | None:
    decided = [d for d in history if d.actor == "bu_head" and d.to_stage in BU_DECIDES]
    return min(decided, key=lambda d: (_utc(d.created_at) or datetime.max.replace(tzinfo=UTC), d.id or 0)) if decided else None


def _waiting_days(candidate: Candidate, history: list[DecisionRecord], now: datetime) -> int:
    times = [_utc(d.created_at) for d in history if d.created_at] or [_utc(candidate.created_at) or now]
    return max(0, (now - max(times)).days)


def org_insights(rows, jobs, bus, decisions, *, now: datetime, days: int) -> dict:
    """The org admin's insights, KPIs and unit table, from data already in scope."""
    from hiring.transparency import DIFFERED, FOLLOWED, outcome

    window = timedelta(days=days) if days else None
    start = now - window if window else None
    prev_start = now - 2 * window if window else None

    def in_now(at: datetime | None) -> bool:
        at = _utc(at)
        return at is not None and (start is None or at >= start)

    def in_prev(at: datetime | None) -> bool:
        at = _utc(at)
        return window is not None and at is not None and prev_start <= at < start

    bu_names = {b.id: b.name for b in bus}
    history: dict[str, list[DecisionRecord]] = defaultdict(list)
    for d in decisions:
        history[d.candidate_id].append(d)
    by_bu: dict[str | None, list] = defaultdict(list)
    for row in rows:
        by_bu[row[1].bu_id].append(row)

    # -- KPIs -------------------------------------------------------------------------------
    def time_to_decision(pick) -> tuple[float | None, int]:
        values = []
        for c, _, _ in rows:
            first = _first_bu_decision(history[c.id])
            if first and pick(first.created_at) and c.created_at:
                values.append(((_utc(first.created_at) - _utc(c.created_at)).total_seconds()) / 86400)
        return (round(_median(values), 1) if len(values) >= 1 else None), len(values)

    def conversion(pick) -> tuple[float | None, int]:
        cohort = [c for c, _, s in rows if pick(c.created_at) and _fit(s) is not None]
        shortlisted = sum(1 for c in cohort if c.stage == S.SHORTLISTED.value
                          or any(d.to_stage == S.SHORTLISTED.value for d in history[c.id]))
        return _pct(shortlisted, len(cohort)), len(cohort)

    def agreement(pick) -> tuple[float | None, int]:
        counts = Counter(
            outcome(d.actor, d.to_stage, d.model_recommendation) for d in decisions if d.actor != "system" and pick(d.created_at)
        )
        n = counts[FOLLOWED] + counts[DIFFERED]
        return _pct(counts[FOLLOWED], n), n

    ttd_now, ttd_n = time_to_decision(in_now)
    ttd_prev, ttd_prev_n = time_to_decision(in_prev)
    conv_now, conv_n = conversion(in_now)
    conv_prev, conv_prev_n = conversion(in_prev)
    agree_now, agree_n = agreement(in_now)
    agree_prev, agree_prev_n = agreement(in_prev)

    waiting = []  # (candidate, job, days waiting)
    for c, j, s in rows:
        if c.stage in WAITS_ON_PEOPLE or (c.stage == S.NEW.value and (s is None or s.status in ("waiting", "failed"))):
            waiting.append((c, j, _waiting_days(c, history[c.id], now)))
    long_waits = [w for w in waiting if w[2] > LONG_WAIT_DAYS]

    def kpi(value, n, before, before_n, *, unit, better):
        comparable = value is not None and before is not None and n >= MIN_N and before_n >= MIN_N
        return {
            "value": value if n >= MIN_N else None, "n": n, "unit": unit,
            "previous": before if comparable else None,
            "change_percent": _change(value, before) if comparable else None, "better": better,
        }

    kpis = {
        "time_to_decision": kpi(ttd_now, ttd_n, ttd_prev, ttd_prev_n, unit="days", better="lower"),
        "screen_to_shortlist": kpi(conv_now, conv_n, conv_prev, conv_prev_n, unit="percent", better="higher"),
        "waiting_on_people": {
            "value": len(waiting), "n": len(waiting), "unit": "candidates", "better": "lower",
            "over_week": len(long_waits), "over_week_percent": _pct(len(long_waits), len(waiting)),
            "previous": None, "change_percent": None,
        },
        "agreement": kpi(agree_now, agree_n, agree_prev, agree_prev_n, unit="percent", better="higher"),
    }

    # -- Units --------------------------------------------------------------------------------
    org_steps: dict[str, list[float]] = defaultdict(list)
    unit_steps: dict[str | None, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for c, j, _ in rows:
        for step, spent in _spans(c, history[c.id]).items():
            org_steps[step].append(spent)
            unit_steps[j.bu_id][step].append(spent)

    units = []
    for b in bus:
        members = by_bu.get(b.id, [])
        screened = [(c, s) for c, _, s in members if _fit(s) is not None and in_now(c.created_at)]
        passed = sum(1 for _, s in screened if _fit(s).passes_screen)
        ttd = []
        for c, _, _ in members:
            first = _first_bu_decision(history[c.id])
            if first and in_now(first.created_at) and c.created_at:
                ttd.append(((_utc(first.created_at) - _utc(c.created_at)).total_seconds()) / 86400)
        units.append({
            "bu_id": b.id, "name": b.name,
            "open_jobs": sum(1 for j in jobs if j.bu_id == b.id and j.request_status == "open"),
            "in_progress": sum(1 for c, _, _ in members if GROUP_OF.get(c.stage) not in ("closed", "shortlisted")),
            "median_days_to_decision": round(_median(ttd), 1) if len(ttd) >= 3 else None,
            "decided": len(ttd),
            "pass_rate": _pct(passed, len(screened)) if len(screened) >= MIN_N else None,
            "screened": len(screened),
            "waiting_over_week": sum(1 for c, j, _ in long_waits if j.bu_id == b.id),
        })

    # -- Insights -----------------------------------------------------------------------------
    insights = []

    # 1. A unit whose step takes far longer than the organisation's.
    for step in ("hr", "technical", "bu_head", "screening"):
        org = org_steps.get(step, [])
        if len(org) < MIN_N:
            continue
        org_median = _median(org)
        for b in bus:
            mine = unit_steps[b.id].get(step, [])
            if len(mine) < MIN_N or not org_median:
                continue
            m = _median(mine)
            if m >= SLOW_RATIO * org_median and m - org_median >= SLOW_MIN_EXTRA_DAYS:
                now_there = sum(1 for c, j, _ in rows if j.bu_id == b.id and GROUP_OF.get(c.stage) == step)
                insights.append({
                    "kind": "slow_step", "tone": "attention", "score": (m / org_median) * (1 + now_there),
                    "text": f"{b.name}'s {STEP_NAMES[step]} take {m:.1f} days, {m / org_median:.1f}× the organisation's median of {org_median:.1f}."
                            + (f" {now_there} {'candidate is' if now_there == 1 else 'candidates are'} there now." if now_there else ""),
                    "link": {"to": f"/candidates?bu={b.id}&group={step}&sort=waiting", "label": "See who is there"},
                    "basis": f"{len(mine)} finished in {b.name}; {len(org)} across the organisation.",
                })

    # 2. People keeping candidates waiting for over a week.
    bu_waits = [w for w in long_waits if w[0].stage in (S.WITH_BU_HEAD.value, S.ON_HOLD.value)]
    hr_waits = [w for w in long_waits if w not in bu_waits]
    if len(bu_waits) >= 2:
        per = Counter(bu_names.get(j.bu_id, "Unassigned") for _, j, _ in bu_waits).most_common(1)[0]
        insights.append({
            "kind": "bu_head_waits", "tone": "attention", "score": 3 * len(bu_waits),
            "text": f"{len(bu_waits)} candidates have waited over {LONG_WAIT_DAYS} days for a BU head's decision"
                    + (f" ({per[0]}: {per[1]})." if per[1] < len(bu_waits) else f", all in {per[0]}."),
            "link": {"to": "/candidates?group=bu_head&sort=waiting", "label": "See them"},
            "basis": f"The oldest has waited {max(w[2] for w in bu_waits)} days.",
        })
    if len(hr_waits) >= MIN_N:
        insights.append({
            "kind": "hr_waits", "tone": "attention", "score": 1.5 * len(hr_waits),
            "text": f"{len(hr_waits)} candidates have waited over {LONG_WAIT_DAYS} days for HR to act; the oldest for {max(w[2] for w in hr_waits)} days.",
            "link": {"to": "/candidates?sort=waiting", "label": "See the queue"},
            "basis": f"{len(waiting)} candidates are waiting on a person in all.",
        })

    # 3. Disagreement with MITRA, and how it moved.
    if agree_n >= MIN_N and agree_now is not None:
        differ_now = round(100 - agree_now, 1)
        differ_prev = round(100 - agree_prev, 1) if agree_prev is not None and agree_prev_n >= MIN_N else None
        moved = differ_prev is not None and abs(differ_now - differ_prev) >= DIFFER_SHIFT
        if differ_now >= DIFFER_RATE or moved:
            trend = f", {'up' if differ_now > differ_prev else 'down'} from {differ_prev:g}%" if moved else ""
            insights.append({
                "kind": "disagreement", "tone": "info", "score": differ_now / 5 + (3 if moved else 0),
                "text": f"People differed from MITRA on {differ_now:g}% of the decisions it could be compared on{trend}.",
                "link": {"to": "/transparency?result=differed", "label": "Read those decisions"},
                "basis": f"{agree_n} decisions could be compared with MITRA's recommendation.",
            })

    # 4. A unit whose screening passes far more or fewer CVs than the rest.
    all_screened = sum(u["screened"] for u in units)
    all_passed = sum(1 for c, _, s in rows if _fit(s) is not None and in_now(c.created_at) and _fit(s).passes_screen)
    if all_screened >= 2 * MIN_N:
        org_rate = 100.0 * all_passed / all_screened
        for u in units:
            if u["pass_rate"] is None:
                continue
            gap = u["pass_rate"] - org_rate
            if abs(gap) >= PASS_RATE_GAP:
                low = gap < 0
                insights.append({
                    "kind": "pass_rate", "tone": "info", "score": abs(gap) / 10,
                    "text": f"Screening passes {org_rate:.0f}% of CVs; {u['name']} passes {u['pass_rate']:.0f}%."
                            + (" Check its jobs' descriptions and cut-off." if low else " Check whether its cut-off is too easy."),
                    "link": {"to": f"/candidates?bu={u['bu_id']}" + ("&flag=below" if low else ""), "label": "See its candidates"},
                    "basis": f"{u['screened']} screened in {u['name']}; {all_screened} in all.",
                })

    # 5. The oldest open job with candidates and no shortlist.
    stale = []
    for j in jobs:
        opened = _utc(j.created_at)
        if j.request_status != "open" or opened is None:
            continue
        mine = [c for c, jj, _ in rows if jj.id == j.id]
        open_days = (now - opened).days
        if open_days >= STALE_JOB_DAYS and len(mine) >= MIN_N and not any(c.stage == S.SHORTLISTED.value for c in mine):
            stale.append((open_days, j, len(mine)))
    if stale:
        open_days, j, n = max(stale, key=lambda t: t[0])
        insights.append({
            "kind": "stale_job", "tone": "info", "score": open_days / 15,
            "text": f"No shortlist yet for {j.title} after {open_days} days open and {n} candidates."
                    + (f" {len(stale) - 1} other open {'job is' if len(stale) == 2 else 'jobs are'} in the same position." if len(stale) > 1 else ""),
            "link": {"to": f"/jobs/{j.id}/candidates", "label": "Open the job"},
            "basis": f"Open jobs older than {STALE_JOB_DAYS} days with {MIN_N} or more candidates.",
        })

    insights.sort(key=lambda i: -i["score"])
    for i in insights:
        i.pop("score")

    return {
        "computed_at": now.isoformat(),
        "days": days,
        "min_n": MIN_N,
        "insights": insights[:5],
        "kpis": kpis,
        "units": units,
    }



@router.get("/org/insights")
async def org_insights_route(
    days: int = Query(30, ge=0, le=3650, description="The period; 0 means all time (no comparison)."),
    user: User = Depends(require_role(Role.ORG_ADMIN)),
) -> dict:
    """What an org admin should look at first: insights, KPIs against the previous period, and units."""
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        bus = await _bus(session, user)
        rows, _ = await _rows(session, user, None)
        jobs = list((await session.execute(select(Job).where(job_scope(user)))).scalars())
        ids = [c.id for c, _, _ in rows]
        decisions = list(
            (await session.execute(select(DecisionRecord).where(DecisionRecord.candidate_id.in_(ids))))
            .scalars()
        ) if ids else []
    return org_insights(rows, jobs, bus, decisions, now=now, days=days)
