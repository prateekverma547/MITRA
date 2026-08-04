"""Writing a finished interview back to the database.

The transcript is the auditable ground truth (CLAUDE.md), so this runs in the
bot's `finally` block — on a clean end, a crash, or a Ctrl-C alike. An interview
that happened but left no record is worse than one that never ran, because the
candidate gave their time and there is nothing to show for it.

Kept out of `bot/brain/`, which must stay pure and framework-free.
"""

from datetime import UTC, datetime, timedelta

from loguru import logger

from shared.contracts import (
    RECORDING_RETENTION_DAYS,
    Speaker,
    Transcript,
    TranscriptTurn,
)


def build_transcript(*, interview_id: str, turns: list[dict], duration_seconds: float) -> Transcript:
    """Turn the observer's raw turns into the persisted contract.

    Audio captured before the interviewer spoke is dropped rather than stored as
    the candidate's words. A live session recorded a bystander in the room; a
    hiring record that attributes a stranger's speech to a candidate is worse
    than one with a small gap in it.
    """
    kept: list[TranscriptTurn] = []
    for turn in turns:
        speaker = turn.get("speaker")
        if speaker not in (Speaker.CANDIDATE, Speaker.INTERVIEWER):
            continue
        kept.append(
            TranscriptTurn(
                index=len(kept),
                speaker=Speaker(speaker),
                text=turn.get("text", ""),
                at_seconds=float(turn.get("at_seconds", 0.0)),
                section_id=turn.get("section_id"),
            )
        )

    return Transcript(
        interview_id=interview_id,
        turns=kept,
        duration_seconds=round(duration_seconds, 2),
    )


async def record_recording_started(*, interview_id: str, offset_seconds: float) -> None:
    """Mark the interview as being recorded, while it still is.

    Written mid-session on purpose. Everything else the bot persists waits for
    the `finally` block, which is right for a transcript that is not complete
    until the end, and wrong for this: a bot that is killed never reaches that
    block, and the sweep that collects and later deletes recordings has no other
    way to learn that one exists. A recording nobody knows about is a file that
    outlives its retention promise.

    Never raises. Failing to note the recording must not end the interview.
    """
    try:
        from app.db import Interview, RecordingStatus, get_sessionmaker

        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None:
                return
            started = datetime.now(UTC)
            interview.recording_status = RecordingStatus.RECORDING
            interview.recording_started_at = started
            interview.recording_offset_seconds = round(offset_seconds, 2)
            interview.recording_error = None
            # The clock starts here, at the interview, and never moves again.
            # Retention is a promise about how long a recording is kept, so a
            # deadline that reset whenever somebody watched it would not be the
            # promise the candidate was shown.
            interview.recording_expires_at = started + timedelta(
                days=RECORDING_RETENTION_DAYS
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"[{interview_id}] the recording started but could not be noted "
            f"against the interview: {exc}. It will not be collected or deleted "
            f"automatically."
        )


async def record_recording_failed(*, interview_id: str, reason: str) -> None:
    """Record that there is no recording, and why.

    The reason is shown to whoever opens the interview. "No recording" with no
    explanation invites the assumption that one is still coming.
    """
    try:
        from app.db import Interview, RecordingStatus, get_sessionmaker

        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None:
                return
            interview.recording_status = RecordingStatus.UNAVAILABLE
            interview.recording_error = reason
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] could not note the failed recording: {exc}")


async def save_interview_result(
    *,
    interview_id: str,
    transcript: Transcript,
    section_outcomes: list[dict],
    session_metrics: dict | None = None,
    failure_reason: str | None = None,
) -> None:
    """Persist the transcript and close out the interview record.

    Never raises: a database problem must not lose the session on top of
    whatever already went wrong. It logs loudly instead — the session JSON on
    disk remains as a fallback copy.
    """
    try:
        from app.db import Interview, InterviewStatus, get_sessionmaker

        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None:
                logger.error(
                    f"[{interview_id}] no such interview to save against; "
                    f"transcript kept only in the session file"
                )
                return

            interview.transcript = transcript.model_dump(mode="json")
            interview.section_outcomes = section_outcomes
            interview.session_metrics = session_metrics or {}
            interview.ended_at = datetime.now(UTC)
            interview.status = (
                InterviewStatus.FAILED if failure_reason else InterviewStatus.COMPLETED
            )
            interview.failure_reason = failure_reason
            await session.commit()

        logger.info(
            f"[{interview_id}] saved: {len(transcript.turns)} turns, "
            f"{transcript.duration_seconds:.0f}s, status="
            f"{'failed' if failure_reason else 'completed'}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"[{interview_id}] COULD NOT SAVE TRANSCRIPT: {exc}. "
            f"The session file on disk is now the only copy."
        )
        return

    # Only now, with the complete transcript committed, does scoring start.
    # Deliberately after the commit and outside its try block: the transcript is
    # the thing that must survive, and a scoring failure must never be able to
    # take it down with it.
    if failure_reason is None:
        await _score_in_the_background(interview_id)


async def _score_in_the_background(interview_id: str) -> None:
    """Kick off scoring at the moment its input exists, not when it is read.

    Awaited here rather than left as a loose task: this runs in the bot's
    shutdown path, and a task abandoned at process exit would never finish. The
    interview is already over, so the wait costs the candidate nothing — and if
    the process dies anyway, `feedback_status` stays retryable.
    """
    try:
        from feedback.run import generate_feedback

        logger.info(f"[{interview_id}] scoring the completed transcript")
        await generate_feedback(interview_id)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"[{interview_id}] feedback could not be generated: {exc}. "
            f"The transcript is saved; scoring can be retried from the panel."
        )
