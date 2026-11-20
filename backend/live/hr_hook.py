"""The booking hook: every plan is prepared for its booking (C-124, C-A23).

At booking, Session A's `blueprint.refine.refine_for_booking` is called for EVERY plan,
with or without HR findings (C-A23, round 16). In order, it:
1. refines the plan with the candidate's HR findings, when there are any; Mitra probes
   them neutrally and never mentions HR;
2. sizes the plan to the booked length, listing dropped areas in `not_planned`;
3. writes a goal for each question (`question_goals`).

- Findings source: Track H's HRRoundEvaluation.findings_for_technical, from the
  candidate's latest evaluated HR round (hiring/hr_evaluation.py). The setting
  HR_FINDINGS_FIXTURE, naming a JSON file `{"<candidate id>": ["finding", ...]}`,
  overrides it for tests and local runs.
- No findings, or findings over the length guard: the plan is still sized and given
  goals; the findings are simply not used.
- The booking always succeeds. If the function is missing, raises, times out
  (BOOKING_REFINE_TIMEOUT_SECONDS) or returns something that is not a plan, the
  ORIGINAL plan is kept, and the two notes say so plainly (in the booking answer and the log).
- Costs are stored with the candidate: HR refinement as `blueprint_refine`, goal
  writing as `blueprint_goals` (A's own label).
"""

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.db import Candidate, get_sessionmaker
from shared.contracts import HRFindingsForTechnical, InterviewBlueprint

FIXTURE_ENV = "HR_FINDINGS_FIXTURE"

#: The one guard against absurd input (C-A13): the findings' total length.
#: Track H does not cap their number, so neither does this; but every finding
#: goes into ONE model call, so the total text is what bounds that call's prompt
#: and cost (10,000 characters is about 2,500 tokens, dozens of findings).
#: Over it, the plan is unchanged and the booking says why.
MAX_HR_FINDINGS_CHARS = 10_000


async def hr_findings_for(candidate: Candidate) -> list[str]:
    """The candidate's findings for the technical round: Track H's latest
    evaluated HR round (`HRRoundEvaluation.findings_for_technical`, merge fix of
    2026-11-19). HR_FINDINGS_FIXTURE, when set, still wins, for the suite and
    for local runs without an HR round."""
    path = os.environ.get(FIXTURE_ENV, "").strip()
    if not path:
        from hiring.hr_evaluation import findings_for_technical

        return await findings_for_technical(candidate.id)
    try:
        data = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as exc:
        logger.error(f"HR findings fixture could not be read ({type(exc).__name__}); treated as none")
        return []
    found = data.get(candidate.id) if isinstance(data, dict) else None
    return [str(f) for f in found] if isinstance(found, list) else []


def refinement_function():
    """Session A's `refine_for_booking`, or None while it does not exist."""
    try:
        from blueprint import refine
    except ImportError:
        return None
    return getattr(refine, "refine_for_booking", None)


#: The longest a booking waits for its plan to be prepared (C-A23): up to three text-model
#: calls (HR findings, goals and one goal retry), each normally 10 to 60 s. Past it, the
#: original plan is kept and the booking says so. A choice made without asking (C-176).
BOOKING_REFINE_TIMEOUT_SECONDS = 180.0
#: Round 18: the retry WITHOUT the HR findings (sizing and goal writing: one call,
#: normally 10 to 60 s) has its own, shorter limit, so a booking never waits twice 180 s.
BOOKING_RETRY_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class BookingNotes:
    """What happened to the plan at booking, in plain words (InterviewCreated)."""

    hr_findings_note: str
    plan_note: str


