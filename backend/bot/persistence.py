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


def build_transcript(
    *,
    interview_id: str,
    turns: list[dict],
    duration_seconds: float,
    timeline=None,
    clock_zero: float | None = None,
    silero_starts: list[float] | None = None,
    silero_ends: list[float] | None = None,
    plan_started_wall: float | None = None,
) -> Transcript:
    """Turn the observer's raw turns into the persisted contract.

    **Every turn is kept** (task 19). Audio captured before the interviewer
    first spoke is stored as the candidate's speaker slot with
    `pre_interview=True`, and every reader of the record leaves it out
    (`Transcript.interview_turns`). It used to be dropped here, so the raw
    record was already filtered; a live session had recorded a bystander, and a
    hiring record must not attribute that to the candidate, which the flag
    still prevents.

    On the realtime engine (`timeline` given) the turns are timed by when they
    were SPOKEN and stored in that order:
    - a candidate turn takes OpenAI's speech window for its item, refined by
      Silero's edges inside it (`silero`), else the window corrected by the
      measured padding (`openai_window`);
    - Mitra's turns come from the timeline's reply items, timed by playback
      (`playback`); a reply the candidate spoke over is `cut`, its `text` is
      what was heard and the full reply is kept in `generated_text`.
    `at_seconds` keeps its meaning, the arrival of the text. Without a
    timeline (the pipeline engine) turns keep arrival order and times
    (`arrival`).
    """
    built: list[dict] = []
    for order, turn in enumerate(turns):
        speaker = turn.get("speaker")
        pre = speaker == "pre_interview_audio"
        if pre:
            speaker = Speaker.CANDIDATE.value
        if speaker not in (Speaker.CANDIDATE, Speaker.INTERVIEWER):
            continue
        built.append({
            "speaker": speaker,
            "text": turn.get("text", ""),
            "at_seconds": float(turn.get("at_seconds", 0.0)),
            "section_id": turn.get("section_id"),
            "item_id": turn.get("item_id"),
            "pre_interview": pre,
            "timing_source": "arrival",
            "_order": order,
        })

    if timeline is not None and clock_zero is not None:
        built = _time_realtime_turns(
            built, timeline, clock_zero, silero_starts or [], silero_ends or []
        )

    def spoken(t: dict) -> tuple[float, int]:
        start = t.get("audio_start_seconds")
        return (start if start is not None else t["at_seconds"], t["_order"])

    if plan_started_wall is not None and clock_zero is not None:
        # Round 14: the small talk before her first plan question (she starts the plan
        # with move_on) is pre-interview: kept in the record, never scored.
        plan_start = plan_started_wall - clock_zero
        for t in built:
            if spoken(t)[0] < plan_start:
                t["pre_interview"] = True

    kept = [
        TranscriptTurn(index=i, **{k: v for k, v in t.items() if not k.startswith("_")})
        for i, t in enumerate(sorted(built, key=spoken))
    ]
    return Transcript(
        interview_id=interview_id,
        turns=kept,
        duration_seconds=round(duration_seconds, 2),
    )


def _time_realtime_turns(built, timeline, clock_zero, silero_starts, silero_ends) -> list[dict]:
    """Spoken times for the realtime engine. See build_transcript."""
    from bot.realtime_timeline import (
        OPENAI_END_PAD_SECONDS,
        OPENAI_START_PAD_SECONDS,
        SILERO_TOLERANCE_SECONDS,
    )

    def s(wall):
        return None if wall is None else round(wall - clock_zero, 2)

    for turn in built:
        if turn["speaker"] != Speaker.CANDIDATE or not turn["item_id"]:
            continue
        start, end = timeline.user_window_wall(turn["item_id"])
        if start is None or end is None:
            continue
        inside_starts = [x for x in silero_starts
                         if start - SILERO_TOLERANCE_SECONDS <= x <= end]
        inside_ends = [x for x in silero_ends
                       if start <= x <= end + SILERO_TOLERANCE_SECONDS]
        if inside_starts and inside_ends and min(inside_starts) < max(inside_ends):
            turn.update(audio_start_seconds=s(min(inside_starts)),
                        audio_end_seconds=s(max(inside_ends)), timing_source="silero")
        else:
            a, b = start + OPENAI_START_PAD_SECONDS, end - OPENAI_END_PAD_SECONDS
            if b <= a:
                a, b = start, end
            turn.update(audio_start_seconds=s(a), audio_end_seconds=s(b),
                        timing_source="openai_window")

    replies = [a for a in timeline.assistants.values() if a.audio_bytes and a.text]
    if not replies:
        return built  # nothing to rebuild Mitra's side from: keep the observer's turns

    candidates = [t for t in built if t["speaker"] == Speaker.CANDIDATE]
    first_order = min((t["_order"] for t in built), default=0)
    mitra = []
    for n, reply in enumerate(replies):
        start = reply.playback_start_wall
        if start is None:  # never reported as played: place it where its audio arrived
            start = reply.first_delta_wall
        end = reply.playback_end_wall
        if end is None and start is not None:
            # Playback end never reported (the call ended while it played, or no
            # BotStoppedSpeaking came): its start plus the audio's own length, the
            # same estimate as the start's "arrival" fallback. Always recorded, so the
            # after-call check can leave Mitra's voice out (D-58.3).
            end = start + reply.duration_ms / 1000
        if reply.cut and reply.cut_wall is not None:
            end = reply.cut_wall if end is None else min(end, reply.cut_wall)
            end = max(end, start)
        heard = reply.heard()
        mitra.append({
            "speaker": Speaker.INTERVIEWER.value,
            "text": heard,
            "at_seconds": s(reply.last_text_wall or reply.first_delta_wall) or 0.0,
            "section_id": None,
            "item_id": reply.item_id,
            "pre_interview": False,
            "timing_source": "playback" if reply.playback_start_wall is not None else "arrival",
            "audio_start_seconds": s(start),
            "audio_end_seconds": s(end),
            "cut": reply.cut,
            "generated_text": reply.text if reply.cut else None,
            "_order": first_order - len(replies) + n,
        })

    # Spoken before Mitra's first words: pre-interview, whatever the arrival order said.
    first_mitra = min((m["audio_start_seconds"] for m in mitra
                       if m["audio_start_seconds"] is not None), default=None)
    if first_mitra is not None:
        for turn in candidates:
            if turn.get("audio_start_seconds") is not None and turn["audio_start_seconds"] < first_mitra:
                turn["pre_interview"] = True
    return candidates + mitra


