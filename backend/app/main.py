"""Employer panel API: JD upload, clarification chat, CV upload, blueprint retrieval.

Async work uses FastAPI `BackgroundTasks` plus a status column — no queue
(CLAUDE.md). The scheduling rule matters more than the mechanism: **work starts
when its input arrives, never when its output is needed.** Blueprint generation
is kicked off at CV upload, so the employer gets an immediate response and the
blueprint is finished and stored long before anyone joins an interview.
"""

import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from datetime import UTC, datetime

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import (
    BlueprintStatus,
    Candidate,
    ClarificationTurn,
    Job,
    SpecStatus,
    create_all,
    get_sessionmaker,
)
from blueprint.clarify import (
    EMPLOYER_ROLE,
    INTERVIEWER_ROLE,
    ClarificationChat,
    ClarificationError,
)
from blueprint.documents import DocumentError, extract_text
from blueprint.generate import BlueprintGenerationError, BlueprintGenerator
from bot.config import Settings
from shared.branding import BOT_FULL_NAME, BOT_NAME, PRODUCT_TAGLINE
from shared.contracts import EvaluationSpec


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all()
    yield


app = FastAPI(title=f"{BOT_NAME} — {BOT_FULL_NAME}", lifespan=lifespan)

from app.interviews import router as interviews_router  # noqa: E402

app.include_router(interviews_router)


def _settings() -> Settings:
    return Settings.load()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Request / response shapes
# --------------------------------------------------------------------------


class JobCreated(BaseModel):
    job_id: str
    spec_status: str
    first_question: str


class ClarifyRequest(BaseModel):
    message: str


class ClarifyResponse(BaseModel):
    job_id: str
    reply: str
    done: bool
    spec_status: str
    evaluation_spec: dict | None = None
    #: Positions assumed but never stated by the employer. The panel must show
    #: these rather than letting them pass on a skimmed "looks right".
    inferred: list[str] = []


class CandidateCreated(BaseModel):
    candidate_id: str
    job_id: str
    blueprint_status: str
    message: str


class BlueprintResponse(BaseModel):
    candidate_id: str
    job_id: str
    blueprint_status: str
    blueprint: dict | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# JD upload and clarification
# --------------------------------------------------------------------------


@app.post("/jobs", response_model=JobCreated)
async def create_job(background: BackgroundTasks, file: UploadFile = File(...)) -> JobCreated:
    """Upload a job description and get the first clarifying question."""
    try:
        jd_text = extract_text(filename=file.filename or "jd", data=await file.read())
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = _settings()
    chat = ClarificationChat(api_key=settings.openai_api_key, model=settings.blueprint_model)

    try:
        turn = await chat.next_turn(jd_text=jd_text, history=[])
    except ClarificationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    job_id = _new_id("job")
    async with get_sessionmaker()() as session:
        job = Job(
            id=job_id,
            source_filename=file.filename,
            jd_text=jd_text,
            spec_status=SpecStatus.AWAITING_CLARIFICATION,
        )
        job.clarification_turns.append(
            ClarificationTurn(index=0, role=INTERVIEWER_ROLE, content=turn.reply)
        )
        session.add(job)
        await session.commit()

    logger.info(f"[{job_id}] job created from {file.filename}")
    return JobCreated(
        job_id=job_id,
        spec_status=SpecStatus.AWAITING_CLARIFICATION,
        first_question=turn.reply,
    )


