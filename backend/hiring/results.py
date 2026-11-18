"""What the BU head reads before deciding, and the job-wide comparison. Track H.

Built only from evidence Track H already holds: the CV fit against the job's
weighted competencies, the HR round's evaluation, and every decision so far.
The technical round's report belongs to Track I (`<InterviewReport>`); until it
lands, the results page carries a bounded slot for it, and the only technical
data shown here is the headline of a report that really exists for this
candidate. Nothing is filled in from fixtures.

Strengths and gaps are picked out in code from those ratings, never written by
a model, and each one says where it came from (the CV or the HR round), so a
reader can always go back to the evidence.
"""

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select

from app.db import Candidate, Job, get_sessionmaker
from hiring.api import router
from hiring.models import DecisionRecord, HRRound, Screening
from hiring.technical import technical_report
from org.auth import current_user, job_scope
from org.models import User
from shared.screening import MIN_ASSESSED_WEIGHT_PERCENT, FitAnalysis, HRRoundEvaluation
from shared.stages import Actor, CandidateStage, Role, allowed_next

S = CandidateStage

#: Stages the BU head decides from. Anything else, the page is read-only.
DECIDING = (S.WITH_BU_HEAD, S.ON_HOLD)

#: Not part of the default comparison: nobody has screened them yet, or their
#: process has ended. "Include closed" brings the closed ones back.
NOT_SCREENED = (S.NEW,)
CLOSED = (S.REJECTED, S.PARKED, S.WITHDREW)

STRONG = 3
WEAK = 1


def _fit(screening: Screening | None) -> FitAnalysis | None:
    if screening is None or not screening.fit_analysis:
        return None
    try:
        return FitAnalysis.model_validate(screening.fit_analysis)
    except Exception:  # noqa: BLE001 - an old row must not break the page
        return None


def _evaluation(hr: HRRound | None) -> HRRoundEvaluation | None:
    if hr is None or not hr.evaluation:
        return None
    try:
        return HRRoundEvaluation.model_validate(hr.evaluation)
    except Exception:  # noqa: BLE001
        return None


async def _latest_hr(session, candidate_id: str) -> HRRound | None:
    return (
        await session.execute(
            select(HRRound).where(HRRound.candidate_id == candidate_id, HRRound.evaluation.isnot(None))
            .order_by(HRRound.id.desc())
        )
    ).scalars().first()


def _weight_percents(fit: FitAnalysis) -> dict[str, float]:
    total = sum(r.weight for r in fit.ratings) or 1.0
    return {r.competency_id: round(r.weight / total * 100, 1) for r in fit.ratings}


def strengths_and_gaps(fit: FitAnalysis | None, evaluation: HRRoundEvaluation | None) -> tuple[list[dict], list[dict]]:
    """Picked out in code from the ratings, heaviest competencies first.

    A strength is a rating of 3 or 4. A gap is a rating of 0 or 1, or a
    competency the CV says nothing about that carries real weight (10% or
    more of the job): "no evidence" is a gap in what we know, not a low score,
    and it is labelled that way.
    """
    strengths: list[dict] = []
    gaps: list[dict] = []
    if fit:
        weights = _weight_percents(fit)
        for r in sorted(fit.ratings, key=lambda r: -r.weight):
            item = {
                "source": "cv", "name": r.competency_name, "rating": r.rating,
                "weight_percent": weights[r.competency_id], "why": r.reasoning,
            }
            if r.rating is None:
                if weights[r.competency_id] >= 10:
                    gaps.append(item | {"kind": "no_evidence"})
            elif r.rating >= STRONG:
                strengths.append(item | {"kind": "strong"})
            elif r.rating <= WEAK:
                gaps.append(item | {"kind": "low"})
    if evaluation:
        for r in evaluation.ratings:
            item = {"source": "hr_round", "name": r.name, "rating": r.rating, "weight_percent": None, "why": r.reasoning}
            if r.rating is not None and r.rating >= STRONG:
                strengths.append(item | {"kind": "strong"})
            elif r.rating is not None and r.rating <= WEAK:
                gaps.append(item | {"kind": "low"})
    return strengths, gaps


