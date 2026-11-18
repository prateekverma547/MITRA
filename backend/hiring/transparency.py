"""Transparency for the org admin: who decided what, beside what the model said;
the records behind it; which model and prompt produced every score; and the
audit trail. Track H, phase 4.

Everything is read from rows that already exist. Nothing here is a new record
of its own, so nothing can drift from what actually happened. Every route is
the org admin's alone (decision 3: admins read everything, decide nothing).

"Followed" and "differed" are only ever said when the recorded recommendation
and the person's move can be compared without interpretation. When they cannot
(a "Not now", a "discuss" recommendation, an older free-text recommendation),
the answer is "not comparable", because a guess here would become a written
claim about how a person does their job.
"""

from datetime import UTC, date, datetime, time, timedelta

from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_, select

from app.db import Candidate, Interview, Job, RecordingStatus, get_sessionmaker
from hiring.api import router
from hiring.models import AuditEvent, DecisionRecord, HRRound, Screening
from hiring.stages import audit
from org.auth import job_scope, require_role
from org.models import BusinessUnit, User
from shared.stages import Role

ADMIN = require_role(Role.ORG_ADMIN)

# ---- followed or differed --------------------------------------------------------------------------

FOLLOWED, DIFFERED, NOT_COMPARABLE, NO_RECOMMENDATION, MITRA = (
    "followed", "differed", "not_comparable", "no_recommendation", "mitra",
)
OUTCOMES = (FOLLOWED, DIFFERED, NOT_COMPARABLE, NO_RECOMMENDATION)


def model_said(recommendation: str | None) -> str | None:
    """What a recorded recommendation says, as `go`, `stop`, or None when it says neither.

    Reads the two sentences this codebase writes (hiring.candidates.recommendation
    and hiring.hr_evaluation.recommendation_text). Anything else, including a
    "discuss", is None: it does not point one way.
    """
    text = (recommendation or "").strip()
    if not text:
        return None
    if text.startswith("Screening score"):
        if "Too little evidence" in text:
            return None
        if "at or above the" in text:
            return "go"
        if "below the" in text:
            return "stop"
        return None
    if text.startswith("HR round evaluation recommends:"):
        verdict = text.split(":", 1)[1].strip().lower()
        if verdict.startswith("proceed"):
            return "go"
        if verdict.startswith("reject"):
            return "stop"
        return None
    return None


#: The moves that answer a go/stop recommendation: going ahead, and rejecting.
#: "Not now" (parked) answers neither, so it is never counted either way.
_GO = {"ready_for_hr", "ready_for_technical"}
_STOP = {"rejected"}


def outcome(actor: str, to_stage: str, recommendation: str | None) -> str:
    if actor == "system":
        return MITRA
    if not (recommendation or "").strip():
        return NO_RECOMMENDATION
    said = model_said(recommendation)
    if said is None or to_stage not in _GO | _STOP:
        return NOT_COMPARABLE
    went = "go" if to_stage in _GO else "stop"
    return FOLLOWED if went == said else DIFFERED


# ---- helpers ----------------------------------------------------------------------------------------


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    return value.isoformat() if value else None


def _window(since: date | None, until: date | None) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(since, time.min, tzinfo=UTC) if since else None
    end = datetime.combine(until, time.min, tzinfo=UTC) + timedelta(days=1) if until else None
    return start, end


async def _scope(session, user: User) -> tuple[dict, dict, dict]:
    """Candidates, jobs and BU names this admin may see, by id."""
    rows = (
        await session.execute(
            select(Candidate, Job).join(Job, Candidate.job_id == Job.id).where(job_scope(user))
        )
    ).all()
    bus = (
        await session.execute(select(BusinessUnit).where(BusinessUnit.org_id == user.org_id))
    ).scalars().all()
    candidates = {c.id: c for c, _ in rows}
    jobs = {j.id: j for _, j in rows}
    return candidates, jobs, {b.id: b.name for b in bus}


async def _names(session, ids: set[str]) -> dict[str, User]:
    if not ids:
        return {}
    return {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()}


def _bu(job: Job, bu_names: dict) -> str:
    return bu_names.get(job.bu_id) or job.business_unit or "Unassigned"


