"""Start-up clean-up for interviews whose bot died with the web process (wave 1).

A bot is a child of the web process (app/interviews.py `_start_bot`). When the
web process restarts (a deploy, a crash), its bots usually die with it, but
their interviews stayed "in progress" forever: wrong in the record, and a
reloading candidate was refused with no explanation.

At start-up, for every interview marked in progress:
- its bot is ALIVE when the recorded process (`interviews.bot_pid`) exists and
  its command line is `bot.run_bot … --interview-id <this interview>` (a reused
  process id is not mistaken for the bot). Such an interview is never touched:
  the bot is adopted into the registry, so rejoins and capacity see it.
- otherwise it is ended: status failed, `failure_reason` and
  `session_metrics.ended_because` = interrupted_by_restart, `ended_at` now, and
  an `interview_ended` event (source server) on the stream. The reload and
  rejoin refusals then give that reason.

One web process (replicas pinned at 1). With more, each would need to know
which bots are its own (D-43).
"""

import os
import subprocess
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select

from app.db import Interview, InterviewStatus, get_sessionmaker

INTERRUPTED_REASON = ("Interrupted by a server restart: the interviewer stopped and the interview could not "
                      "continue. A problem on our side, not the candidate's.")


def bot_process_alive(pid: int | None, interview_id: str) -> bool:
    """Whether `pid` is this interview's bot, still running."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists; the command line below decides
    try:
        command = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="], capture_output=True,
                                 text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "bot.run_bot" in command and f"--interview-id {interview_id}" in command


async def reconcile_running_interviews(now: datetime | None = None) -> dict[str, list[str]]:
    """Adopt live bots; end interviews whose bot is gone. Returns both lists."""
    from app.capacity import registry
    from live.clock import interview_time_now
    from live.emit import make_event
    from live.store import insert_events
    from shared.interview_contracts import EndReason, EventSource, InterviewEnded

    now = now or datetime.now(UTC)
    adopted, ended = [], []
    async with get_sessionmaker()() as session:
        # Only MITRA's rounds have a bot to lose. An HR round is two people in
        # a Daily room with no process of ours in it, so a restart leaves it
        # exactly as it was (Track H's HR round, hiring/hr_round.py).
        running = list(await session.scalars(
            select(Interview).where(Interview.status == InterviewStatus.IN_PROGRESS,
                                    Interview.round != "hr")))
        for interview in running:
            if registry.is_running(interview.id):
                continue  # this process's own (not the case at start-up)
            if bot_process_alive(interview.bot_pid, interview.id):
                registry.adopt(interview.id, interview.bot_pid)
                adopted.append(interview.id)
                continue
            seconds = interview_time_now(interview, now)["interview_seconds"] or 0.0
            interview.status = InterviewStatus.FAILED
            interview.failure_reason = INTERRUPTED_REASON
            interview.ended_at = now
            metrics = dict(interview.session_metrics or {})
            metrics.setdefault("ended_because", EndReason.INTERRUPTED_BY_RESTART.value)
            interview.session_metrics = metrics
            ended.append((interview.id, seconds))
        await session.commit()
    if ended:
        await insert_events([make_event(iid, InterviewEnded(ended_because=EndReason.INTERRUPTED_BY_RESTART,
                                                            duration_seconds=seconds),
                                        source=EventSource.SERVER, at_seconds=seconds,
                                        event_id=f"{iid}:ended_by_restart") for iid, seconds in ended])
    for iid, _ in ended:
        logger.warning(f"[{iid}] interview ended at start-up: its bot was gone (interrupted by a server restart)")
    return {"adopted": adopted, "ended": [iid for iid, _ in ended]}


def was_interrupted(interview: Interview) -> bool:
    return (interview.session_metrics or {}).get("ended_because") == "interrupted_by_restart"


# -- which engine interviews will use (start-up safety) ------------------------------------------

#: The loud line when the old engine is active. The demo must run OpenAI Realtime.
PIPELINE_WARNING = ("!!! INTERVIEW ENGINE: pipeline. The OLD scripted engine (speech-to-text, "
                    "gpt-4.1-mini, ElevenLabs) is active, NOT OpenAI Realtime. Unset INTERVIEW_ENGINE "
                    "to run realtime. !!!")


def announce_engine() -> str:
    """Log which engine interviews will use, read exactly as the bot reads it
    (bot/run_bot.py: `bot.config.interview_engine()`, unknown values falling back to the
    default). The bot inherits this process's environment (app/interviews.py
    `_start_bot`), so this is what it will run. Logs the engine's name only: never the
    raw value or any other setting."""
    from bot.config import INTERVIEW_ENGINE_ENV, PIPELINE_ENGINE, interview_engine

    engine = interview_engine()
    raw = os.environ.get(INTERVIEW_ENGINE_ENV, "").strip().lower()
    if raw and raw != engine:
        logger.warning(f"{INTERVIEW_ENGINE_ENV} has an unknown value; interviews will use {engine}")
    if engine == PIPELINE_ENGINE:
        logger.warning(PIPELINE_WARNING)
    else:
        logger.info(f"Interview engine: {engine} (OpenAI Realtime)")
    return engine
