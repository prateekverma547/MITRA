"""Every call to a model, and what each stage of hiring costs. Track H.

The org admin's "AI calls" view is a trace of every model call MITRA makes,
like an observability tool: stage, model, prompt version, tokens, latency,
status and cost. "Cost" adds it up by stage of the journey, model, business
unit and job, per candidate and per hire.

What is recorded is metadata only (`hiring.models.ModelCall`): never the
prompt, so never a CV, a transcript or anything else personal (DPDP Act 2023).
A short preview of the model's own output is kept so a person can see what a
call produced.

Recording can never break the call it records: `record` catches everything and
logs. Prices are a dated table below, overridable with `MODEL_PRICES_JSON`; a
model with no known price is shown as "price not set", never guessed.

The technical interview is recorded by the interview engine itself, in
`Interview.session_metrics["usage"]`; it is read from there, not copied here.
Building MITRA's plan and scoring the technical report do not record their
usage yet, and are shown as not measured.
"""

import json
import os
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import or_, select

from app.db import Candidate, Interview, Job, get_sessionmaker
from hiring.api import router
from hiring.models import DecisionRecord, ModelCall
from org.auth import require_role
from org.models import BusinessUnit, User
from shared.stages import Role

# ---- stages ---------------------------------------------------------------------------------

#: The journey's order, and how each stage is named on screen.
STAGES: list[tuple[str, str]] = [
    ("job_understanding", "Understanding the job"),
    ("cv_screening", "CV screening"),
    ("hr_questions", "HR round questions"),
    ("hr_transcription", "HR round transcription"),
    ("hr_evaluation", "HR round evaluation"),
    ("technical_plan", "MITRA's interview plan"),
    ("technical_interview", "MITRA interview"),
    ("technical_scoring", "Technical report scoring"),
    ("other", "Other"),
]
STAGE_LABELS = dict(STAGES)
STAGE_ORDER = {key: i for i, (key, _) in enumerate(STAGES)}
#: Stages whose model usage is not recorded yet, and why.
NOT_MEASURED = {
    "technical_plan": "Building MITRA's plan does not report its usage yet.",
    "technical_scoring": "Scoring the technical report does not report its usage yet.",
}

# ---- prices ---------------------------------------------------------------------------------

#: USD. Text models: per 1M input, cached input and output tokens. Transcription:
#: per minute of audio. Realtime: per 1M text and audio tokens, in and out.
#: Taken from OpenAI's published API pricing as known on 2026-11-19; check
#: https://openai.com/api/pricing before relying on a figure, and override any
#: of them with MODEL_PRICES_JSON (same shape, keyed by model name).
PRICE_SOURCE = "OpenAI API pricing, as known on 2026-11-19 (override with MODEL_PRICES_JSON)"
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "cached_input": 0.025, "output": 0.40},
    "gpt-4o": {"input": 2.50, "cached_input": 1.25, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.60},
    "gpt-4o-transcribe": {"per_minute": 0.006},
    "gpt-4o-transcribe-diarize": {"per_minute": 0.006},
    "gpt-4o-mini-transcribe": {"per_minute": 0.003},
    "gpt-realtime": {"text_in": 4.00, "text_in_cached": 0.40, "text_out": 16.00,
                     "audio_in": 32.00, "audio_in_cached": 0.40, "audio_out": 64.00},
    "gpt-realtime-mini": {"text_in": 0.60, "text_in_cached": 0.06, "text_out": 2.40,
                          "audio_in": 10.00, "audio_in_cached": 0.30, "audio_out": 20.00},
}

_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def prices() -> dict[str, dict[str, float]]:
    table = dict(DEFAULT_PRICES)
    raw = os.environ.get("MODEL_PRICES_JSON")
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                table.update({str(k): v for k, v in extra.items() if isinstance(v, dict)})
        except ValueError:
            logger.warning("MODEL_PRICES_JSON is not valid JSON; using the built-in prices")
    return table


def price_for(model: str | None) -> dict[str, float] | None:
    """The price of a model, matching a dated snapshot to its base name. None if unknown."""
    if not model:
        return None
    table = prices()
    return table.get(model) or table.get(_DATED.sub("", model))