def _page(items: list, page: int, page_size: int) -> list:
    start = (page - 1) * page_size
    return items[start:start + page_size]


# ---- 1. the decision log ------------------------------------------------------------------------------


@router.get("/transparency/decisions")
async def decisions(
    q: str = "",
    bu: str | None = None,
    actor: str | None = Query(None, pattern="^(mitra|people)$"),
    role: str | None = Query(None, pattern="^(hr|bu_head)$"),
    result: str | None = Query(None, pattern="^(followed|differed|not_comparable|no_recommendation)$"),
    since: date | None = None,
    until: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    user: User = Depends(ADMIN),
) -> dict:
    """Every move of every candidate, newest first, with the model's recommendation beside it."""
    start, end = _window(since, until)
    async with get_sessionmaker()() as session:
        candidates, jobs, bu_names = await _scope(session, user)
        records = []
        if candidates:
            records = (
                await session.execute(
                    select(DecisionRecord).where(DecisionRecord.candidate_id.in_(list(candidates)))
                    .order_by(DecisionRecord.created_at.desc(), DecisionRecord.id.desc())
                )
            ).scalars().all()
        people = await _names(session, {d.actor_user_id for d in records if d.actor_user_id})

    needle = q.strip().lower()
    rows = []
    for d in records:
        at = _utc(d.created_at)
        if (start and at and at < start) or (end and at and at >= end):
            continue
        c = candidates[d.candidate_id]
        j = jobs[c.job_id]
        if bu and j.bu_id != bu:
            continue
        if needle and needle not in f"{c.name or ''} {j.title or ''}".lower():
            continue
        if actor == "mitra" and d.actor != "system":
            continue
        if actor == "people" and d.actor == "system":
            continue
        if role and d.actor != role:
            continue
        person = people.get(d.actor_user_id)
        rows.append({
            "id": d.id, "at": _iso(d.created_at),
            "candidate": {"id": c.id, "name": c.name},
            "job": {"id": j.id, "title": j.title}, "bu_name": _bu(j, bu_names),
            "from": d.from_stage, "to": d.to_stage,
            "actor": {"kind": "mitra" if d.actor == "system" else "person", "role": d.actor,
                      "name": "MITRA" if d.actor == "system" else (person.name if person else d.actor.replace("_", " ").title())},
            "reason": d.reason, "model_recommendation": d.model_recommendation,
            "outcome": outcome(d.actor, d.to_stage, d.model_recommendation),
        })

    # The summary reads the filters except the outcome, so "show me the ones that
    # differed" can be a link from it without its own count changing under you.
    by_people = [r for r in rows if r["actor"]["kind"] == "person"]
    counts = {o: sum(1 for r in by_people if r["outcome"] == o) for o in OUTCOMES}
    comparable = counts[FOLLOWED] + counts[DIFFERED]
    summary = {
        "by_people": len(by_people), "by_mitra": len(rows) - len(by_people), **counts,
        "differed_percent": round(100 * counts[DIFFERED] / comparable) if comparable else None,
    }
    if result:
        rows = [r for r in rows if r["outcome"] == result]
    return {
        "total": len(rows), "page": page, "page_size": page_size,
        "items": _page(rows, page, page_size), "summary": summary,
        "business_units": [{"id": k, "name": v} for k, v in sorted(bu_names.items(), key=lambda kv: kv[1])],
    }


# ---- 2. records: transcripts, evaluations, screenings, recordings ------------------------------------

SNIPPET = 90


def _snippet(text: str, needle: str) -> str:
    i = text.lower().find(needle)
    if i < 0:
        return text[: SNIPPET * 2] + ("…" if len(text) > SNIPPET * 2 else "")
    start, end = max(0, i - SNIPPET), min(len(text), i + len(needle) + SNIPPET)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