@app.post("/jobs/{job_id}/clarify", response_model=ClarifyResponse)
async def clarify(job_id: str, request: ClarifyRequest) -> ClarifyResponse:
    """Answer the current clarifying question and get the next one."""
    async with get_sessionmaker()() as session:
        job = await session.scalar(
            select(Job).where(Job.id == job_id).options(selectinload(Job.clarification_turns))
        )
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job '{job_id}'.")
        if job.spec_status == SpecStatus.READY:
            raise HTTPException(
                status_code=409,
                detail="This job's evaluation spec is already complete.",
            )

        history = [{"role": t.role, "content": t.content} for t in job.clarification_turns]
        history.append({"role": EMPLOYER_ROLE, "content": request.message})

        settings = _settings()
        chat = ClarificationChat(
            api_key=settings.openai_api_key, model=settings.blueprint_model
        )
        try:
            turn = await chat.next_turn(jd_text=job.jd_text, history=history)
        except ClarificationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        next_index = len(job.clarification_turns)
        job.clarification_turns.append(
            ClarificationTurn(index=next_index, role=EMPLOYER_ROLE, content=request.message)
        )
        job.clarification_turns.append(
            ClarificationTurn(index=next_index + 1, role=INTERVIEWER_ROLE, content=turn.reply)
        )

        if turn.done and turn.spec is not None:
            job.evaluation_spec = turn.spec.model_dump(mode="json")
            job.spec_status = SpecStatus.READY
            logger.info(f"[{job_id}] evaluation spec complete: {turn.spec.role_title}")

        await session.commit()

        return ClarifyResponse(
            job_id=job_id,
            reply=turn.reply,
            done=job.spec_status == SpecStatus.READY,
            spec_status=job.spec_status,
            evaluation_spec=job.evaluation_spec,
            inferred=turn.inferred,
        )


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    async with get_sessionmaker()() as session:
        job = await session.scalar(
            select(Job).where(Job.id == job_id).options(selectinload(Job.clarification_turns))
        )
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job '{job_id}'.")
        return {
            "job_id": job.id,
            "source_filename": job.source_filename,
            "spec_status": job.spec_status,
            "evaluation_spec": job.evaluation_spec,
            "clarification": [
                {"role": t.role, "content": t.content} for t in job.clarification_turns
            ],
        }


# --------------------------------------------------------------------------
# CV upload and blueprint generation
# --------------------------------------------------------------------------


@app.get("/jobs")
async def list_jobs() -> list[dict]:
    """Every role being hired for, newest first."""
    async with get_sessionmaker()() as session:
        rows = await session.scalars(select(Job).order_by(Job.created_at.desc()))
        return [
            {
                "job_id": row.id,
                "role_title": (row.evaluation_spec or {}).get("role_title"),
                "source_filename": row.source_filename,
                "spec_status": row.spec_status,
                "created_at": row.created_at,
            }
            for row in rows
        ]


@app.get("/jobs/{job_id}/candidates")
async def list_candidates(job_id: str) -> list[dict]:
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(Candidate)
            .where(Candidate.job_id == job_id)
            .order_by(Candidate.created_at.desc())
        )
        return [
            {
                "candidate_id": row.id,
                "name": row.name,
                "source_filename": row.source_filename,
                "blueprint_status": row.blueprint_status,
                "blueprint_error": row.blueprint_error,
                "created_at": row.created_at,
            }
            for row in rows
        ]


@app.post("/jobs/{job_id}/candidates", response_model=CandidateCreated)
async def create_candidate(
    job_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
) -> CandidateCreated:
    """Upload a CV. Blueprint generation starts immediately in the background.

    Generation is scheduled here, at the moment its input arrives — not lazily
    when someone opens the interview. The employer gets an immediate response
    and the blueprint is ready long before the candidate joins.
    """
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job '{job_id}'.")
        if job.spec_status != SpecStatus.READY or not job.evaluation_spec:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Finish the clarification chat before uploading candidates — "
                    "there is no evaluation spec to build a blueprint against."
                ),
            )

    try:
        cv_text = extract_text(filename=file.filename or "cv", data=await file.read())
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    candidate_id = _new_id("cand")
    async with get_sessionmaker()() as session:
        session.add(
            Candidate(
                id=candidate_id,
                job_id=job_id,
                source_filename=file.filename,
                cv_text=cv_text,
                blueprint_status=BlueprintStatus.PENDING,
            )
        )
        await session.commit()

    background.add_task(generate_blueprint_task, candidate_id)

    logger.info(f"[{candidate_id}] CV uploaded for job {job_id}; generation queued")
    return CandidateCreated(
        candidate_id=candidate_id,
        job_id=job_id,
        blueprint_status=BlueprintStatus.PENDING,
        message="Blueprint generation started. Poll the candidate endpoint for status.",
    )


