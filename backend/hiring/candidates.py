"""Candidates on a job request: CV upload, Re-upload or Proceed, screening.

The flow, from docs/TRACK_H.md phase 1:

1. HR adds a candidate to an approved job: a name and a CV. The candidate is
   stage `new`, and the screening row is `waiting`.
2. HR sees the CV as MITRA read it and chooses Re-upload (replace the file,
   still waiting) or Proceed.
3. Proceed starts screening in the background: identity removed, ratings with
   verified quotes, the percentage computed in code, ten HR questions. When it
   finishes the system records the fact (`new` -> `screened`), with the score
   as the model's recommendation.
4. HR then decides at the gate through the ordinary move route. Below the
   cut-off, proceeding needs a written reason (`gate_rules` below).
"""

from datetime import UTC, datetime

from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from sqlalchemy import select

from app.db import Candidate, Job, SpecStatus, get_sessionmaker
from blueprint.documents import DocumentError, extract_text
from bot.config import Settings
from hiring.api import router
from hiring.models import Screening
from hiring.stages import StageError, audit, move
from org.auth import current_user, job_scope, require_role
from org.models import User
from org.users import new_id
from screening.fit import Screener, ScreeningError, screening_model, threshold_percent
from shared.contracts import EvaluationSpec
from shared.screening import MIN_ASSESSED_WEIGHT_PERCENT, FitAnalysis
from shared.stages import Actor, CandidateStage, Role, allowed_next

_hr = require_role(Role.HR)

WAITING, RUNNING, READY, FAILED = "waiting", "running", "ready", "failed"


def _screener() -> Screener:
    settings = Settings.load()
    return Screener(api_key=settings.openai_api_key, model=screening_model(settings.blueprint_model))


async def _job(session, user: User, job_id: str) -> Job:
    job = (await session.execute(select(Job).where(Job.id == job_id, job_scope(user)))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="No such job request.")
    return job