@router.get("/transparency/records")
async def records(
    q: str = "",
    kind: str | None = Query(None, pattern="^(hr_round|screening|recording)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=100),
    user: User = Depends(ADMIN),
) -> dict:
    """HR round transcripts and evaluations, screenings and stored recordings, searchable.

    Search reads the words: a transcript matches on what was said in it, an
    evaluation on its summary and findings, a screening on its reasoning, and
    all of them on the candidate's name and the job title.
    """
    needle = q.strip().lower()
    async with get_sessionmaker()() as session:
        candidates, jobs, bu_names = await _scope(session, user)
        ids = list(candidates)
        rounds = screenings = recorded = []
        if ids:
            rounds = (
                await session.execute(
                    select(HRRound, Interview).join(Interview, HRRound.interview_id == Interview.id)
                    .where(HRRound.candidate_id.in_(ids)).order_by(HRRound.id.desc())
                )
            ).all()
            screenings = (
                await session.execute(
                    select(Screening).where(Screening.candidate_id.in_(ids), Screening.fit_analysis.isnot(None))
                    .order_by(Screening.finished_at.desc())
                )
            ).scalars().all()
            recorded = (
                await session.execute(
                    select(Interview).where(
                        Interview.candidate_id.in_(ids), Interview.recording_status == RecordingStatus.STORED,
                    ).order_by(Interview.created_at.desc())
                )
            ).scalars().all()

    def who(cid: str) -> dict:
        c = candidates[cid]
        j = jobs[c.job_id]
        return {"candidate": {"id": c.id, "name": c.name}, "job": {"id": j.id, "title": j.title},
                "bu_name": _bu(j, bu_names)}

    def names_match(cid: str) -> bool:
        c = candidates[cid]
        return needle in f"{c.name or ''} {jobs[c.job_id].title or ''}".lower()

    items: list[dict] = []
    if kind in (None, "hr_round"):
        for hr, interview in rounds:
            if not (hr.transcript or hr.evaluation):
                continue
            ev = hr.evaluation or {}
            hits = []
            if needle:
                for turn in hr.transcript or []:
                    text = turn.get("text") or ""
                    if needle in text.lower():
                        hits.append({"turn": turn.get("index"), "speaker": turn.get("speaker"),
                                     "at_seconds": turn.get("start"), "text": _snippet(text, needle)})
                written = " ".join([ev.get("summary") or "", *(ev.get("findings_for_technical") or [])])
                if not hits and needle in written.lower():
                    hits.append({"turn": None, "speaker": "evaluation", "at_seconds": None,
                                 "text": _snippet(written, needle)})
                if not hits and not names_match(hr.candidate_id):
                    continue
            items.append({
                "kind": "hr_round", "id": f"hr-{hr.id}", "at": _iso(hr.evaluated_at or hr.ended_at or interview.created_at),
                **who(hr.candidate_id),
                "turns": len(hr.transcript or []), "eval_status": hr.eval_status,
                "recommendation": ev.get("recommendation"), "summary": ev.get("summary"),
                "attribution": (hr.attribution or {}).get("confidence"),
                "matches": hits[:3], "more_matches": max(0, len(hits) - 3),
                "open": f"/candidates/{hr.candidate_id}/hr-round",
            })
    if kind in (None, "screening"):
        for s in screenings:
            fit = s.fit_analysis or {}
            text = " ".join([fit.get("summary") or "", fit.get("experience_note") or "",
                             *[(r.get("reasoning") or "") for r in fit.get("ratings") or []]])
            hits = []
            if needle:
                if needle in text.lower():
                    hits.append({"turn": None, "speaker": "screening", "at_seconds": None, "text": _snippet(text, needle)})
                elif not names_match(s.candidate_id):
                    continue
            items.append({
                "kind": "screening", "id": f"screening-{s.id}", "at": _iso(s.finished_at), **who(s.candidate_id),
                "score_percent": fit.get("score_percent"), "assessed_weight_percent": fit.get("assessed_weight_percent"),
                "summary": fit.get("summary"), "matches": hits, "more_matches": 0,
                "open": f"/candidates/{s.candidate_id}",
            })
    if kind in (None, "recording"):
        for interview in recorded:
            if needle and not names_match(interview.candidate_id):
                continue
            items.append({
                "kind": "recording", "id": f"rec-{interview.id}", "at": _iso(interview.ended_at or interview.created_at),
                **who(interview.candidate_id), "round": interview.round,
                "play": f"/api/transparency/recordings/{interview.id}",
                "open": (f"/candidates/{interview.candidate_id}/hr-round" if interview.round == "hr"
                         else f"/candidates/{interview.candidate_id}"),
                "matches": [], "more_matches": 0,
            })

    items.sort(key=lambda i: i["at"] or "", reverse=True)
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in ("hr_round", "screening", "recording")}
    return {"total": len(items), "page": page, "page_size": page_size,
            "items": _page(items, page, page_size), "counts": counts}


