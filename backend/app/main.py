"""Employer panel API: JD upload, clarification chat, CV upload, blueprint retrieval.

Async work uses FastAPI `BackgroundTasks` plus a status column — no queue
(CLAUDE.md). The scheduling rule matters more than the mechanism: **work starts
when its input arrives, never when its output is needed.** Blueprint generation
is kicked off at CV upload, so the employer gets an immediate response and the
blueprint is finished and stored long before anyone joins an interview.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from functools import lru_cache
from pathlib import Path
from datetime import UTC, datetime

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Response as FastAPIResponse, UploadFile
from fastapi.responses import HTMLResponse, Response
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import COOKIE_NAME, check_password, issue_token, require_admin
from app.db import (
    BlueprintStatus,
    Candidate,
    ClarificationTurn,
    Interview,
    InterviewStatus,
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
from blueprint.refine import BlueprintRefiner, RefinementError
from bot.config import Settings
from shared.branding import BOT_FULL_NAME, BOT_NAME, FAVICON_URL, LOGO_URL, PRODUCT_TAGLINE
from shared.contracts import (
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
    RECORDING_RETENTION_DAYS,
    EvaluationSpec,
    InterviewBlueprint,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all()

    # Recordings arrive from Daily after the call rather than during it, and
    # they have to be deleted ten days later. Both are clock-driven, neither can
    # hang off a request, and there is no queue in this project by decision, so
    # they run as one background task for the life of the process. Started here
    # so a redeploy also picks up anything the last one left behind.
    from app.recordings import sweep_enabled, sweep_forever

    sweeper = asyncio.create_task(sweep_forever()) if sweep_enabled() else None
    try:
        yield
    finally:
        if sweeper is not None:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper


app = FastAPI(title=f"{BOT_NAME}: {BOT_FULL_NAME}", lifespan=lifespan)

from app.interviews import router as interviews_router  # noqa: E402
from app.recordings import router as recordings_router  # noqa: E402

app.include_router(interviews_router)
app.include_router(recordings_router)


def _settings() -> Settings:
    return Settings.load()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Request / response shapes
# --------------------------------------------------------------------------


class LoginRequest(BaseModel):
    password: str


class RefineRequest(BaseModel):
    message: str


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
    #: Only set when a *revision* completes: which candidates' plans were
    #: regenerated, and which were deliberately left alone. Shown so the reach
    #: of a spec change is visible at the moment it happens.
    propagation: dict | None = None


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
    refinements: list[dict] = []
    #: This plan was built from an older spec than the profile now has. It was
    #: left alone rather than regenerated because the employer hand-refined it.
    plan_is_stale: bool = False


# --------------------------------------------------------------------------
# JD upload and clarification
# --------------------------------------------------------------------------


@app.post("/jobs", response_model=JobCreated, dependencies=[Depends(require_admin)])
async def create_job(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    business_unit: str = Form(""),
) -> JobCreated:
    """Upload a job description and get the first clarifying question.

    `title` and `business_unit` are the employer's own labels, taken here rather
    than derived later: the spec's `role_title` only exists once the
    clarification chat finishes, so without these a profile is unidentifiable in
    the list for the whole time it is being set up — and three Business Analyst
    openings for three different units look identical forever.
    """
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
            title=(title or "").strip() or None,
            business_unit=(business_unit or "").strip() or None,
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


@app.post("/jobs/{job_id}/clarify", response_model=ClarifyResponse, dependencies=[Depends(require_admin)])
async def clarify(
    job_id: str, request: ClarifyRequest, background: BackgroundTasks
) -> ClarifyResponse:
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
                detail=(
                    "This profile's evaluation spec is already complete. "
                    "Reopen it to change what the interview tests."
                ),
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

        # A revision is a spec that already existed and has now changed. The
        # first time through there is nothing downstream to propagate to.
        is_revision = job.evaluation_spec is not None

        if turn.done and turn.spec is not None:
            job.evaluation_spec = turn.spec.model_dump(mode="json")
            job.spec_status = SpecStatus.READY
            if is_revision:
                job.spec_version += 1
            logger.info(f"[{job_id}] evaluation spec complete: {turn.spec.role_title}")

        await session.commit()
        spec_now_ready = job.spec_status == SpecStatus.READY
        spec_payload = job.evaluation_spec

    propagation = None
    if spec_now_ready and is_revision:
        propagation = await _propagate_spec_change(job_id, background)

    return ClarifyResponse(
        job_id=job_id,
        reply=turn.reply,
        done=spec_now_ready,
        spec_status=SpecStatus.READY if spec_now_ready else SpecStatus.AWAITING_CLARIFICATION,
        evaluation_spec=spec_payload,
        inferred=turn.inferred,
        propagation=propagation,
    )


@app.post("/jobs/{job_id}/reopen", dependencies=[Depends(require_admin)])
async def reopen_spec(job_id: str) -> dict:
    """Reopen a finished clarification so the employer can change what is tested.

    The conversation continues where it left off rather than starting over —
    the model still has the JD and everything already agreed, so the employer
    only has to state the change.

    Nothing is regenerated here. Reopening is not the decision; the decision is
    made when the revised spec is confirmed, and `_propagate_spec_change` below
    is what acts on it.
    """
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No profile '{job_id}'.")
        if job.spec_status != SpecStatus.READY:
            raise HTTPException(
                status_code=409,
                detail="This spec is not finished yet, so there is nothing to reopen.",
            )
        job.spec_status = SpecStatus.AWAITING_CLARIFICATION
        await session.commit()

    logger.info(f"[{job_id}] spec reopened for revision")
    return {"job_id": job_id, "spec_status": SpecStatus.AWAITING_CLARIFICATION}


async def _propagate_spec_change(job_id: str, background: BackgroundTasks) -> dict:
    """Decide which candidates a revised spec reaches.

    Three groups, three different answers:

    - **Already interviewed** — untouched, always. Their blueprint is the record
      of what they were actually asked. Rewriting it would mean a later feedback
      report scoring them against competencies that did not exist while they
      were being interviewed, which is a written judgement about a real person
      measured against a yardstick nobody applied to them.
    - **Hand-refined by the employer** — left alone and flagged stale, by
      explicit decision. Regenerating would silently discard the employer's own
      edits; the panel shows these so the choice to refresh stays theirs.
    - **Everyone else** — regenerated against the new spec, in the background.
    """
    regenerated: list[str] = []
    skipped_refined: list[str] = []
    skipped_interviewed: list[str] = []

    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.job_id == job_id)
                .options(selectinload(Candidate.interviews))
            )
        )

        for candidate in candidates:
            interviewed = any(
                i.status in (InterviewStatus.COMPLETED, InterviewStatus.IN_PROGRESS)
                for i in candidate.interviews
            )
            if interviewed:
                skipped_interviewed.append(candidate.id)
                continue
            if candidate.blueprint_refinements:
                skipped_refined.append(candidate.id)
                continue

            candidate.blueprint_status = BlueprintStatus.PENDING
            candidate.spec_version = job.spec_version
            regenerated.append(candidate.id)

        await session.commit()

    for candidate_id in regenerated:
        background.add_task(generate_blueprint_task, candidate_id)

    logger.info(
        f"[{job_id}] spec revised: {len(regenerated)} plans regenerating, "
        f"{len(skipped_refined)} kept (hand-refined), "
        f"{len(skipped_interviewed)} kept (already interviewed)"
    )
    return {
        "regenerated": regenerated,
        "skipped_refined": skipped_refined,
        "skipped_interviewed": skipped_interviewed,
    }


@app.get("/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def get_job(job_id: str) -> dict:
    async with get_sessionmaker()() as session:
        job = await session.scalar(
            select(Job).where(Job.id == job_id).options(selectinload(Job.clarification_turns))
        )
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job '{job_id}'.")
        return {
            "job_id": job.id,
            "title": job.title,
            "business_unit": job.business_unit,
            "source_filename": job.source_filename,
            "spec_status": job.spec_status,
            "spec_version": job.spec_version,
            "evaluation_spec": job.evaluation_spec,
            "clarification": [
                {"role": t.role, "content": t.content} for t in job.clarification_turns
            ],
        }


# --------------------------------------------------------------------------
# CV upload and blueprint generation
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Admin session
# --------------------------------------------------------------------------


@app.post("/admin/login")
async def admin_login(
    body: LoginRequest, request: Request, response: FastAPIResponse
) -> dict:
    if not check_password(body.password):
        raise HTTPException(status_code=401, detail="Incorrect password.")
    response.set_cookie(
        COOKIE_NAME,
        issue_token(),
        httponly=True,  # not readable from JavaScript
        samesite="lax",
        # Secure only when the connection actually is. Hardcoding True means the
        # browser never sends the cookie back over plain HTTP, so login silently
        # succeeds and every subsequent request 401s — which is exactly what it
        # did on localhost.
        secure=request.url.scheme == "https",
        max_age=12 * 60 * 60,
    )
    return {"ok": True}


@app.post("/admin/logout")
async def admin_logout(response: FastAPIResponse) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/admin/session")
async def admin_session(request: Request) -> dict:
    """Lets the panel decide whether to show the login screen."""
    from app.auth import token_is_valid

    return {"signed_in": token_is_valid(request.cookies.get(COOKIE_NAME))}


@app.delete("/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def delete_job(job_id: str) -> dict:
    """Delete a profile and everything under it.

    Cascades to its clarification chat, candidates, blueprints and interviews —
    including transcripts. There is no undo, which is why the panel asks first.
    """
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No profile '{job_id}'.")
        await session.delete(job)
        await session.commit()
    logger.info(f"[{job_id}] profile deleted")
    return {"deleted": job_id}


@app.get("/jobs", dependencies=[Depends(require_admin)])
async def list_jobs() -> list[dict]:
    """Every role being hired for, newest first.

    Carries the candidate tallies so the list answers "where does this one
    stand?" without a request per profile.
    """
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(
                select(Job)
                .order_by(Job.created_at.desc())
                .options(
                    selectinload(Job.candidates).selectinload(Candidate.interviews)
                )
            )
        )
        return [
            {
                "job_id": row.id,
                "title": row.title,
                "business_unit": row.business_unit,
                "role_title": (row.evaluation_spec or {}).get("role_title"),
                "source_filename": row.source_filename,
                "spec_status": row.spec_status,
                "spec_version": row.spec_version,
                "created_at": row.created_at,
                "candidate_count": len(row.candidates),
                "interviewed_count": sum(
                    1
                    for c in row.candidates
                    if any(i.status == InterviewStatus.COMPLETED for i in c.interviews)
                ),
                "stale_count": sum(
                    1 for c in row.candidates if c.spec_version < row.spec_version
                ),
            }
            for row in rows
        ]


@app.get("/jobs/{job_id}/candidates", dependencies=[Depends(require_admin)])
async def list_candidates(job_id: str) -> list[dict]:
    """Everyone lined up for this role, with where their interview stands.

    `interview_status` is the one thing the employer is scanning this list for,
    so it is computed here rather than left to a request per candidate.
    """
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No profile '{job_id}'.")

        rows = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.job_id == job_id)
                .order_by(Candidate.created_at.desc())
                .options(selectinload(Candidate.interviews))
            )
        )
        return [
            {
                "candidate_id": row.id,
                "name": row.name,
                "source_filename": row.source_filename,
                "blueprint_status": row.blueprint_status,
                "blueprint_error": row.blueprint_error,
                "created_at": row.created_at,
                "interview_status": _interview_status(row),
                "has_refinements": bool(row.blueprint_refinements),
                # Planned against a spec the employer has since changed.
                "plan_is_stale": row.spec_version < job.spec_version,
            }
            for row in rows
        ]


def _interview_status(candidate: Candidate) -> str:
    """Where this candidate stands, as one word the panel can show.

    A completed interview outranks everything: it is the fact the employer is
    looking for, and it stays true even if a later session were somehow booked.
    """
    statuses = {i.status for i in candidate.interviews}
    if InterviewStatus.COMPLETED in statuses:
        return "completed"
    if InterviewStatus.IN_PROGRESS in statuses:
        return "in_progress"
    if InterviewStatus.SCHEDULED in statuses:
        return "scheduled"
    if InterviewStatus.FAILED in statuses:
        return "failed"
    if InterviewStatus.EXPIRED in statuses:
        return "expired"
    return "not_scheduled"


@app.post("/jobs/{job_id}/candidates", response_model=CandidateCreated, dependencies=[Depends(require_admin)])
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
                    "Finish the clarification chat before uploading candidates. "
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
        # Recorded now, from the spec actually being used. Reading it back
        # later would risk stamping a version the plan was never built against.
        candidate.spec_version = job.spec_version
        spec_payload = job.evaluation_spec
        cv_text = candidate.cv_text
        # The JD is half the vocabulary of the field, and generation could not
        # see it before.
        jd_text = job.jd_text
        await session.commit()

    try:
        settings = _settings()
        spec = EvaluationSpec.model_validate(spec_payload)
        generator = BlueprintGenerator(
            api_key=settings.openai_api_key, model=settings.blueprint_model
        )
        blueprint = await generator.generate(
            blueprint_id=candidate_id, spec=spec, cv_text=cv_text, jd_text=jd_text
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


@app.get("/candidates/{candidate_id}", response_model=BlueprintResponse, dependencies=[Depends(require_admin)])
async def get_candidate(candidate_id: str) -> BlueprintResponse:
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"No candidate '{candidate_id}'.")
        job = await session.get(Job, candidate.job_id)
        return BlueprintResponse(
            candidate_id=candidate.id,
            job_id=candidate.job_id,
            blueprint_status=candidate.blueprint_status,
            blueprint=candidate.blueprint,
            error=candidate.blueprint_error,
            refinements=candidate.blueprint_refinements or [],
            plan_is_stale=bool(job and candidate.spec_version < job.spec_version),
        )


#: The UIs live at the repository root, outside the Python package: they are
#: their own thing and will become React apps (CLAUDE.md). The Docker image
#: mirrors this layout so the path resolves identically in both places.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


#: One more than the number of pages served, so every page stays cached. At
#: maxsize=2 with three pages the cache thrashed and each request re-read disk.
@lru_cache(maxsize=8)
def _render_page(name: str) -> str:
    """A page with the product's identity substituted in.

    Read and rendered once. The name reaches the candidate through the spoken
    introduction, the call, and this page, and all three read from
    shared/branding.py so they cannot drift apart. The logo's URL comes from
    the same module for the same reason.
    """
    html = (FRONTEND_DIR / name).read_text()
    for token, value in (
        ("{{BOT_NAME}}", BOT_NAME),
        ("{{BOT_FULL_NAME}}", BOT_FULL_NAME),
        ("{{PRODUCT_TAGLINE}}", PRODUCT_TAGLINE),
        ("{{LOGO_URL}}", LOGO_URL),
        ("{{FAVICON_URL}}", FAVICON_URL),
        # The interview-length rule, so the panel states the same numbers the
        # contract enforces instead of its own copy of them.
        ("{{DURATION_MIN}}", str(MIN_DURATION_MINUTES)),
        ("{{DURATION_MAX}}", str(MAX_DURATION_MINUTES)),
        # How long a recording is kept. This one is a promise made to a
        # candidate, and the sweep that deletes recordings reads the same
        # constant, so the notice cannot end up describing a policy nothing
        # enforces.
        ("{{RETENTION_DAYS}}", str(RECORDING_RETENTION_DAYS)),
    ):
        html = html.replace(token, value)
    return html


#: Pages and scripts are rendered from files that change on every deploy, and
#: nothing in their URLs changes with them. Without this the browser is free to
#: reuse whatever it fetched last time and never ask again, which is exactly
#: what it did: a deploy landed, the API served the new data, and the panel kept
#: running last week's JavaScript against it. The bodies are a few tens of KB,
#: so revalidating every time costs nothing worth measuring.
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/join", include_in_schema=False)
async def join_page() -> HTMLResponse:
    """The candidate's entry point.

    Serving this from the backend keeps the candidate on our own domain: they
    see a meeting ID prompt and a consent notice, never a Daily URL.
    """
    return HTMLResponse(_render_page("candidate/index.html"), headers=NO_CACHE)


@app.get("/assets/{filename}", include_in_schema=False)
async def asset(filename: str) -> Response:
    """Brand artwork, for formats that cannot be inlined.

    The SVG mark is inlined into the pages themselves; this exists so a PNG or
    JPEG logo can be dropped in without a code change.
    """
    allowed = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon"}
    path = (FRONTEND_DIR / "assets" / filename).resolve()

    # Refuse anything that climbs out of the assets folder.
    if not path.is_file() or (FRONTEND_DIR / "assets").resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="No such asset.")
    media_type = allowed.get(path.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=404, detail="No such asset.")

    return Response(path.read_bytes(), media_type=media_type,
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/admin", include_in_schema=False)
async def admin_page() -> HTMLResponse:
    """The admin panel: JD -> clarification -> CV -> blueprint -> interview.

    Behind `ADMIN_PASSWORD`: it lists every job description, every CV and every
    interview transcript in the database. The page itself renders the sign-in
    screen, so it is reachable while the data behind it is not.
    """
    return HTMLResponse(_render_page("admin/index.html"), headers=NO_CACHE)


@app.get("/admin/admin.js", include_in_schema=False)
async def admin_script() -> Response:
    return Response(
        _render_page("admin/admin.js"),
        media_type="application/javascript",
        headers=NO_CACHE,
    )


@app.post("/candidates/{candidate_id}/refine", dependencies=[Depends(require_admin)])
async def refine_blueprint(candidate_id: str, request: RefineRequest) -> dict:
    """Change the interview plan by describing what you want changed.

    The revision is validated as strictly as generation is; one that would leave
    the plan uninterviewable is refused and the existing blueprint stands.
    """
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"No candidate '{candidate_id}'.")
        if candidate.blueprint_status != BlueprintStatus.READY or not candidate.blueprint:
            raise HTTPException(
                status_code=409, detail="There is no finished plan to refine yet."
            )
        current = InterviewBlueprint.model_validate(candidate.blueprint)
        cv_text = candidate.cv_text
        history = list(candidate.blueprint_refinements or [])

    settings = _settings()
    refiner = BlueprintRefiner(
        api_key=settings.openai_api_key, model=settings.blueprint_model
    )
    try:
        result = await refiner.refine(
            blueprint=current, cv_text=cv_text, message=request.message, history=history
        )
    except RefinementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        candidate.blueprint = result.blueprint.model_dump(mode="json")
        candidate.blueprint_refinements = history + [
            {"role": "employer", "content": request.message},
            {"role": "assistant", "content": result.reply},
        ]
        await session.commit()

    logger.info(f"[{candidate_id}] blueprint refined")
    return {
        "reply": result.reply,
        "blueprint": result.blueprint.model_dump(mode="json"),
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