def link_waits_to_turns(waits: list[dict], transcript: Transcript) -> list[dict]:
    """Key each reply wait by transcript turn index, through OpenAI's item ids (F-060).

    The latency observer numbers waits with its own counter; a wait records the
    candidate item it followed and the reply item it measured. Waits without
    ids (the pipeline engine) are returned unchanged.
    """
    by_item = {t.item_id: t.index for t in transcript.turns if t.item_id}
    linked = []
    for wait in waits:
        wait = dict(wait)
        wait["turn_index"] = by_item.get(wait.get("user_item_id"))
        wait["reply_turn_index"] = by_item.get(wait.get("reply_item_id"))
        linked.append(wait)
    return linked


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


async def mark_failed_if_unfinished(
    *, interview_id: str, reason: str, ended_because: str | None = None
) -> bool:
    """End an interview that is still `in_progress` as FAILED, with a reason.

    F-017: a bot that fails at start-up, or dies without reaching its `finally`
    block, used to leave the interview `in_progress` for ever: the candidate
    rejoined into an empty room and no new session could be booked. Only an
    interview still `in_progress` is touched, so an interview the bot already
    saved (completed, or failed with its own reason) is never overwritten.
    FAILED is an existing status: no schema change. Never raises.

    `ended_because`, when given, is written to `session_metrics` the way both
    engines record it at the end of a call, so a start-up failure reads
    `ended_because = failed_to_start` (InterviewEvent's EndReason) beside the
    FAILED status. Only into empty metrics: the bot's own record is never
    overwritten.
    """
    try:
        from app.db import Interview, InterviewStatus, get_sessionmaker

        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None or interview.status != InterviewStatus.IN_PROGRESS:
                return False
            interview.status = InterviewStatus.FAILED
            interview.failure_reason = reason
            if ended_because is not None and not interview.session_metrics:
                interview.session_metrics = {"ended_because": ended_because}
            interview.ended_at = datetime.now(UTC)
            await session.commit()
        logger.error(f"[{interview_id}] interview marked failed: {reason}")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] could not mark the interview failed: {exc}")
        return False


async def watch_bot_exit(interview_id: str, process, *, on_exit=None) -> None:
    """Web-process side of F-017: notice the bot subprocess exiting.

    Covers a bot that dies without writing anything (killed, segfault, an
    exception before its own handler). `on_exit` releases the capacity slot at
    once rather than waiting for the next lazy reap.
    """
    returncode = await process.wait()
    if on_exit is not None:
        on_exit()
    await mark_failed_if_unfinished(
        interview_id=interview_id,
        reason=f"The interviewer process exited (code {returncode}) without saving the interview.",
    )


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
    # Cleaning first (A-52, round 4): scoring reads its declared name fixes when it
    # verifies quotes. Both are awaited here and neither can touch the raw transcript;
    # a cleaning failure only means scoring runs without declared fixes.
    if failure_reason is None:
        await _clean_in_the_background(interview_id)
        await _score_in_the_background(interview_id)


async def _clean_in_the_background(interview_id: str) -> None:
    """The cleaned transcript (task 25), scheduled the way scoring is: after the
    commit, awaited in the shutdown path, never able to touch the raw transcript.
    A failure leaves session_metrics["cleaned_transcript"] missing or marked."""
    try:
        from feedback.clean import generate_cleaned_transcript

        await generate_cleaned_transcript(interview_id)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] cleaned transcript could not be written: {exc}")


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


async def write_transcript_zero(interview_id: str, zero_wall: float) -> bool:
    """Store the transcript's zero (UTC) in interviews.transcript_zero_at, for the
    server's clock route (live/clock.py: "exact" once it is set). Joint A + C step.

    Never raises and never blocks the interview: called as its own task.
    """
    try:
        from datetime import UTC, datetime

        from app.db import Interview, get_sessionmaker

        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None:
                logger.warning(f"[{interview_id}] no such interview; transcript zero not stored")
                return False
            interview.transcript_zero_at = datetime.fromtimestamp(zero_wall, UTC)
            await session.commit()
        logger.info(f"[{interview_id}] transcript zero stored")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] transcript zero not stored: {exc!r}")
        return False


async def load_booked_timing(interview_id: str) -> dict | None:
    """The booking's timing for this interview (app/db.py: booked_duration_minutes,
    planned_duration_minutes, planned_grace_minutes; live/booking.py writes them).
    None when the interview has no booking value, or on any failure."""
    try:
        from app.db import Interview, get_sessionmaker

        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None or interview.planned_duration_minutes is None:
                return None
            return {"booked": interview.booked_duration_minutes,
                    "planned": interview.planned_duration_minutes,
                    "grace": interview.planned_grace_minutes}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] booked timing not read: {exc!r}")
        return None