@router.get("/transparency/recordings/{interview_id}", include_in_schema=False)
async def play_recording(interview_id: str, user: User = Depends(ADMIN)):
    """A stored recording, for the org admin, and the opening is itself recorded.

    The older `/interviews/{id}/recording/video` is behind the old admin
    password, which console users do not have.
    """
    from app.recordings import stored_path

    async with get_sessionmaker()() as session:
        interview = (
            await session.execute(
                select(Interview).join(Candidate, Interview.candidate_id == Candidate.id)
                .join(Job, Candidate.job_id == Job.id)
                .where(Interview.id == interview_id, job_scope(user))
            )
        ).scalar_one_or_none()
        if interview is None:
            raise HTTPException(status_code=404, detail="No such interview in your organisation.")
        path = stored_path(interview)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail=f"There is no recording file for this interview (it is '{interview.recording_status}').")
        await audit(session, "recording_opened", by=user, subject_type="interview", subject_id=interview_id)
        await session.commit()
    return FileResponse(path, media_type="video/mp4", filename=f"{interview_id}.mp4")


# ---- 3. the AI usage register ----------------------------------------------------------------------

PURPOSES = {
    "cv_fit": "CV fit screening",
    "hr_questions": "HR round questions",
    "hr_evaluation": "HR round evaluation",
    "job_understanding": "Understanding the job request",
}
NOT_RECORDED = "not recorded"


@router.get("/transparency/ai")
async def ai_register(
    purpose: str | None = Query(None, pattern="^(cv_fit|hr_questions|hr_evaluation|job_understanding)$"),
    model: str | None = None,
    prompt_version: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    user: User = Depends(ADMIN),
) -> dict:
    """Every model output behind a score, a question set or a recommendation.

    Read from the outputs themselves: each contract carries the model and the
    prompt version that produced it, so the register cannot disagree with them.
    """
    async with get_sessionmaker()() as session:
        candidates, jobs, bu_names = await _scope(session, user)
        ids = list(candidates)
        screenings = rounds = []
        if ids:
            screenings = (
                await session.execute(select(Screening).where(Screening.candidate_id.in_(ids)))
            ).scalars().all()
            rounds = (
                await session.execute(
                    select(HRRound).where(HRRound.candidate_id.in_(ids), HRRound.evaluation.isnot(None))
                )
            ).scalars().all()
        all_jobs = (await session.execute(select(Job).where(job_scope(user)))).scalars().all()

    uses: list[dict] = []

    def add(kind: str, payload: dict | None, at: datetime | None, cid: str | None, job: Job | None, subject: str):
        if not payload:
            return
        uses.append({
            "purpose": kind, "purpose_label": PURPOSES[kind],
            "model": payload.get("model") or NOT_RECORDED,
            "prompt_version": payload.get("prompt_version") or NOT_RECORDED,
            "at": _iso(at), "subject": subject,
            "candidate": {"id": cid, "name": candidates[cid].name} if cid else None,
            "job": {"id": job.id, "title": job.title} if job else None,
            "bu_name": _bu(job, bu_names) if job else None,
        })

    for s in screenings:
        job = jobs[candidates[s.candidate_id].job_id]
        fit = s.fit_analysis or {}
        if fit:
            add("cv_fit", fit, s.finished_at, s.candidate_id, job, f"Score {fit.get('score_percent', 0):g}%")
        add("hr_questions", s.hr_questions, s.finished_at, s.candidate_id, job, "Ten HR round questions")
    for hr in rounds:
        job = jobs[candidates[hr.candidate_id].job_id]
        word = {"proceed_to_technical": "Recommended proceeding", "reject": "Recommended rejecting",
                "discuss": "Recommended discussing"}
        add("hr_evaluation", hr.evaluation, hr.evaluated_at, hr.candidate_id, job,
            word.get((hr.evaluation or {}).get("recommendation"), "Evaluation"))
    # The job-understanding chat stores the spec it wrote, but not which model
    # wrote it. Listed honestly rather than left out.
    for j in all_jobs:
        if j.evaluation_spec:
            add("job_understanding", {"model": "", "prompt_version": ""}, j.created_at, None, j,
                "What MITRA understood about the job")

    groups: dict[tuple, dict] = {}
    for u in uses:
        key = (u["purpose"], u["model"], u["prompt_version"])
        g = groups.setdefault(key, {
            "purpose": u["purpose"], "purpose_label": u["purpose_label"], "model": u["model"],
            "prompt_version": u["prompt_version"], "count": 0, "first_used": None, "last_used": None,
        })
        g["count"] += 1
        if u["at"]:
            g["first_used"] = min(filter(None, [g["first_used"], u["at"]]))
            g["last_used"] = max(filter(None, [g["last_used"], u["at"]]))
    order = list(PURPOSES)
    register = sorted(groups.values(), key=lambda g: (order.index(g["purpose"]), g["model"], g["prompt_version"]))

    if purpose:
        uses = [u for u in uses if u["purpose"] == purpose]
    if model:
        uses = [u for u in uses if u["model"] == model]
    if prompt_version:
        uses = [u for u in uses if u["prompt_version"] == prompt_version]
    uses.sort(key=lambda u: u["at"] or "", reverse=True)

    gaps = []
    if any(g["model"] == NOT_RECORDED for g in register):
        gaps.append("Some outputs do not record the model that produced them. "
                    "The job-understanding chat has never recorded it; older screenings may not either.")
    return {"register": register, "total": len(uses), "page": page, "page_size": page_size,
            "items": _page(uses, page, page_size), "gaps": gaps}