def text_cost(model: str, *, input_tokens: int, cached_input_tokens: int = 0, output_tokens: int = 0,
              audio_seconds: float | None = None) -> float | None:
    """USD for one call, or None when the model has no known price."""
    p = price_for(model)
    if p is None:
        return None
    if "per_minute" in p:
        if audio_seconds is None:
            return None
        return round(audio_seconds / 60 * p["per_minute"], 6)
    if "input" not in p:
        return None
    cached = min(cached_input_tokens, input_tokens)
    usd = ((input_tokens - cached) * p["input"] + cached * p.get("cached_input", p["input"])
           + output_tokens * p["output"]) / 1_000_000
    return round(usd, 6)


def realtime_cost(model: str | None, usage: dict) -> tuple[float | None, bool]:
    """USD for a technical interview from the engine's token counts, and whether it is priced."""
    p = price_for(model)
    if p is not None and "audio_in" in p:
        cached_audio = usage.get("cached_input_audio_tokens", 0) or 0
        cached_text = max((usage.get("cached_input_tokens", 0) or 0) - cached_audio, 0)
        audio_in = max((usage.get("input_audio_tokens", 0) or 0) - cached_audio, 0)
        text_in = max((usage.get("input_tokens", 0) or 0) - (usage.get("input_audio_tokens", 0) or 0) - cached_text, 0)
        audio_out = usage.get("output_audio_tokens", 0) or 0
        text_out = max((usage.get("output_tokens", 0) or 0) - audio_out, 0)
        usd = (audio_in * p["audio_in"] + cached_audio * p["audio_in_cached"] + text_in * p["text_in"]
               + cached_text * p["text_in_cached"] + audio_out * p["audio_out"] + text_out * p["text_out"]) / 1_000_000
        return round(usd, 6), True
    # The engine prices its own sessions; when our table does not know the model, use its figure.
    estimate = usage.get("estimated_usd")
    if isinstance(estimate, (int, float)):
        return round(float(estimate), 6), True
    return None, False


# ---- recording ------------------------------------------------------------------------------

_context: ContextVar[dict | None] = ContextVar("model_call_context", default=None)


@contextmanager
def context(**ids: str | None):
    """Ids a recorded call belongs to (job_id, candidate_id, interview_id), for code that does not pass them."""
    token = _context.set({**(_context.get() or {}), **{k: v for k, v in ids.items() if v}})
    try:
        yield
    finally:
        _context.reset(token)


def started() -> float:
    return time.monotonic()


def _usage(response: Any) -> dict:
    """Token counts from an OpenAI response, whatever its shape."""
    usage = getattr(response, "usage", None) if response is not None else None
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}
    get = usage.get if isinstance(usage, dict) else (lambda k, u=usage: getattr(u, k, None))

    def nested(name: str, key: str) -> int:
        detail = get(name)
        if detail is None:
            return 0
        value = detail.get(key) if isinstance(detail, dict) else getattr(detail, key, None)
        return int(value or 0)

    out = {
        "input_tokens": int(get("prompt_tokens") or get("input_tokens") or 0),
        "output_tokens": int(get("completion_tokens") or get("output_tokens") or 0),
        "cached_input_tokens": nested("prompt_tokens_details", "cached_tokens")
        or nested("input_token_details", "cached_tokens"),
        "reasoning_tokens": nested("completion_tokens_details", "reasoning_tokens") or None,
    }
    if get("type") == "duration" and get("seconds") is not None:
        out["audio_seconds"] = float(get("seconds"))
    return out


async def _org_for(session, job_id: str | None, candidate_id: str | None, interview_id: str | None) -> tuple[str | None, str | None]:
    """The org (and job) a call belongs to, from whichever id it has."""
    if interview_id and not candidate_id:
        interview = await session.get(Interview, interview_id)
        candidate_id = interview.candidate_id if interview else None
    if candidate_id and not job_id:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is not None:
            job_id = candidate.job_id
            if candidate.org_id:
                return candidate.org_id, job_id
    if job_id:
        job = await session.get(Job, job_id)
        return (job.org_id if job else None), job_id
    return None, job_id


