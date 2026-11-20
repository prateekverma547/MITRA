"""How many interviews may run at once on this container.

Every interview is a child process holding a WebRTC connection, a VAD model and
a turn-detection model. Measured at ~224MB resident while idle, more once audio
is flowing. CLAUDE.md pins replicas at 1 because a bot is a child of one
specific container, so this number is the whole capacity of the deployment.

Without a cap the failure is not "the new interview is slow". The container hits
its memory limit, the OOM killer takes a process, and Railway restarts the
container — which kills **every** in-flight interview, including the ones that
were running fine. One candidate clicking join can end three other people's
interviews. That asymmetry is the whole reason this file exists: refusing one
candidate costs one rescheduled interview, and accepting them costs all of them.

The arithmetic behind the default, against Railway's 8GB / 8 vCPU replica:

    8192MB replica limit
    -  400MB  backend (FastAPI, SQLAlchemy, asyncpg, the Python base image)
    / ~400MB per bot under load
    =  ~19 concurrent interviews before memory binds

Memory is not what binds first, though. Each bot runs Silero VAD continuously
and smart-turn inference at every turn end, on top of WebRTC encode/decode —
sustained CPU, not bursts. **Per-bot CPU has not been measured**, so the default
below is set well under the memory ceiling rather than at it: 6 bots is ~2.4GB
of 8GB and leaves better than a vCPU each.

Raise `MAX_CONCURRENT_INTERVIEWS` once the Railway Metrics tab shows what
concurrent interviews actually cost in CPU. Raising it past what the replica can
carry converts a clean refusal into an outage, which is the trade this file
exists to avoid — so raise it from a measurement, not from optimism.
"""

import asyncio
import os

from loguru import logger

DEFAULT_MAX_CONCURRENT = 6


class AtCapacity(RuntimeError):
    """Raised when another bot would not safely fit on this container."""


def max_concurrent() -> int:
    raw = os.environ.get("MAX_CONCURRENT_INTERVIEWS")
    if not raw:
        return DEFAULT_MAX_CONCURRENT
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            f"MAX_CONCURRENT_INTERVIEWS={raw!r} is not a number; "
            f"using {DEFAULT_MAX_CONCURRENT}."
        )
        return DEFAULT_MAX_CONCURRENT
    if value < 1:
        logger.warning(
            f"MAX_CONCURRENT_INTERVIEWS={value} would refuse every interview; "
            f"using {DEFAULT_MAX_CONCURRENT}."
        )
        return DEFAULT_MAX_CONCURRENT
    return value


class BotRegistry:
    """The live bot processes this container owns.

    Counted from the actual child processes rather than from interview rows.
    A bot that segfaults never gets to write `completed` to the database, so a
    status-based count drifts upward and eventually refuses every interview
    forever — the cap would become the outage it exists to prevent.

    **A seat is reserved, not checked for.** `reserve()` writes the placeholder
    in the same synchronous step that decides there is room, so two requests
    cannot both see the last free seat. The reservation is taken before any
    `await`; checking first and registering after the awaits let simultaneous
    joins each pass the check and start more bots than the cap allows.

    Asyncio runs one request at a time between awaits, so the synchronous section
    is atomic within this process. CLAUDE.md pins replicas at 1, which is what
    makes that enough.
    """

    def __init__(self) -> None:
        #: interview_id -> the live process, or None while its spawn is pending.
        self._bots: dict[str, asyncio.subprocess.Process | None] = {}

    def _reap(self) -> None:
        finished = [
            i for i, p in self._bots.items() if p is not None and p.returncode is not None
        ]
        for interview_id in finished:
            del self._bots[interview_id]

    def live_count(self) -> int:
        """Live bots plus spawns in progress: both hold a seat."""
        self._reap()
        return len(self._bots)

    def is_running(self, interview_id: str) -> bool:
        """True when a bot is live or being started for this interview."""
        self._reap()
        return interview_id in self._bots

    def reserve(self, interview_id: str) -> bool:
        """Take a seat for one interview. Synchronous: call before any await.

        Returns True when this caller now owns the seat and must either register a
        process or release it. Returns False when the interview already has one,
        which is a reconnect or a second request, not an error.

        Raises AtCapacity when no seat is free.
        """
        self._reap()
        if interview_id in self._bots:
            return False
        if len(self._bots) >= max_concurrent():
            raise AtCapacity(
                f"{len(self._bots)} interviews already running, "
                f"limit is {max_concurrent()}."
            )
        self._bots[interview_id] = None
        return True

    def register(self, interview_id: str, process: asyncio.subprocess.Process) -> None:
        self._reap()
        self._bots[interview_id] = process
        logger.info(
            f"[{interview_id}] bot registered "
            f"({len(self._bots)}/{max_concurrent()} interviews running)"
        )

    def clear(self) -> None:
        """Forget every seat. For tests, which share one module-level registry."""
        self._bots.clear()

    def release(self, interview_id: str) -> None:
        """Give back a seat that was reserved but never got a process.

        Only ever drops a pending placeholder. A live bot is never removed here,
        because removing it would free a seat that is still in use.
        """
        if interview_id in self._bots and self._bots[interview_id] is None:
            del self._bots[interview_id]


#: One registry per container, matching the one-replica deployment.
registry = BotRegistry()