# ---- 4. the audit trail ---------------------------------------------------------------------------

ACTION_LABELS = {
    "signed_in": "Signed in",
    "sign_in_failed": "Failed sign-in",
    "job_request_created": "Created a job request",
    "job_request_edit_started": "Reopened a job request",
    "candidate_added": "Added a candidate",
    "cv_replaced": "Replaced a CV",
    "screening_started": "Started screening",
    "hr_round_booked": "Booked an HR round",
    "hr_round_ended": "Ended an HR round",
    "recording_opened": "Opened a recording",
}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action) or action.replace("_", " ").capitalize()


@router.get("/transparency/audit")
async def audit_trail(
    q: str = "",
    action: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=5, le=100),
    user: User = Depends(ADMIN),
) -> dict:
    """Everything people did that someone may later need to account for, newest first.

    Failed sign-ins carry no organisation (nobody was signed in), and there is
    one organisation per deployment, so they are shown here too.
    """
    async with get_sessionmaker()() as session:
        events = (
            await session.execute(
                select(AuditEvent).where(or_(AuditEvent.org_id == user.org_id, AuditEvent.org_id.is_(None)))
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            )
        ).scalars().all()
        people = await _names(session, {e.actor_user_id for e in events if e.actor_user_id})

    actions = sorted({e.action for e in events})
    needle = q.strip().lower()
    rows = []
    for e in events:
        if action and e.action != action:
            continue
        person = people.get(e.actor_user_id)
        actor = {"name": person.name, "role": person.role, "email": person.email} if person else None
        row = {
            "id": e.id, "at": _iso(e.created_at), "action": e.action, "label": action_label(e.action),
            "actor": actor, "subject_type": e.subject_type, "subject_id": e.subject_id, "detail": e.detail,
        }
        if needle:
            hay = " ".join(str(x) for x in [row["label"], e.subject_id or "", e.detail or "",
                                            *(actor.values() if actor else [])]).lower()
            if needle not in hay:
                continue
        rows.append(row)
    return {"total": len(rows), "page": page, "page_size": page_size, "items": _page(rows, page, page_size),
            "actions": [{"key": a, "label": action_label(a)} for a in actions]}
