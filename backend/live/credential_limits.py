"""Rate limits for the candidate's credential routes: the check and the join.

Both routes take a meeting ID and a password, so both share these limits and one
budget: an attacker cannot double their guesses by alternating routes.

The password is 8 characters from a 25-letter alphabet (app/meeting.py): about
1.5 × 10^11 combinations, so guessing it is not a practical attack at any rate
these allow. The limits exist to stop floods and enumeration, and above all must
not let anyone **lock a real candidate out by failing on purpose**. So:

- **Per address, every attempt** (`ADDRESS_PER_MINUTE`): a flood from one
  address is refused before any database work. Generous, because several
  candidates may share one address (an office, a carrier's NAT).
- **Per address and meeting ID, failures only** (`PAIR_FAILURES` in
  `PAIR_WINDOW`): one address guessing at one interview is stopped. Someone
  else's failures never count against you.
- **Per meeting ID, failures only, across addresses** (`MEETING_FAILURES` in
  `MEETING_WINDOW`): a distributed guess at one interview is noticed, and then
  only addresses that **themselves** failed on that meeting ID are refused. An
  address with no failure on it (the real candidate typing the right password)
  is never refused by this rule, however many failures others caused.

The one case these cannot separate: an attacker and the candidate behind the
same address (C-77). In memory, one web replica (CLAUDE.md); a restart clears
them.
"""

import time
from collections import defaultdict, deque

ADDRESS_PER_MINUTE = 60
PAIR_FAILURES = 5
PAIR_WINDOW = 15 * 60.0
MEETING_FAILURES = 30
MEETING_WINDOW = 60 * 60.0
_MAX_KEYS = 50_000


class CredentialLimits:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._pair_failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._meeting_failures: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _trim(q: deque, now: float, window: float) -> deque:
        while q and q[0] <= now - window:
            q.popleft()
        return q

    def refuse(self, address: str, meeting_id: str, now: float | None = None) -> str | None:
        """Why this attempt is refused before the credentials are read, or None."""
        refused = self.refusal(address, meeting_id, now)
        return refused[0] if refused is not None else None

    def refusal(self, address: str, meeting_id: str, now: float | None = None) -> tuple[str, int] | None:
        """(why, seconds until this address may try this meeting ID again), or None.

        The wait comes only from this address's own attempts and from failures
        counted against the meeting ID string, which are counted the same way
        whether or not an interview has that ID: it says nothing about whether
        the interview exists (PA-15, C-165). Rounded up to whole seconds, at least 1."""
        now = time.monotonic() if now is None else now
        attempts = self._trim(self._attempts[address], now, 60.0)
        if len(attempts) >= ADDRESS_PER_MINUTE:
            # Free again when enough of the minute's attempts have aged out.
            return "address", _seconds(attempts[len(attempts) - ADDRESS_PER_MINUTE] + 60.0 - now)
        attempts.append(now)
        pair = self._trim(self._pair_failures[(address, meeting_id)], now, PAIR_WINDOW)
        if len(pair) >= PAIR_FAILURES:
            return "pair", _seconds(pair[len(pair) - PAIR_FAILURES] + PAIR_WINDOW - now)
        meeting = self._trim(self._meeting_failures[meeting_id], now, MEETING_WINDOW)
        if len(meeting) >= MEETING_FAILURES and pair:
            # Free when the meeting's count falls below the line, or this address's
            # own failures on it have all aged out, whichever comes first.
            below = meeting[len(meeting) - MEETING_FAILURES] + MEETING_WINDOW
            own_gone = pair[-1] + PAIR_WINDOW
            return "meeting", _seconds(min(below, own_gone) - now)
        return None

    def failed(self, address: str, meeting_id: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._pair_failures[(address, meeting_id)].append(now)
        self._meeting_failures[meeting_id].append(now)
        if len(self._pair_failures) > _MAX_KEYS or len(self._attempts) > _MAX_KEYS:
            self.reset()  # bounded memory; a reset only forgets, never refuses

    def reset(self) -> None:
        self._attempts.clear()
        self._pair_failures.clear()
        self._meeting_failures.clear()


def _seconds(wait: float) -> int:
    return max(1, int(-(-wait // 1)))  # rounded up


limits = CredentialLimits()
