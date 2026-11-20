"""Booking a technical round: the duration rule and the two links (task 20).

Called by the booking route in app/interviews.py, which keeps everything else
it did before (ready blueprint, one live session per candidate, admin auth).

**Duration (decision of 2026-11-19).** Per booking, defaulting to the plan's
(the spec snapshot inside the candidate's blueprint), 20 to 60 minutes on both
engines (`MAX_INTERVIEW_MINUTES`). The contract still allows 90, so old specs
and plans load; a booking over 60 is refused here.

**Trimming, realtime engine only.** OpenAI cuts a realtime session at 60
minutes. The hard stop (planned minutes plus grace, on the coordinator's clock)
must land by `HARD_STOP_BY_MINUTES` (58), a 2-minute margin. First the grace
shrinks, down to `MIN_GRACE_MINUTES` (2); if that is not enough, the planned
minutes are trimmed. The booked minutes stay what HR chose: 60 booked shows as
60, and runs as 56 planned plus 2 grace.

What the 2-minute margin has to cover (measured, C-48):
- before the coordinator's clock starts, the session is already open: the
  fresh OpenAI connection is opened when the candidate connects, alongside a
  1.0 s settle wait (bot/run_realtime.py start_interview). Task-19 live run:
  at most 5.8 s from the candidate connecting to the greeting's first audio;
- after the hard stop, the goodbye: a note asks Mitra for a short goodbye, and
  the call ends once it has played, at most 20 s after the ending was marked
  (realtime_coordinator `_GOODBYE_TIMEOUT_SECONDS`). Mitra ends an interview
  herself with end_interview (reason "completed" or "withdrew"); code checks its
  guards and hangs up the same way (round 14, A-95).
About 26 s of 120 s. **But nothing in the bot ends the call at the hard stop
by itself** if the model ignores the note: then the margin is not a guarantee
(C-48, joint A + C).

**Links.** `TwoLinks` (shared/interview_contracts.py) carries the meeting ID,
which the candidate types and which is not a secret, and never the password.
The password is returned once, in the booking response's own field.
- candidate_join_url: `<base>/interview/#/join`. The candidate app is served at
  /interview/ and routes by hash (frontend/candidate-app/src/route.ts); #/join
  is its first screen. No meeting ID in the URL: the candidate types both, and
  a link that carries half the credential invites forwarding it.
- live_view_url: `<base>/console/#/live/<interview_id>`. The console is served
  at /console with hash routing (Track H's console, createHashRouter), and the
  live view is Track I's `features/live` screen, coming in round 3. The link is
  behind the console sign-in; it holds no token.
"""

import os
from dataclasses import dataclass

from shared.contracts import MAX_INTERVIEW_MINUTES, MIN_DURATION_MINUTES
from shared.interview_contracts import TwoLinks

SESSION_LIMIT_MINUTES = 60
#: Where the hard stop must land on the realtime engine: 2 minutes before the cut.
HARD_STOP_BY_MINUTES = 58
MIN_GRACE_MINUTES = 2
#: Measured (task-19 live run, C-48): an upper bound on how long the OpenAI
#: session is open before the coordinator's clock starts.
PRE_INTERVIEW_SECONDS_MEASURED = 5.8
#: The longest the call stays up after an ending is marked (Mitra's accepted
#: end_interview, or the hard stop's goodbye note), bot/realtime_coordinator.py.
GOODBYE_TAIL_SECONDS = 20.0


class BookingError(ValueError):
    """A booking that must be refused. The message is shown to the admin."""


@dataclass(frozen=True)
class BookedTiming:
    booked_minutes: int      # what HR chose and sees; TwoLinks.duration_minutes
    planned_minutes: int     # what the bot must run to (the "booked time is over" mark)
    grace_minutes: int       # planned + grace = the hard stop
    note: str | None         # one plain sentence when trimmed, else None

    @property
    def trimmed(self) -> bool:
        return self.note is not None


def booked_timing(
    *, plan_minutes: int, grace_minutes: int, requested: int | None, engine: str
) -> BookedTiming:
    """The booking's timing, or BookingError with a reason a person can act on."""
    booked = plan_minutes if requested is None else requested
    if not MIN_DURATION_MINUTES <= booked <= MAX_INTERVIEW_MINUTES:
        raise BookingError(
            f"An interview can be {MIN_DURATION_MINUTES} to {MAX_INTERVIEW_MINUTES} "
            f"minutes long; {booked} was asked for."
        )
    planned, grace = booked, grace_minutes
    if engine != "realtime" or planned + grace <= HARD_STOP_BY_MINUTES:
        return BookedTiming(booked, planned, grace, None)

    # Realtime: first the grace, never below the minimum (and never raised).
    grace = min(grace, max(MIN_GRACE_MINUTES, HARD_STOP_BY_MINUTES - planned))
    if planned + grace > HARD_STOP_BY_MINUTES:
        planned = HARD_STOP_BY_MINUTES - grace
    if planned < booked:
        note = (f"Planned for {planned} minutes of questions so the interview finishes "
                f"within the {SESSION_LIMIT_MINUTES}-minute session limit.")
    else:
        note = (f"The overrun allowance is {grace} minutes instead of {grace_minutes}, so "
                f"the interview finishes within the {SESSION_LIMIT_MINUTES}-minute session limit.")
    return BookedTiming(booked, planned, grace, note)


def base_url(request_base: str) -> str:
    """APP_BASE_URL when set (the address candidates are given), else this request's."""
    return (os.environ.get("APP_BASE_URL") or request_base).rstrip("/")


def two_links(*, base: str, interview_id: str, meeting_id: str, duration_minutes: int,
              expires_at) -> TwoLinks:
    return TwoLinks(
        interview_id=interview_id,
        meeting_id=meeting_id,
        candidate_join_url=f"{base}/interview/#/join",
        live_view_url=f"{base}/console/#/live/{interview_id}",
        duration_minutes=duration_minutes,
        expires_at=expires_at,
    )
