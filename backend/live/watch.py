"""The hidden watcher join for the live view (task 22, backend; decision 7).

HR, and the BU head of the job's BU, may watch a technical round live. Notes
only: nobody speaks into the call. The watcher joins the room with a token that
makes them hidden and receive-only (bot/services/daily.py
`create_watcher_token`), and every token issued is recorded (`WatchRecord`).

**When watching may start: once the interview is in progress**, not before
the candidate joins. Reasons:
- before the candidate joins there is nothing to watch: the bot is spawned at
  the candidate's join, so the room is empty;
- the token's expiry is tied to the interview's hard stop, and the hard stop is
  only known from `started_at` (set when the candidate joins and the bot is
  spawned); a token issued earlier would either expire mid-interview (eject at
  expiry) or need a long, loose lifetime;
- a hidden participant idling in an empty room for hours is a Daily cost and a
  standing pass for nothing.
The live view shows "not started yet" on the 409 and asks again.

**Expiry:** `started_at` + the hard stop (planned minutes plus grace, as
booked; otherwise the plan's) + `WATCH_MARGIN_MINUTES`, never past the room's
own expiry. The margin covers the coordinator's clock starting after
`started_at` (the candidate connecting, then the settle wait) and the goodbye.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from shared.interview_contracts import WatchRefusalReason

WATCHER_PREFIX = "watcher:"
WATCH_MARGIN_MINUTES = 10
DEFAULT_HARD_STOP_MINUTES = 45  # the default plan: 40 minutes plus 5 of grace


class WatchRefused(ValueError):
    """Every refusal carries a reason code as well as its message (B-50): the
    console switches on the code, never on the wording."""

    def __init__(self, status: int, message: str, reason: WatchRefusalReason):
        super().__init__(message)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class WatchPass:
    user_id: str
    expires_at: datetime


def watcher_user_id(who: str) -> str:
    """'watcher:<who>', inside Daily's 36-character user_id limit."""
    user_id = f"{WATCHER_PREFIX}{who}"
    if len(user_id) > 36:
        raise ValueError(f"watcher user_id is {len(user_id)} characters; Daily allows 36")
    return user_id


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def hard_stop_minutes(interview, blueprint: dict | None) -> int:
    planned = getattr(interview, "planned_duration_minutes", None)
    grace = getattr(interview, "planned_grace_minutes", None)
    if planned is not None and grace is not None:
        return planned + grace
    spec = (blueprint or {}).get("evaluation_spec") or {}
    if spec.get("duration_minutes"):
        return int(spec["duration_minutes"]) + int(spec.get("overrun_grace_minutes", 5))
    return DEFAULT_HARD_STOP_MINUTES


def watch_pass(interview, blueprint: dict | None, *, who: str, now: datetime | None = None) -> WatchPass:
    """The watcher's user_id and token expiry, or WatchRefused with the reason."""
    now = now or datetime.now(UTC)
    if getattr(interview, "round", "technical") == "hr":
        raise WatchRefused(409, "HR rounds are not watched live: there is no AI interviewer to watch.",
                           WatchRefusalReason.HR_ROUND)
    status = str(interview.status)
    if status in ("completed", "expired"):
        raise WatchRefused(409, "This interview has finished; there is nothing live to watch.",
                           WatchRefusalReason.FINISHED)
    if status == "failed":
        raise WatchRefused(409, "This interview failed; there is nothing live to watch.",
                           WatchRefusalReason.FAILED)
    started = _aware(interview.started_at)
    if status != "in_progress" or started is None:
        raise WatchRefused(409, "Not started yet: watching begins once the candidate has joined.",
                           WatchRefusalReason.NOT_STARTED)
    expires = started + timedelta(minutes=hard_stop_minutes(interview, blueprint) + WATCH_MARGIN_MINUTES)
    room_expires = _aware(interview.room_expires_at)
    if room_expires is not None:
        expires = min(expires, room_expires)
    if expires <= now + timedelta(seconds=30):
        raise WatchRefused(409, "This interview is past its hard stop; there is nothing live to watch.",
                           WatchRefusalReason.PAST_HARD_STOP)
    return WatchPass(user_id=watcher_user_id(who), expires_at=expires)
