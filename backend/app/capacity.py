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
    """

    def __init__(self) -> None:
        self._bots: dict[str, asyncio.subprocess.Process] = {}

    def _reap(self) -> None:
        finished = [i for i, p in self._bots.items() if p.returncode is not None]
        for interview_id in finished:
            del self._bots[interview_id]

    def live_count(self) -> int:
        self._reap()
        return len(self._bots)

    def has_capacity(self) -> bool:
        return self.live_count() < max_concurrent()

    def claim(self) -> None:
        """Raise unless another bot fits. Call before doing paid setup work."""
        if not self.has_capacity():
            raise AtCapacity(
                f"{self.live_count()} interviews already running, "
                f"limit is {max_concurrent()}."
            )

    def register(self, interview_id: str, process: asyncio.subprocess.Process) -> None:
        self._reap()
        self._bots[interview_id] = process
        logger.info(
            f"[{interview_id}] bot registered "
            f"({len(self._bots)}/{max_concurrent()} interviews running)"
        )

    def release(self, interview_id: str) -> None:
        self._bots.pop(interview_id, None)


#: One registry per container, matching the one-replica deployment.
registry = BotRegistry()
