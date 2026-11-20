"""The server is the clock: "interview time now" for one interview.

Interview time is the transcript's clock: seconds since the bot's transcript
zero (TranscriptTurn.at_seconds counts from it). The browser does not keep its
own: it asks here when the call screen opens (GET .../clock), re-syncs from
every intake and status response, and stamps its events in interview time
(joint B + C step, C-46). Then integrity events, the transcript and the
recording share one zero.

- **Exact** when `interviews.transcript_zero_at` is stored. The bot writes it
  (joint A + C step, C-45): today nothing does, so it is always null.
- **Estimate** otherwise: seconds since `started_at` (set just before the bot is
  spawned) minus `ESTIMATE_OFFSET_SECONDS`, the time from the spawn to the
  bot's transcript zero. Measured once: 1.28 s (task 19 live run on a
  developer's laptop: "bot spawned" logged at 01:23:16.436, transcript zero
  01:23:17.719). One sample, a warm machine; a cold container on Railway is
  likely slower, so expect an error of a second or more, and say "estimate".
  Configurable with INTERVIEW_CLOCK_OFFSET_SECONDS.
"""

import os
from datetime import UTC, datetime

ESTIMATE_OFFSET_ENV = "INTERVIEW_CLOCK_OFFSET_SECONDS"
DEFAULT_ESTIMATE_OFFSET_SECONDS = 1.3


def estimate_offset_seconds() -> float:
    raw = os.environ.get(ESTIMATE_OFFSET_ENV, "").strip()
    try:
        return float(raw) if raw else DEFAULT_ESTIMATE_OFFSET_SECONDS
    except ValueError:
        return DEFAULT_ESTIMATE_OFFSET_SECONDS


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def interview_time_now(interview, now: datetime | None = None) -> dict:
    """{"interview_seconds", "estimate", "basis", "server_time"} for this interview.

    `interview_seconds` is None before the interview has started.
    """
    now = now or datetime.now(UTC)
    zero = _aware(getattr(interview, "transcript_zero_at", None))
    started = _aware(interview.started_at)
    if zero is not None:
        seconds, estimate, basis = (now - zero).total_seconds(), False, "transcript_zero"
    elif started is not None:
        seconds = (now - started).total_seconds() - estimate_offset_seconds()
        estimate, basis = True, "started_at_minus_offset"
    else:
        seconds, estimate, basis = None, True, "not_started"
    return {
        "interview_seconds": None if seconds is None else round(max(0.0, seconds), 3),
        "estimate": estimate,
        "basis": basis,
        "server_time": now.isoformat(),
    }