async def _candidate(session, user: User, candidate_id: str) -> tuple[Candidate, Job]:
    row = (
        await session.execute(
            select(Candidate, Job).join(Job, Candidate.job_id == Job.id)
            .where(Candidate.id == candidate_id, job_scope(user))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No such candidate.")
    return row[0], row[1]


async def _screening(session, candidate_id: str) -> Screening:
    screening = (
        await session.execute(select(Screening).where(Screening.candidate_id == candidate_id))
    ).scalar_one_or_none()
    if screening is None:
        screening = Screening(candidate_id=candidate_id, status=WAITING)
        session.add(screening)
        await session.flush()
    return screening


async def _read_cv(file: UploadFile) -> str:
    try:
        return extract_text(filename=file.filename or "cv", data=await file.read())
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _summary(candidate: Candidate, screening: Screening | None) -> dict:
    raw = screening.fit_analysis if screening and screening.fit_analysis else None
    fit = FitAnalysis.model_validate(raw) if raw else None
    return {
        "id": candidate.id,
        "name": candidate.name or "Unnamed candidate",
        "stage": candidate.stage,
        "source_filename": candidate.source_filename,
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        "screening_status": screening.status if screening else WAITING,
        "score_percent": fit.score_percent if fit else None,
        "assessed_weight_percent": fit.assessed_weight_percent if fit else None,
        "threshold_percent": fit.threshold_percent if fit else threshold_percent(),
        "min_assessed_percent": MIN_ASSESSED_WEIGHT_PERCENT,
        "enough_evidence": bool(fit and fit.enough_evidence),
        #: Needs a written reason to proceed: a low score, or too little to judge.
        "below_threshold": bool(fit and not fit.passes_screen),
    }


async def _detail(session, user: User, candidate: Candidate, job: Job) -> dict:
    screening = await _screening(session, candidate.id)
    role = Role(user.role)
    actor = {Role.HR: Actor.HR, Role.BU_HEAD: Actor.BU_HEAD}.get(role)
    is_hr = role is Role.HR
    waiting = screening.status in (WAITING, FAILED) and candidate.stage == CandidateStage.NEW
    return _summary(candidate, screening) | {
        "job": {"id": job.id, "title": job.title, "bu_name": job.business_unit},
        "cv_text": candidate.cv_text,
        "screening_error": screening.error if screening.status == FAILED else None,
        "fit": screening.fit_analysis,
        "hr_questions": screening.hr_questions,
        "next": [s.value for s in allowed_next(CandidateStage(candidate.stage), actor)] if actor else [],
        "can_reupload": is_hr and waiting,
        "can_proceed": is_hr and waiting,
    }


# ---- routes -----------------------------------------------------------------------------


@router.get("/jobs/{job_id}/candidates")
async def list_candidates(job_id: str, user: User = Depends(current_user)) -> dict:
    async with get_sessionmaker()() as session:
        job = await _job(session, user, job_id)
        rows = (
            await session.execute(
                select(Candidate, Screening)
                .outerjoin(Screening, Screening.candidate_id == Candidate.id)
                .where(Candidate.job_id == job.id)
                .order_by(Candidate.created_at.desc())
            )
        ).all()
        return {
            "job": {
                "id": job.id, "title": job.title, "bu_name": job.business_unit,
                "accepting": job.request_status == "open" and job.spec_status == SpecStatus.READY,
            },
            "threshold_percent": threshold_percent(),
            "can_add": Role(user.role) is Role.HR,
            "candidates": [_summary(c, s) for c, s in rows],
        }


@router.post("/jobs/{job_id}/candidates", status_code=201)
async def add_candidate(
    job_id: str,
    name: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(_hr),
) -> dict:
    """Upload a CV for this job. Nothing is screened until HR chooses Proceed."""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Add the candidate's name.")
    cv_text = await _read_cv(file)
    async with get_sessionmaker()() as session:
        job = await _job(session, user, job_id)
        if job.request_status != "open" or job.spec_status != SpecStatus.READY:
            raise HTTPException(
                status_code=409,
                detail="Its BU head is revising this job request. Add candidates once it is approved again.",
            )
        candidate = Candidate(
            id=new_id("cand"), job_id=job.id, name=name[:256], source_filename=file.filename,
            cv_text=cv_text, org_id=job.org_id or user.org_id, stage=CandidateStage.NEW.value,
            spec_version=job.spec_version,
        )
        session.add(candidate)
        await session.flush()
        session.add(Screening(candidate_id=candidate.id, status=WAITING))
        await audit(session, "candidate_added", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"job_id": job.id})
        await session.commit()
        logger.info(f"[{candidate.id}] CV uploaded for {job.id} by {user.id}")
        return await _detail(session, user, candidate, job)


@router.get("/candidates/{candidate_id}")
async def get_candidate(candidate_id: str, user: User = Depends(current_user)) -> dict:
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        detail = await _detail(session, user, candidate, job)
        await session.commit()
        return detail


@router.post("/candidates/{candidate_id}/cv")
async def reupload_cv(candidate_id: str, file: UploadFile = File(...), user: User = Depends(_hr)) -> dict:
    """Replace the CV, for when MITRA read the wrong file or read it badly."""
    cv_text = await _read_cv(file)
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        screening = await _screening(session, candidate.id)
        if candidate.stage != CandidateStage.NEW or screening.status not in (WAITING, FAILED):
            raise HTTPException(status_code=409, detail="Screening has started, so this CV can no longer be replaced.")
        candidate.cv_text = cv_text
        candidate.source_filename = file.filename
        screening.status, screening.error = WAITING, None
        await audit(session, "cv_replaced", by=user, subject_type="candidate", subject_id=candidate.id)
        await session.commit()
        return await _detail(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/proceed", status_code=202)
async def proceed(candidate_id: str, background: BackgroundTasks, user: User = Depends(_hr)) -> dict:
    """HR confirms the CV. Screening runs in the background; the page polls."""
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        screening = await _screening(session, candidate.id)
        if candidate.stage != CandidateStage.NEW or screening.status not in (WAITING, FAILED):
            raise HTTPException(status_code=409, detail="Screening has already started for this candidate.")
        if not job.evaluation_spec:
            raise HTTPException(status_code=409, detail="This job has no finished understanding to screen against.")
        screening.status, screening.error = RUNNING, None
        screening.started_at = datetime.now(UTC)
        await audit(session, "screening_started", by=user, subject_type="candidate", subject_id=candidate.id)
        await session.commit()
        detail = await _detail(session, user, candidate, job)
    background.add_task(run_screening, candidate_id)
    return detail


# ---- the background task --------------------------------------------------------------------


async def run_screening(candidate_id: str) -> None:
    """Screen one candidate. Never raises: a failure is recorded for HR to retry."""
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        job = await session.get(Job, candidate.job_id) if candidate else None
        if candidate is None or job is None or not job.evaluation_spec:
            return
        spec = EvaluationSpec.model_validate(job.evaluation_spec)
        inputs = {"candidate_id": candidate.id, "job_id": job.id, "spec": spec, "jd_text": job.jd_text or ""}
        cv_text, name = candidate.cv_text, candidate.name

    try:
        screener = _screener()
        fit, redacted = await screener.analyse_fit(**inputs, cv_text=cv_text, candidate_name=name)
        questions = await screener.hr_questions(**inputs, redacted_cv=redacted, fit=fit)
    except (ScreeningError, Exception) as exc:  # noqa: BLE001
        logger.exception(f"[{candidate_id}] screening failed")
        async with get_sessionmaker()() as session:
            screening = await _screening(session, candidate_id)
            screening.status = FAILED
            screening.error = str(exc) if isinstance(exc, ScreeningError) else "Screening stopped unexpectedly."
            screening.finished_at = datetime.now(UTC)
            await session.commit()
        return

    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        screening = await _screening(session, candidate_id)
        screening.fit_analysis = fit.model_dump(mode="json")
        screening.hr_questions = questions.model_dump(mode="json")
        screening.status = READY
        screening.finished_at = datetime.now(UTC)
        if candidate.stage == CandidateStage.NEW:
            await move(session, candidate, CandidateStage.SCREENED, by=None,
                       model_recommendation=recommendation(fit))
        await session.commit()
    logger.info(f"[{candidate_id}] screened: {fit.score_percent}% on {fit.assessed_weight_percent}% of the weight")


# ---- the gate ------------------------------------------------------------------------------


async def _hr_round_recommendation(session, candidate_id: str) -> str | None:
    """MITRA's reading of the HR round, recorded beside HR's decision after it."""
    from hiring.hr_evaluation import recommendation_text
    from hiring.models import HRRound
    from shared.screening import HRRoundEvaluation

    hr = (
        await session.execute(
            select(HRRound).where(HRRound.candidate_id == candidate_id, HRRound.evaluation.isnot(None))
            .order_by(HRRound.id.desc())
        )
    ).scalars().first()
    if hr is None:
        return None
    return recommendation_text(HRRoundEvaluation.model_validate(hr.evaluation))


def recommendation(fit: FitAnalysis) -> str:
    side = "below" if fit.below_threshold else "at or above"
    text = (
        f"Screening score {fit.score_percent:g}%, {side} the {fit.threshold_percent:g}% cut-off, "
        f"judged on {fit.assessed_weight_percent:g}% of the job's weight."
    )
    if not fit.enough_evidence:
        text += (
            f" Too little evidence to judge: the CV speaks to less than "
            f"{MIN_ASSESSED_WEIGHT_PERCENT:g}% of what the job asks for."
        )
    return text


async def gate_rules(session, candidate: Candidate, to: CandidateStage, reason: str | None) -> str | None:
    """Extra rules at each gate. Returns the model recommendation to record.

    The recommendation recorded is the one the person had in front of them:
    the screening fit at the screening gate, the HR round evaluation after the
    HR round, and the technical report once the technical round is done.

    Proceeding to the HR round below the cut-off is allowed, decided by a
    person, and needs a written reason, so "proceed anyway" is never a reflex.
    """
    stage = CandidateStage(candidate.stage)
    if stage == CandidateStage.HR_DONE:
        return await _hr_round_recommendation(session, candidate.id)
    if stage in (CandidateStage.TECHNICAL_DONE, CandidateStage.WITH_BU_HEAD, CandidateStage.ON_HOLD):
        from hiring.technical import technical_recommendation

        return await technical_recommendation(session, candidate.id)
    if CandidateStage(candidate.stage) not in (CandidateStage.SCREENED, CandidateStage.PARKED):
        return None
    screening = (
        await session.execute(select(Screening).where(Screening.candidate_id == candidate.id))
    ).scalar_one_or_none()
    if screening is None or not screening.fit_analysis:
        return None
    fit = FitAnalysis.model_validate(screening.fit_analysis)
    if to == CandidateStage.READY_FOR_HR and not fit.passes_screen and not (reason or "").strip():
        why = "scored below the cut-off" if fit.enough_evidence else "has too little evidence in the CV to judge"
        raise StageError(422, f"This candidate {why}. Say why they should go ahead anyway.")
    return recommendation(fit)