async def refine_plan_for_booking(candidate_id: str, *, booked_minutes: float | None = None) -> BookingNotes:
    """Prepare the candidate's plan for this booking (C-A23). Never raises."""
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None or not candidate.blueprint:
            return BookingNotes("No plan to refine.", "No plan to prepare.")
        stored_plan = candidate.blueprint
        try:
            raw_findings = await hr_findings_for(candidate)
            findings = HRFindingsForTechnical(candidate_id=candidate.id, findings_for_technical=raw_findings)
        except ValueError as exc:
            logger.error(f"[{candidate_id}] HR findings not usable ({exc.__class__.__name__}); not used")
            findings = None
            hr_note = "HR findings could not be read; they were not used."
        else:
            total = sum(len(f) for f in findings.findings_for_technical)
            if not findings.findings_for_technical:
                findings, hr_note = None, "No HR findings."
            elif total > MAX_HR_FINDINGS_CHARS:
                logger.warning(f"[{candidate_id}] {len(findings.findings_for_technical)} HR finding(s), {total} "
                               f"characters, over the {MAX_HR_FINDINGS_CHARS} limit; not used")
                findings = None
                hr_note = (f"HR findings are too long to use ({total} characters; the limit is "
                           f"{MAX_HR_FINDINGS_CHARS}); they were not used.")
            else:
                hr_note = ""  # set from the result below
    prepare = refinement_function()
    if prepare is None:
        logger.warning(f"[{candidate_id}] plan preparation is not available; original plan kept")
        return BookingNotes(hr_note or "HR findings were not used: plan preparation is not available.",
                            "Plan preparation is not available; the original plan is kept.")
    usage: list[dict] = []
    report: dict = {}
    try:
        plan = InterviewBlueprint.model_validate(stored_plan)
        refined = await asyncio.wait_for(
            prepare(plan, findings, booked_minutes=booked_minutes, usage=usage, report=report),
            timeout=BOOKING_REFINE_TIMEOUT_SECONDS)
        if not isinstance(refined, InterviewBlueprint):
            raise ValueError("the preparation did not return a plan")
        if findings is not None and refined.hr_findings is None:
            raise ValueError("the preparation did not record what it did with the HR findings")
    except Exception as exc:  # noqa: BLE001  (TimeoutError included: the booking must still succeed)
        why = "it took too long" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else type(exc).__name__
        if findings is None:
            logger.error(f"[{candidate_id}] plan preparation for the booking failed, original plan kept: {exc!r}")
            await _store_costs(candidate_id, usage)  # a failed call is billed too (C-A14)
            return BookingNotes(
                hr_note or f"Plan refinement with HR findings failed ({why}); the original plan is kept.",
                f"The plan could not be prepared for this booking ({why}); the original plan is kept.")
        # Round 18 (Session A, for C-176): the HR refinement failed. Prepare the plan again
        # WITHOUT the findings, so it is still sized to the booking and gets its goals;
        # only the HR probes are lost. If that fails too, the original plan is kept.
        logger.error(f"[{candidate_id}] plan preparation with HR findings failed ({exc!r}); "
                     f"retrying without the findings")
        try:
            refined = await asyncio.wait_for(
                prepare(plan, None, booked_minutes=booked_minutes, usage=usage, report=report),
                timeout=BOOKING_RETRY_TIMEOUT_SECONDS)
            if not isinstance(refined, InterviewBlueprint):
                raise ValueError("the preparation did not return a plan")
        except Exception as retry_exc:  # noqa: BLE001
            again = ("it took too long" if isinstance(retry_exc, (asyncio.TimeoutError, TimeoutError))
                     else type(retry_exc).__name__)
            logger.error(f"[{candidate_id}] plan preparation without HR findings failed too, original plan "
                         f"kept: {retry_exc!r}")
            await _store_costs(candidate_id, usage)
            return BookingNotes(
                f"Plan refinement with HR findings failed ({why}); the HR findings were not used.",
                f"The plan could not be prepared for this booking, with or without the HR findings "
                f"({again}); the original plan is kept.")
        findings = None
        hr_note = (f"Plan refinement with HR findings failed ({why}), so the HR findings were not used; "
                   f"the plan was still prepared without them.")
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        candidate.blueprint = refined.model_dump(mode="json")
        await session.commit()
    await _store_costs(candidate_id, usage)
    if findings is not None:
        used = sum(1 for f in refined.hr_findings.findings if f.outcome == "probed")
        hr_note = f"Plan refined with HR findings: {used} of {len(refined.hr_findings.findings)} probed."
    note = plan_note(refined, report, booked_minutes)
    logger.info(f"[{candidate_id}] plan prepared for the booking: {hr_note} {note}")
    return BookingNotes(hr_note, note)


def plan_note(plan: InterviewBlueprint, report: dict, booked_minutes: float | None) -> str:
    """One plain sentence on the sizing and the goals, from the plan and A's report."""
    kept = len(plan.competency_plans)
    dropped = [n.name for n in plan.not_planned]
    parts = []
    if booked_minutes is not None:
        size = f"Sized to {booked_minutes:g} minutes: {kept} of {kept + len(dropped)} areas planned"
        parts.append(size + (f" (not planned: {', '.join(dropped)})" if dropped else ""))
    questions = sum(len(p.seed_questions) for p in plan.competency_plans)
    written = sum(1 for p in plan.competency_plans for g in p.question_goals if g)
    goals = (report.get("goals") or {})
    if goals.get("error"):
        parts.append("goal writing failed, so Mitra is shown the plan's own questions")
    else:
        parts.append(f"goals written for {written} of {questions} questions")
    return "; ".join(parts) + "."


#: The job label of a plan REFINEMENT's cost record in candidates.text_model_calls
#: (C-158, D-79). Session A's refinement records itself as job "blueprint", the same as
#: generation; stored here under its own label so the two can be told apart. Rows
#: stored before this keep "blueprint".
REFINE_JOB = "blueprint_refine"


async def _store_costs(candidate_id: str, usage: list[dict]) -> None:
    """Append the booking's cost records to candidates.text_model_calls (C-A14): the HR
    refinement (A records it as "blueprint") labelled `REFINE_JOB`; any other label, such
    as A's `blueprint_goals` for goal writing, kept as A wrote it (C-176)."""
    if not usage:
        return
    labelled = [{**record, "job": REFINE_JOB} if isinstance(record, dict) and record.get("job") == "blueprint"
                else record for record in usage]
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is not None:
            candidate.text_model_calls = [*(candidate.text_model_calls or []), *labelled]
            await session.commit()