async def record(
    *,
    stage: str,
    purpose: str,
    model: str,
    started_at: float,
    response: Any = None,
    error: BaseException | str | None = None,
    status: str | None = None,
    prompt_version: str | None = None,
    audio_seconds: float | None = None,
    summary: dict | None = None,
    output_preview: str | None = None,
    job_id: str | None = None,
    candidate_id: str | None = None,
    interview_id: str | None = None,
) -> None:
    """Write one ModelCall. Never raises: a failure to record never breaks the call it records."""
    try:
        ids = _context.get() or {}
        job_id = job_id or ids.get("job_id")
        candidate_id = candidate_id or ids.get("candidate_id")
        interview_id = interview_id or ids.get("interview_id")
        tokens = _usage(response)
        seconds = audio_seconds if audio_seconds is not None else tokens.get("audio_seconds")
        if error is not None and response is None:
            cost = 0.0  # a request that failed before an answer is not billed
        else:
            cost = text_cost(
                model, input_tokens=tokens.get("input_tokens", 0), cached_input_tokens=tokens.get("cached_input_tokens", 0),
                output_tokens=tokens.get("output_tokens", 0), audio_seconds=seconds,
            )
        async with get_sessionmaker()() as session:
            org_id, job_id = await _org_for(session, job_id, candidate_id, interview_id)
            session.add(ModelCall(
                org_id=org_id, stage=stage if stage in STAGE_LABELS else "other", purpose=purpose[:128],
                model=model or "unknown", prompt_version=prompt_version,
                status=status or ("error" if error is not None else "ok"),
                error=str(error)[:2000] if error is not None else None,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                input_tokens=tokens.get("input_tokens", 0), cached_input_tokens=tokens.get("cached_input_tokens", 0),
                output_tokens=tokens.get("output_tokens", 0), reasoning_tokens=tokens.get("reasoning_tokens"),
                audio_seconds=seconds, cost_usd=cost, price_known=cost is not None,
                job_id=job_id, candidate_id=candidate_id, interview_id=interview_id,
                summary=summary, output_preview=(output_preview or "")[:280] or None,
            ))
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"could not record a {stage} model call: {exc}")


# ---- reading ----------------------------------------------------------------------------------

_admin = require_role(Role.ORG_ADMIN)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _in_org(user: User):
    # One organisation per deployment; a call recorded before its job existed has no org.
    return or_(ModelCall.org_id == user.org_id, ModelCall.org_id.is_(None))


def _call_row(c: ModelCall, names: dict, jobs: dict) -> dict:
    job = jobs.get(c.job_id)
    return {
        "id": str(c.id),
        "at": _utc(c.created_at).isoformat() if c.created_at else None,
        "stage": c.stage, "stage_label": STAGE_LABELS.get(c.stage, c.stage), "purpose": c.purpose,
        "model": c.model, "prompt_version": c.prompt_version, "status": c.status,
        "latency_ms": c.latency_ms, "input_tokens": c.input_tokens, "cached_input_tokens": c.cached_input_tokens,
        "output_tokens": c.output_tokens, "reasoning_tokens": c.reasoning_tokens, "audio_seconds": c.audio_seconds,
        "cost_usd": c.cost_usd, "price_known": c.price_known,
        "candidate": {"id": c.candidate_id, "name": names.get(c.candidate_id)} if c.candidate_id else None,
        "job": {"id": c.job_id, "title": job.title if job else None} if c.job_id else None,
        "interview_id": c.interview_id,
    }


async def _technical_rows(session, user: User, since: datetime | None) -> list[dict]:
    """One trace row per technical interview, from the engine's own usage record."""
    query = (
        select(Interview, Candidate, Job)
        .join(Candidate, Interview.candidate_id == Candidate.id)
        .join(Job, Candidate.job_id == Job.id)
        .where(Interview.round == "technical", Interview.session_metrics.isnot(None))
    )
    rows = []
    for interview, candidate, job in (await session.execute(query)).all():
        if job.org_id and job.org_id != user.org_id:
            continue
        metrics = interview.session_metrics or {}
        usage = metrics.get("usage") or {}
        if not usage:
            continue
        at = _utc(interview.ended_at or interview.started_at or interview.created_at)
        if since and at and at < since:
            continue
        model = metrics.get("realtime_model") or metrics.get("llm_model") or "realtime"
        cost, known = realtime_cost(model, usage)
        minutes = usage.get("minutes")
        rows.append({
            "id": f"int:{interview.id}",
            "at": at.isoformat() if at else None,
            "stage": "technical_interview", "stage_label": STAGE_LABELS["technical_interview"],
            "purpose": "MITRA's voice interview", "model": model, "prompt_version": None,
            "status": "ok" if interview.status == "completed" else interview.status,
            "latency_ms": int(minutes * 60_000) if isinstance(minutes, (int, float)) else None,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0), "reasoning_tokens": None,
            "audio_seconds": round(minutes * 60, 1) if isinstance(minutes, (int, float)) else None,
            "cost_usd": cost, "price_known": known,
            "candidate": {"id": candidate.id, "name": candidate.name}, "job": {"id": job.id, "title": job.title},
            "interview_id": interview.id,
            "_bu": job.bu_id,
            "_detail": {
                "summary": {
                    "responses": usage.get("responses"), "audio_in_tokens": usage.get("input_audio_tokens"),
                    "audio_out_tokens": usage.get("output_audio_tokens"), "minutes": minutes,
                    "ended_because": metrics.get("ended_because"),
                },
                "output_preview": None,
                "cost_note": None if price_for(model) else "The interview engine's own estimate.",
            },
        })
    return rows


