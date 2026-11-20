"""Rejoin after a dropped call (phase 3 wave 1). The bot alone decides the grace window,
the counting and the ending (C-126); Session C's server only detects and reports.

Decided (Karandeep):
  - On a drop the interview pauses: a REJOIN_GRACE_SECONDS (90 s) window for the SAME
    candidate (user_id candidate:<id>; the participant id may change, as on a reload).
  - Mitra stays silent while waiting (the reply is cancelled, input paused, the silence
    ladder holds and never closes during it).
  - On return, code says "Welcome back. Let me repeat the last question." and the last
    question, word for word.
  - The clock keeps running; the hard stop still applies (ended silently during a drop).
  - A drop of REJOIN_COUNTS_FROM_SECONDS (10 s) or more counts. At most REJOIN_LIMIT (3)
    counted rejoins; the return from a 4th counted drop ends the interview with a fixed,
    kind goodbye (rejoin_limit_reached).
  - Not back within the grace window: ended silently, candidate_did_not_return.

How a drop is noticed (D-27, D-30):
  - Session C's status-reading detector: a "dropped" signal 3 s after the browser's
    readings stop, read here with live.drops.signals_after about once a second (C-121).
    The server writes that drop's event itself; the bot does not repeat it.
  - Daily: the candidate's last connection leaving (participant-left), which for a
    silent cut comes 34-38 s late or never; the bot sends that drop's event.
How a return is noticed:
  - a candidate-mark join on Daily while dropped (a reload: a new participant id);
  - a "resumed" signal while Daily still shows the candidate (a silent cut: the same id).
A return is acted on only once it has held for SETTLE_SECONDS, so a connection that
flaps (D-27: a second interrupt after a long cut) is one drop, not two.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from loguru import logger

from shared.contracts import REJOIN_COUNTS_FROM_SECONDS, REJOIN_GRACE_SECONDS, REJOIN_LIMIT

#: A return must hold this long before it is acted on (flapping connections).
SETTLE_SECONDS = 1.5
#: How often the bot reads Session C's connection signals.
POLL_SECONDS = 1.0


@dataclass
class _Drop:
    started_seconds: float
    started_mono: float
    detected_by: str
    participant_id: str | None


@dataclass
class RejoinController:
    coordinator: object
    presence: object
    live: object
    interview_id: str | None
    #: () -> interview seconds now (the transcript clock, as the events use).
    clock: object
    #: async (interview_id, last_seq) -> [Signal]; live.drops.signals_after in the bot.
    signals: object = None
    grace_seconds: float = REJOIN_GRACE_SECONDS
    counts_from_seconds: float = REJOIN_COUNTS_FROM_SECONDS
    limit: int = REJOIN_LIMIT
    settle_seconds: float = SETTLE_SECONDS
    poll_seconds: float = POLL_SECONDS
    now: object = time.monotonic
    sleep: object = asyncio.sleep

    drops: list = field(default_factory=list)
    rejoin_count: int = 0
    limit_reached: bool = False
    events: list = field(default_factory=list)
    _drop: _Drop | None = None
    _last_seq: int = 0
    _grace_task: asyncio.Task | None = None
    _settle_task: asyncio.Task | None = None
    _poll_task: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        if self.signals is not None and self.interview_id and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll())

    async def close(self) -> None:
        for task in (self._poll_task, self._grace_task, self._settle_task):
            if task and not task.done():
                task.cancel()
        if self._drop is not None:
            # Still away when the session ended: a drop with no return.
            self._finish_drop(returned=False)

    @property
    def dropped(self) -> bool:
        return self._drop is not None

    # -- inputs ----------------------------------------------------------------------

    async def on_candidate_left(self, participant_id: str | None = None, *, error: bool = False) -> None:
        """Daily: the candidate's last connection left (or a fatal error)."""
        await self._start_drop("daily_error" if error else "daily_left", participant_id, send_event=True)

    async def on_candidate_joined(self, participant_id: str | None) -> None:
        """Daily: a candidate-mark connection joined after the first (reconnect or reload)."""
        if self._drop is not None:
            same = bool(participant_id) and participant_id == self._drop.participant_id
            self._arm_return(same_participant=same, back_at=self.clock())

    async def on_signal(self, signal) -> None:
        """Session C's status-reading signals (C-121): "dropped" | "resumed"."""
        self._log("signal", signal_kind=signal.kind, signal_at_seconds=signal.at_seconds,
                  gap_seconds=signal.gap_seconds)
        if signal.kind == "dropped":
            await self._start_drop("status_readings", None, send_event=False, started_seconds=signal.at_seconds)
        elif signal.kind == "resumed" and self._drop is not None:
            # The page is sending again. If Daily still has them, they are back on the
            # same connection (a silent cut); otherwise wait for Daily's join.
            if bool(getattr(self.presence, "_present", None)):
                # The gap ends when the first reading came back (the server's own time,
                # B-104), not when the bot read the signal or after the settle.
                self._arm_return(same_participant=True, back_at=float(signal.at_seconds))

    # -- the decisions -----------------------------------------------------------------

    async def _start_drop(self, detected_by: str, participant_id: str | None, *, send_event: bool,
                          started_seconds: float | None = None) -> None:
        if self.coordinator.is_over:
            return
        if self._settle_task and not self._settle_task.done():
            # A return was settling and the connection went again: the same drop.
            self._settle_task.cancel()
            self._log("return_cancelled_by_new_drop", detected_by=detected_by)
            return
        if self._drop is not None:
            return  # already away (for example: readings first, Daily's leave later)
        start = self.clock() if started_seconds is None else min(float(started_seconds), self.clock())
        self._drop = _Drop(started_seconds=max(0.0, start), started_mono=self.now() - max(0.0, self.clock() - start),
                           detected_by=detected_by, participant_id=participant_id or self._candidate_id())
        self.presence.rejoin_pending = True
        await self.coordinator.candidate_away(True)
        if send_event:
            self.live.candidate_dropped(detected_by, participant_id)
        self._log("dropped", detected_by=detected_by, at_seconds=round(self._drop.started_seconds, 2))
        logger.info(f"[rejoin] candidate dropped ({detected_by}); grace window {self.grace_seconds:.0f} s")
        self._grace_task = asyncio.create_task(self._grace())

    def _arm_return(self, *, same_participant: bool, back_at: float | None = None) -> None:
        if self._settle_task and not self._settle_task.done():
            return
        self._settle_task = asyncio.create_task(self._settle_then_return(same_participant, back_at))

    async def _settle_then_return(self, same_participant: bool, back_at: float | None = None) -> None:
        try:
            await self.sleep(self.settle_seconds)
        except asyncio.CancelledError:
            return
        if self._drop is None or self.coordinator.is_over:
            return
        await self._return(same_participant, back_at)

    async def _return(self, same_participant: bool, back_at: float | None = None) -> None:
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
        record = self._finish_drop(returned=True, same_participant=same_participant, back_at=back_at)
        counted = record["counted"]
        if counted:
            self.rejoin_count += 1
        number = self.rejoin_count if counted else None
        self.live.candidate_returned(record["gap_seconds"], same_participant, counted, number)
        self.presence.rejoin_pending = False
        self._log("returned", gap_seconds=record["gap_seconds"], counted=counted, rejoin_number=number)
        if counted and self.rejoin_count > self.limit:
            self.limit_reached = True
            self.live.rejoin_limit_reached(self.rejoin_count, self.limit)
            self._log("limit_reached", rejoins=self.rejoin_count)
            await self.coordinator.candidate_away(False)
            await self.coordinator.end_rejoin_limit()
            return
        await self.coordinator.candidate_away(False)
        await self.coordinator.welcome_back()

    async def _grace(self) -> None:
        try:
            deadline = self.now() + self.grace_seconds - (self.now() - self._drop.started_mono)
            while self.now() < deadline:
                if self.coordinator.is_over or self._drop is None:
                    return  # ended meanwhile (the End button, the hard stop) or back
                await self.sleep(min(0.5, max(0.01, deadline - self.now())))
        except asyncio.CancelledError:
            return
        if self._drop is None or self.coordinator.is_over:
            return
        self._finish_drop(returned=False)
        self._log("did_not_return")
        logger.info("[rejoin] not back within the grace window; ending")
        await self.coordinator.end_silently("candidate_did_not_return", "candidate_did_not_return")

    def _finish_drop(self, *, returned: bool, same_participant: bool | None = None,
                     back_at: float | None = None) -> dict:
        d = self._drop
        self._drop = None
        if returned:
            # The moment they came back (the resumed signal's own time, or Daily's join);
            # the bot's clock only when neither is known.
            ended = max(d.started_seconds, self.clock() if back_at is None else float(back_at))
            gap = round(ended - d.started_seconds, 2)
            record = {"started_seconds": round(d.started_seconds, 2), "ended_seconds": round(d.started_seconds + gap, 2),
                      "gap_seconds": gap, "detected_by": d.detected_by, "same_participant": same_participant,
                      "counted": gap >= self.counts_from_seconds}
        else:
            record = {"started_seconds": round(d.started_seconds, 2), "ended_seconds": None, "gap_seconds": None,
                      "detected_by": d.detected_by, "same_participant": None, "counted": False}
        self.drops.append(record)
        return record

    # -- signals ---------------------------------------------------------------------

    async def _poll(self) -> None:
        while True:
            try:
                for signal in await self.signals(self.interview_id, self._last_seq):
                    self._last_seq = max(self._last_seq, signal.seq)
                    await self.on_signal(signal)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001  (a database hiccup never ends the call)
                self._log("signals_error", error=repr(exc)[:200])
            try:
                await self.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                return

    # -- the record ----------------------------------------------------------------------

    def _candidate_id(self) -> str | None:
        present = getattr(self.presence, "_present", None) or set()
        return next(iter(present), None)

    def _log(self, kind: str, **fields) -> None:
        self.events.append({"kind": kind, "at_seconds": round(self.clock(), 2), **fields})

    def record(self) -> dict:
        """The ConnectionRecord (shared/contracts.py) for session_metrics and the report."""
        from shared.contracts import ConnectionRecord

        return ConnectionRecord(drops=self.drops, rejoin_count=sum(1 for d in self.drops if d["counted"]),
                                rejoin_limit=self.limit, grace_seconds=self.grace_seconds,
                                limit_reached=self.limit_reached).model_dump(mode="json")