def _technical_headline(report: dict | None) -> dict | None:
    """Only what a real report says, read-only: its signal, summary and scores."""
    if not report:
        return None
    return {
        "recommendation": report.get("recommendation"),
        "summary": report.get("summary"),
        "scores": [
            {
                "name": s.get("name"), "score": s.get("score"),
                "insufficient_signal": bool(s.get("insufficient_signal")),
            }
            for s in report.get("competency_scores", [])
        ],
        "coverage_gaps": report.get("coverage_gaps", []),
    }


async def _names(session, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {u.id: u.name for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()}


@router.get("/candidates/{candidate_id}/results")
async def results(candidate_id: str, user: User = Depends(current_user)) -> dict:
    """Everything the BU head reads before deciding. HR and admins read it too."""
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(Candidate, Job).join(Job, Candidate.job_id == Job.id)
                .where(Candidate.id == candidate_id, job_scope(user))
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="No such candidate.")
        candidate, job = row
        screening = (
            await session.execute(select(Screening).where(Screening.candidate_id == candidate.id))
        ).scalar_one_or_none()
        hr = await _latest_hr(session, candidate.id)
        technical = await technical_report(session, candidate.id)
        decisions = (
            await session.execute(
                select(DecisionRecord).where(DecisionRecord.candidate_id == candidate.id).order_by(DecisionRecord.id)
            )
        ).scalars().all()
        names = await _names(session, {d.actor_user_id for d in decisions if d.actor_user_id})

    fit = _fit(screening)
    evaluation = _evaluation(hr)
    strengths, gaps = strengths_and_gaps(fit, evaluation)
    stage = S(candidate.stage)
    role = Role(user.role)
    is_bu_head = role is Role.BU_HEAD
    sent = next((d for d in reversed(decisions) if d.to_stage == S.WITH_BU_HEAD.value), None)
    weights = _weight_percents(fit) if fit else {}

    return {
        "candidate": {"id": candidate.id, "name": candidate.name or "Unnamed candidate", "stage": candidate.stage},
        "job": {"id": job.id, "title": job.title, "bu_name": job.business_unit},
        "jd_match": {
            "score_percent": fit.score_percent,
            "assessed_weight_percent": fit.assessed_weight_percent,
            "threshold_percent": fit.threshold_percent,
            "min_assessed_percent": MIN_ASSESSED_WEIGHT_PERCENT,
            "enough_evidence": fit.enough_evidence,
            "passes_screen": fit.passes_screen,
            "summary": fit.summary,
            "experience_note": fit.experience_note,
        } if fit else None,
        "rubric": [
            {
                "competency_id": r.competency_id, "name": r.competency_name,
                "weight_percent": weights[r.competency_id], "rating": r.rating,
                "evidence": r.evidence, "why": r.reasoning,
            }
            for r in sorted(fit.ratings, key=lambda r: -r.weight)
        ] if fit else [],
        "hr_round": {
            "recommendation": evaluation.recommendation.value,
            "summary": evaluation.summary,
            "ratings": [r.model_dump(mode="json") for r in evaluation.ratings],
            "findings_for_technical": evaluation.findings_for_technical,
            "notes": hr.notes or None,
            "attribution_confidence": (hr.attribution or {}).get("confidence"),
            "evaluated_at": hr.evaluated_at.isoformat() if hr.evaluated_at else None,
        } if evaluation else None,
        "technical": {
            "report": _technical_headline(technical[1]) if technical else None,
            "interview_id": technical[0].id if technical else None,
        },
        "strengths": strengths,
        "gaps": gaps,
        "sent_to_bu_head": {
            "at": sent.created_at.isoformat() if sent.created_at else None,
            "by": names.get(sent.actor_user_id),
            "note": sent.reason,
        } if sent else None,
        "decisions": [
            {
                "from": d.from_stage, "to": d.to_stage, "actor": d.actor,
                "actor_name": names.get(d.actor_user_id) if d.actor_user_id else None,
                "reason": d.reason, "model_recommendation": d.model_recommendation,
                "at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in decisions
        ],
        "can_decide": is_bu_head and stage in DECIDING,
        "next": [s.value for s in allowed_next(stage, Actor.BU_HEAD)] if is_bu_head and stage in DECIDING else [],
    }


@router.get("/jobs/{job_id}/compare")
async def compare(
    job_id: str,
    include_closed: bool = Query(False, description="Also show rejected, parked and withdrawn candidates."),
    user: User = Depends(current_user),
) -> dict:
    """Every screened candidate on one job, side by side on the same yardstick."""
    async with get_sessionmaker()() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id, job_scope(user)))).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="No such job request.")
        rows = (
            await session.execute(
                select(Candidate, Screening)
                .outerjoin(Screening, Screening.candidate_id == Candidate.id)
                .where(Candidate.job_id == job.id)
                .order_by(Candidate.created_at)
            )
        ).all()
        ids = [c.id for c, _ in rows]
        hr_rows = (
            await session.execute(
                select(HRRound).where(HRRound.candidate_id.in_(ids), HRRound.evaluation.isnot(None))
                .order_by(HRRound.id)
            )
        ).scalars().all() if ids else []
        latest_hr = {h.candidate_id: h for h in hr_rows}
        reports = {}
        for cid in ids:
            found = await technical_report(session, cid)
            if found:
                reports[cid] = found[1]

    # The yardstick is the job's own competencies, heaviest first. A spec is
    # preferred; a job screened before its spec moved keeps the fit's list.
    competencies: list[dict] = []
    spec = job.evaluation_spec or {}
    spec_comps = spec.get("competencies") or []
    if spec_comps:
        total = sum(float(c.get("weight", 0)) for c in spec_comps) or 1.0
        competencies = [
            {"id": c["id"], "name": c.get("name", c["id"]), "weight_percent": round(float(c.get("weight", 0)) / total * 100, 1)}
            for c in sorted(spec_comps, key=lambda c: -float(c.get("weight", 0)))
            if c.get("id")
        ]

    candidates = []
    closed_hidden = unscreened = 0
    for c, s in rows:
        stage = S(c.stage)
        if stage in NOT_SCREENED:
            unscreened += 1
            continue
        if stage in CLOSED and not include_closed:
            closed_hidden += 1
            continue
        fit = _fit(s)
        evaluation = _evaluation(latest_hr.get(c.id))
        if fit and not competencies:
            weights = _weight_percents(fit)
            competencies = [
                {"id": r.competency_id, "name": r.competency_name, "weight_percent": weights[r.competency_id]}
                for r in sorted(fit.ratings, key=lambda r: -r.weight)
            ]
        report = reports.get(c.id)
        candidates.append({
            "id": c.id, "name": c.name or "Unnamed candidate", "stage": c.stage,
            "score_percent": fit.score_percent if fit else None,
            "assessed_weight_percent": fit.assessed_weight_percent if fit else None,
            "enough_evidence": fit.enough_evidence if fit else None,
            "passes_screen": fit.passes_screen if fit else None,
            "ratings": {r.competency_id: r.rating for r in fit.ratings} if fit else {},
            "hr_round": {
                "recommendation": evaluation.recommendation.value,
                "ratings": {r.name: r.rating for r in evaluation.ratings},
            } if evaluation else None,
            "technical": {"recommendation": report.get("recommendation")} if report else None,
        })

    from hiring.hr_evaluation import SOFT_SKILLS

    return {
        "job": {"id": job.id, "title": job.title, "bu_name": job.business_unit},
        "competencies": competencies,
        "soft_skills": [name for _, name, _ in SOFT_SKILLS],
        "candidates": candidates,
        "hidden": {"closed": closed_hidden, "not_screened": unscreened},
        "include_closed": include_closed,
        "threshold_percent": _threshold(rows),
    }


def _threshold(rows) -> float | None:
    for _, s in rows:
        fit = _fit(s)
        if fit:
            return fit.threshold_percent
    return None