async def _all_rows(session, user: User, since: datetime | None) -> list[dict]:
    query = select(ModelCall).where(_in_org(user))
    if since:
        query = query.where(ModelCall.created_at >= since)
    calls = list((await session.execute(query.order_by(ModelCall.created_at.desc(), ModelCall.id.desc()))).scalars())
    cand_ids = {c.candidate_id for c in calls if c.candidate_id}
    job_ids = {c.job_id for c in calls if c.job_id}
    names = {cid: name for cid, name in (await session.execute(
        select(Candidate.id, Candidate.name).where(Candidate.id.in_(cand_ids)))).all()} if cand_ids else {}
    jobs = {j.id: j for j in (await session.execute(select(Job).where(Job.id.in_(job_ids)))).scalars()} if job_ids else {}
    rows = []
    for c in calls:
        row = _call_row(c, names, jobs)
        job = jobs.get(c.job_id)
        row["_bu"] = job.bu_id if job else None
        rows.append(row)
    rows += await _technical_rows(session, user, since)
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


@router.get("/transparency/calls")
async def list_calls(
    stage: str | None = None,
    model: str | None = None,
    status: str | None = Query(None, pattern="^(ok|error|schema_error)$"),
    q: str = "",
    since: str | None = None,
    until: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    user: User = Depends(_admin),
) -> dict:
    """Every model call, newest first: the trace view."""
    def day(value: str | None, end: bool = False) -> datetime | None:
        if not value:
            return None
        try:
            d = datetime.fromisoformat(value).replace(tzinfo=UTC)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"{value!r} is not a date.") from None
        return d + timedelta(days=1) if end else d

    start, end = day(since), day(until, end=True)
    async with get_sessionmaker()() as session:
        rows = await _all_rows(session, user, start)
    needle = q.strip().lower()
    items = []
    for r in rows:
        at = datetime.fromisoformat(r["at"]) if r["at"] else None
        if end and at and at >= end:
            continue
        if stage and r["stage"] != stage:
            continue
        if model and r["model"] != model:
            continue
        if status and r["status"] != status:
            continue
        if needle:
            hay = " ".join(filter(None, [(r["candidate"] or {}).get("name"), (r["job"] or {}).get("title"), r["purpose"]])).lower()
            if needle not in hay:
                continue
        items.append(r)
    total = len(items)
    cost = sum(r["cost_usd"] or 0 for r in items)
    first = (page - 1) * page_size
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [_public(r) for r in items[first:first + page_size]],
        "facets": {
            "stages": [{"key": k, "label": label} for k, label in STAGES if any(r["stage"] == k for r in rows)],
            "models": sorted({r["model"] for r in rows}),
        },
        "summary": {
            "calls": total, "cost_usd": round(cost, 4), "unpriced": sum(1 for r in items if not r["price_known"]),
            "errors": sum(1 for r in items if r["status"] in ("error", "schema_error")),
        },
    }


@router.get("/transparency/calls/{call_id}")
async def call_detail(call_id: str, user: User = Depends(_admin)) -> dict:
    """One call: its metadata, the output preview, timings and cost breakdown."""
    async with get_sessionmaker()() as session:
        if call_id.startswith("int:"):
            rows = [r for r in await _technical_rows(session, user, None) if r["id"] == call_id]
            if not rows:
                raise HTTPException(status_code=404, detail="No such call.")
            row = rows[0]
            return _public(row) | row["_detail"] | {"price": price_for(row["model"]), "price_source": PRICE_SOURCE, "error": None}
        try:
            numeric = int(call_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="No such call.") from None
        call = await session.get(ModelCall, numeric)
        if call is None or (call.org_id and call.org_id != user.org_id):
            raise HTTPException(status_code=404, detail="No such call.")
        names = {}
        if call.candidate_id:
            cand = await session.get(Candidate, call.candidate_id)
            names = {call.candidate_id: cand.name} if cand else {}
        jobs = {}
        if call.job_id:
            job = await session.get(Job, call.job_id)
            jobs = {call.job_id: job} if job else {}
    return _call_row(call, names, jobs) | {
        "error": call.error, "summary": call.summary or {}, "output_preview": call.output_preview,
        "price": price_for(call.model), "price_source": PRICE_SOURCE, "cost_note": None,
    }