async def generate_blueprint_task(candidate_id: str) -> None:
    """Background job: build and store the blueprint for one candidate."""
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            logger.error(f"[{candidate_id}] vanished before generation started")
            return
        job = await session.get(Job, candidate.job_id)
        if job is None or not job.evaluation_spec:
            logger.error(f"[{candidate_id}] job or spec missing at generation time")
            return

        candidate.blueprint_status = BlueprintStatus.GENERATING
        spec_payload = job.evaluation_spec
        cv_text = candidate.cv_text
        await session.commit()

    try:
        settings = _settings()
        spec = EvaluationSpec.model_validate(spec_payload)
        generator = BlueprintGenerator(
            api_key=settings.openai_api_key, model=settings.blueprint_model
        )
        blueprint = await generator.generate(
            blueprint_id=candidate_id, spec=spec, cv_text=cv_text
        )
    except (BlueprintGenerationError, Exception) as exc:  # noqa: BLE001
        logger.error(f"[{candidate_id}] blueprint generation failed: {exc}")
        async with sessionmaker() as session:
            candidate = await session.get(Candidate, candidate_id)
            if candidate is not None:
                candidate.blueprint_status = BlueprintStatus.FAILED
                candidate.blueprint_error = str(exc)
                await session.commit()
        return

    async with sessionmaker() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            return
        candidate.blueprint = blueprint.model_dump(mode="json")
        candidate.blueprint_status = BlueprintStatus.READY
        candidate.blueprint_error = None
        candidate.name = blueprint.candidate_name
        candidate.blueprint_generated_at = datetime.now(UTC)
        await session.commit()

    logger.info(
        f"[{candidate_id}] blueprint ready: {len(blueprint.competency_plans)} sections, "
        f"{len(blueprint.claims_to_verify)} claims to verify"
    )


@app.get("/candidates/{candidate_id}", response_model=BlueprintResponse)
async def get_candidate(candidate_id: str) -> BlueprintResponse:
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"No candidate '{candidate_id}'.")
        return BlueprintResponse(
            candidate_id=candidate.id,
            job_id=candidate.job_id,
            blueprint_status=candidate.blueprint_status,
            blueprint=candidate.blueprint,
            error=candidate.blueprint_error,
        )


STATIC_DIR = Path(__file__).parent / "static"


@lru_cache(maxsize=2)
def _render_page(name: str) -> str:
    """The candidate page with branding substituted in.

    Read and rendered once. The name reaches the candidate through the spoken
    introduction, the call, and this page, and all three read from
    shared/branding.py so they cannot drift apart.
    """
    html = (STATIC_DIR / name).read_text()
    for token, value in (
        ("{{BOT_NAME}}", BOT_NAME),
        ("{{BOT_FULL_NAME}}", BOT_FULL_NAME),
        ("{{PRODUCT_TAGLINE}}", PRODUCT_TAGLINE),
    ):
        html = html.replace(token, value)
    return html


@app.get("/join", include_in_schema=False)
async def join_page() -> HTMLResponse:
    """The candidate's entry point.

    Serving this from the backend keeps the candidate on our own domain: they
    see a meeting ID prompt and a consent notice, never a Daily URL.
    """
    return HTMLResponse(_render_page("join.html"))


@app.get("/panel", include_in_schema=False)
async def panel_page() -> HTMLResponse:
    """The employer panel: JD -> clarification -> CV -> blueprint -> interview.

    Unauthenticated for the POC. Multi-tenant auth is explicitly out of scope
    (CLAUDE.md), but this must not be exposed to real candidates or real
    employers as-is: it lists every job and every CV in the database.
    """
    return HTMLResponse(_render_page("panel.html"))


@app.get("/static/panel.js", include_in_schema=False)
async def panel_script() -> Response:
    return Response(_render_page("panel.js"), media_type="application/javascript")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
