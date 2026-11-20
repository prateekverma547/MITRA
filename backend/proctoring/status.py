"""Live status readings from the candidate's browser, for the live view's tiles.

`POST /api/proctoring/interviews/{id}/status` (proctoring/routes.py):
- the same pass as the intake: the interview-scoped token from the join
  response, and the interview must be running (`intake.window_end`);
- a token bucket per interview: `STATUS_PER_SECOND` sustained, `STATUS_BURST`
  at once (a reading every second, with room for a short network bunch-up);
- the body is exactly the `IntegrityStatus` payload of InterviewEvent, and no
  extra field (`extra="forbid"`);
- the reading is wrapped in an InterviewEvent (`integrity_status`, source
  browser) and published to the in-memory board (live/status.py), which the
  relay merges into the stream. **It is not stored anywhere**: not in
  `interview_events`, not in `integrity_events`. A reading is not evidence.

The interview time on a reading is the server's (seconds since `started_at`),
because the reading carries none; it is for display only.
"""

import time
from datetime import UTC, datetime

from pydantic import ConfigDict

from live.emit import make_event
from live.status import board
from shared.interview_contracts import EventSource, IntegrityStatus

STATUS_PER_SECOND = 1.0
STATUS_BURST = 3


class StatusBody(IntegrityStatus):
    """The IntegrityStatus payload, refusing anything it does not name."""

    model_config = ConfigDict(extra="forbid")


class TokenBucket:
    """Per key: `rate` tokens a second, at most `burst` saved up. In memory."""

    def __init__(self, rate: float = STATUS_PER_SECOND, burst: int = STATUS_BURST):
        self.rate, self.burst = rate, burst
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, at = self._state.get(key, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - at) * self.rate)
        if tokens < 1.0:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - 1.0, now)
        return True

    def reset(self) -> None:
        self._state.clear()


status_limiter = TokenBucket()


def publish(interview_id: str, reading: StatusBody, *, started_at: datetime) -> int:
    """Wrap the reading and put it on the board. Returns its version."""
    now = datetime.now(UTC)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    payload = IntegrityStatus.model_validate(reading.model_dump())
    version_hint = f"{time.monotonic_ns()}"
    event = make_event(
        interview_id, payload, source=EventSource.BROWSER,
        at_seconds=max(0.0, (now - started_at).total_seconds()),
        event_id=f"{interview_id}:status:{version_hint}", wall_time=now,
    )
    return board.publish(interview_id, event.model_dump(mode="json"))