@router.get("/transparency/costs")
async def costs(days: int = Query(30, ge=0, le=3650), user: User = Depends(_admin)) -> dict:
    """What MITRA's models cost, by stage of the journey, model, unit and job; per candidate and per hire."""
    now = datetime.now(UTC)
    since = now - timedelta(days=days) if days else None
    async with get_sessionmaker()() as session:
        rows = await _all_rows(session, user, since)
        bus = {b.id: b.name for b in (await session.execute(
            select(BusinessUnit).where(BusinessUnit.org_id == user.org_id))).scalars()}
        hires_query = select(DecisionRecord.candidate_id).where(DecisionRecord.to_stage == "shortlisted")
        if since:
            hires_query = hires_query.where(DecisionRecord.created_at >= since)
        hires = len(set((await session.execute(hires_query)).scalars()))
        trend_rows = await _all_rows(session, user, now - timedelta(weeks=8))

    def money(values) -> float:
        return round(sum(v for v in values if v), 4)

    by_stage = defaultdict(list)
    for r in rows:
        by_stage[r["stage"]].append(r)
    stages = [
        {
            "key": key, "label": label, "cost_usd": money(r["cost_usd"] for r in by_stage[key]),
            "calls": len(by_stage[key]), "unpriced": sum(1 for r in by_stage[key] if not r["price_known"]),
            "not_measured": NOT_MEASURED.get(key),
        }
        for key, label in STAGES if key != "other" or by_stage["other"]
    ]
    models = Counter()
    model_calls = Counter()
    for r in rows:
        models[r["model"]] += r["cost_usd"] or 0
        model_calls[r["model"]] += 1
    units = Counter()
    for r in rows:
        units[bus.get(r["_bu"], "Not linked to a job")] += r["cost_usd"] or 0
    jobs = Counter()
    job_titles = {}
    for r in rows:
        if r["job"]:
            jobs[r["job"]["id"]] += r["cost_usd"] or 0
            job_titles[r["job"]["id"]] = r["job"]["title"]
    per_candidate = defaultdict(float)
    for r in rows:
        if r["candidate"]:
            per_candidate[r["candidate"]["id"]] += r["cost_usd"] or 0
    candidates = len(per_candidate)
    total = money(r["cost_usd"] for r in rows)

    week0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(weeks=7)
    weeks = [week0 + timedelta(weeks=i) for i in range(8)]
    trend = [0.0] * 8
    for r in trend_rows:
        at = datetime.fromisoformat(r["at"]) if r["at"] else None
        if at is None or at < weeks[0]:
            continue
        i = min(7, int((at - weeks[0]).days // 7))
        trend[i] += r["cost_usd"] or 0

    return {
        "days": days,
        "currency": "USD",
        "price_source": PRICE_SOURCE,
        "totals": {
            "cost_usd": total, "calls": len(rows),
            "unpriced": sum(1 for r in rows if not r["price_known"]),
            "candidates": candidates, "hires": hires,
            "per_candidate_usd": round(sum(per_candidate.values()) / candidates, 4) if candidates else None,
            "per_hire_usd": round(total / hires, 4) if hires else None,
        },
        "by_stage": stages,
        "per_candidate_by_stage": [
            {"key": s["key"], "label": s["label"],
             "cost_usd": round(money(r["cost_usd"] for r in by_stage[s["key"]] if r["candidate"]) / candidates, 4) if candidates else None}
            for s in stages if any(r["candidate"] for r in by_stage[s["key"]])
        ],
        "by_model": [{"model": m, "cost_usd": round(c, 4), "calls": model_calls[m], "priced": price_for(m) is not None}
                     for m, c in models.most_common()],
        "by_bu": [{"name": n, "cost_usd": round(c, 4)} for n, c in units.most_common()],
        "by_job": [{"id": j, "title": job_titles[j], "cost_usd": round(c, 4)} for j, c in jobs.most_common(10)],
        "weeks": [w.date().isoformat() for w in weeks],
        "trend_usd": [round(v, 4) for v in trend],
        "not_measured": [{"key": k, "label": STAGE_LABELS[k], "why": why} for k, why in NOT_MEASURED.items()],
    }
