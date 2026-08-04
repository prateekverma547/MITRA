"""Running the scorer against a saved interview.

**Ordering is the whole point of this module.** Scoring reads the interview row
*after* the transcript has been committed, so it always sees the complete
conversation and never a partial one. It is not incremental, it does not run
between turns, and it cannot touch the conversational path: by the time anything
here executes, the interview is over and the record is closed.

Called from two places, both after the fact:
  - the bot, once it has saved its transcript and is on its way out
  - `POST /interviews/{id}/feedback`, to retry one that failed
"""

from datetime import UTC, datetime

from loguru import logger

from shared.contracts import EvaluationSpec, FeedbackReport, Transcript

from feedback.health import assess, assess_judgment
from feedback.score import FeedbackError, FeedbackScorer


async def generate_feedback(interview_id: str) -> FeedbackReport | None:
    """Score one finished interview and store the report. Never raises.

    A failure here must not corrupt or obscure the transcript, which is the
    thing that actually matters — so it is recorded against the interview and
    left retryable.
    """
    from app.db import (
        Candidate,
        FeedbackStatus,
        Interview,
        InterviewStatus,
        get_sessionmaker,
    )
    from bot.config import Settings

    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            logger.error(f"[{interview_id}] no such interview to score")
            return None

        # Only a finished interview gets scored. A transcript that is still
        # being written is exactly what this must never read.
        if interview.status != InterviewStatus.COMPLETED:
            logger.info(
                f"[{interview_id}] not scoring: status is {interview.status}, "
                f"not completed"
            )
            return None
        if not interview.transcript:
            logger.warning(f"[{interview_id}] completed with no transcript; nothing to score")
            async with sessionmaker() as inner:
                row = await inner.get(Interview, interview_id)
                row.feedback_status = FeedbackStatus.FAILED
                row.feedback_error = "The interview finished without a transcript."
                await inner.commit()
            return None

        candidate = await session.get(Candidate, interview.candidate_id)
        if candidate is None or not candidate.blueprint:
            logger.error(f"[{interview_id}] no blueprint to score against")
            return None

        interview.feedback_status = FeedbackStatus.GENERATING
        interview.feedback_error = None
        await session.commit()

        transcript_payload = interview.transcript
        metrics = interview.session_metrics or {}
        outcomes = interview.section_outcomes or []
        blueprint_payload = candidate.blueprint
        blueprint_id = candidate.id

    try:
        settings = Settings.load()
        transcript = Transcript.model_validate(transcript_payload)
        spec = EvaluationSpec.model_validate(blueprint_payload["evaluation_spec"])

        scorer = FeedbackScorer(
            api_key=settings.openai_api_key, model=settings.feedback_model
        )
        # Derived here, not recorded live, so rebuilding an old report picks
        # up whatever the heuristics have learned since. Both come off the same
        # session metrics the bot already wrote; neither needs a new path.
        health = assess(transcript, metrics, repair_requests=_repairs(metrics))
        judgment_health = assess_judgment(metrics, spec)

        report = await scorer.score(
            interview_id=interview_id,
            blueprint_id=blueprint_id,
            spec=spec,
            transcript=transcript,
            section_outcomes=outcomes,
            contradictions=_contradictions(outcomes),
            health=health,
            judgment_health=judgment_health,
        )
    except (FeedbackError, Exception) as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] feedback generation failed: {exc}")
        async with sessionmaker() as session:
            interview = await session.get(Interview, interview_id)
            if interview is not None:
                interview.feedback_status = FeedbackStatus.FAILED
                interview.feedback_error = str(exc)
                await session.commit()
        return None

    async with sessionmaker() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            return report
        interview.feedback_report = report.model_dump(mode="json")
        interview.feedback_status = FeedbackStatus.READY
        interview.feedback_error = None
        interview.feedback_generated_at = datetime.now(UTC)
        await session.commit()

    scored = sum(1 for s in report.competency_scores if s.score is not None)
    logger.info(
        f"[{interview_id}] feedback ready: {scored}/{len(report.competency_scores)} "
        f"competencies scored, {len(report.coverage_gaps)} gaps"
    )
    return report


def _repairs(metrics: dict) -> int:
    """How many times the interviewer had to ask for something again.

    Recorded by the brain rather than inferred from its wording: guessing at our
    own behaviour from our own transcript would be the least reliable source
    available.
    """
    recorded = metrics.get("repairs_requested")
    if recorded is not None:
        return int(recorded)
    # Interviews recorded before the brain counted these.
    events = metrics.get("brain_events") or []
    return sum(1 for e in events if e.get("kind") == "repair")


def _contradictions(outcomes: list[dict]) -> list[dict]:
    """Carried through from the brain rather than re-derived.

    The brain already judged these during the interview, with a measured
    precision of 1.00 against labelled cases. Asking the scorer to find them
    again from the transcript would be a second, unmeasured judgement about a
    person's honesty.
    """
    found: list[dict] = []
    for outcome in outcomes:
        found.extend(outcome.get("contradictions") or [])
    return found
