"""The bot's side of the live event stream (joint A + C step).

Every event the bot is the source of goes through here: built with Session C's
`live.emit.make_event` against the contract (shared/interview_contracts.py),
queued with `EventEmitter.emit`, which never blocks (live/emit.py).

**Nothing here may slow, block or break an interview.** `make_event` validates
and can raise; a payload that fails is counted (`invalid`) and dropped. If
`live/` cannot be imported, or the emitter cannot start, the class is disabled
and counts what it would have sent (`disabled_skipped`). A full queue or a store
that is down is the emitter's business: it drops and counts, it never waits.

Interview time (`at_seconds`) is seconds since the transcript's zero
(`TranscriptObserver.clock_zero`), the clock the transcript, the server's clock
route and the browser share.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, TranscriptionFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed

#: How long after BotStoppedSpeaking a finished reply is read from the timeline.
FLUSH_DELAY_SECONDS = 0.2

#: Coordinator records that become events (see RealtimeCoordinator._record).
#: Each maps to a list of (payload class name, fields).
_COORDINATOR_EVENTS = {
    # Round 14: Mitra ends the interview with end_interview(reason); code only checks
    # its two guards and hangs up. move_on (C-A17, added by Session C as C-156): the area
    # itself also arrives as area_changed. A backstop move is code's, never a tool call,
    # so area_backstop and backstop_move_ignored are not sent as ToolCalled.
    "move_on": [("ToolCalled", {"tool": "move_on", "outcome": "moved"})],
    "move_on_refused": [("ToolCalled", {"tool": "move_on", "outcome": "refused"})],
    "area_move_ignored": [("ToolCalled", {"tool": "move_on", "outcome": "ignored"})],
    "end_interview_withdrew": [("Withdrawal", {"stage": "confirmed", "by": "model"}),
                               ("ToolCalled", {"tool": "end_interview", "outcome": "accepted_withdrew"})],
    "end_interview_completed": [("ToolCalled", {"tool": "end_interview", "outcome": "accepted_completed"})],
    "end_interview_refused": [("ToolCalled", {"tool": "end_interview", "outcome": "refused"})],
    "end_interview_after_end": [("ToolCalled", {"tool": "end_interview", "outcome": "after_end"})],
    "end_button": [("Withdrawal", {"stage": "confirmed", "by": "code"})],
}


class LiveEvents:
    """One per interview. Disabled (counting only) when there is no interview id."""

    def __init__(self, interview_id: str | None, *, clock_zero: float, blueprint=None, emitter=None,
                 enabled: bool = True):
        self.interview_id = interview_id
        self.clock_zero = clock_zero
        self._blueprint = blueprint
        self._emitter = emitter
        self.enabled = bool(enabled and interview_id)
        self.invalid = 0
        #: () -> the realtime area number now (C-A1); set by the coordinator.
        self.area_now = None
        self.disabled_skipped = 0
        self.turns_sent = 0
        #: The next turn's index. Assigned whether or not the event is sent, so the
        #: auditor numbers turns the same way when live events are disabled.
        self._next_turn = 0
        #: OpenAI item id -> the turn_index it was sent with (for reply waits).
        self.turn_index_of_item: dict[str, int] = {}
        self._warned = False
        #: (wall time, area number) of every area change, so a candidate's answer is filed
        #: under the area in effect when they started speaking (A-116).
        self._area_changes: list[tuple[float, int]] = []

    # -- life cycle -------------------------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled:
            return
        try:
            if self._emitter is None:
                from live.emit import EventEmitter

                self._emitter = EventEmitter(name=self.interview_id)
            await self._emitter.start()
        except Exception as exc:  # noqa: BLE001  (an interview never depends on this)
            logger.warning(f"[{self.interview_id}] live events disabled: {exc!r}")
            self.enabled = False

    async def close(self) -> dict:
        stats = {}
        if self.enabled and self._emitter is not None:
            try:
                stats = await self._emitter.close()
            except Exception as exc:  # noqa: BLE001
                stats = {"close_failed": repr(exc)}
        return {**(stats or {}), "invalid": self.invalid, "disabled_skipped": self.disabled_skipped,
                "turns_sent": self.turns_sent, "enabled": self.enabled}

    # -- sending ------------------------------------------------------------------------------

    def at_seconds(self, wall: float | None = None) -> float:
        return max(0.0, (wall if wall is not None else time.time()) - self.clock_zero)

    def send(self, payload_name: str, fields: dict, *, source: str, at_seconds: float | None = None) -> bool:
        """Build and queue one event. Never raises; False when not sent."""
        if not self.enabled:
            self.disabled_skipped += 1
            return False
        try:
            import shared.interview_contracts as contract
            from live.emit import make_event

            payload = getattr(contract, payload_name)(**fields)
            event = make_event(self.interview_id, payload, source=source,
                               at_seconds=self.at_seconds() if at_seconds is None else at_seconds)
            return bool(self._emitter.emit(event))
        except Exception as exc:  # noqa: BLE001
            self.invalid += 1
            if not self._warned:
                self._warned = True
                logger.warning(f"[{self.interview_id}] live event not sent ({payload_name}): {exc!r}")
            return False

    # -- areas --------------------------------------------------------------------------------

    def area(self, area_number: int) -> tuple[str, str, float | None]:
        """(area_id, area_name, budget) for a realtime area number (1 = opening, last = closing)."""
        bp = self._blueprint
        plans = bp.competency_plans if bp is not None else []
        if area_number <= 1:
            return "opening", "Opening", getattr(bp, "opening_minutes", None)
        if area_number >= len(plans) + 2:
            return "closing", "Closing", getattr(bp, "closing_minutes", None)
        plan = plans[area_number - 2]
        return plan.competency_id, plan.name, plan.time_budget_minutes

    # -- what the coordinator and the bot report ----------------------------------------------------

    def area_at(self, wall: float, *, default: int) -> int:
        """The area in effect at wall time `wall`. Areas change one at a time (the
        coordinator's single path, _move_from), so the area before a change is the
        changed-to area minus one."""
        after = next((area for at, area in self._area_changes if at > wall), None)
        return default if after is None else max(1, after - 1)

    def interview_started(self, booked_minutes: int | None) -> None:
        self.send("InterviewStarted", {"engine": "realtime", "booked_minutes": booked_minutes}, source="bot")

    def area_changed(self, area_number: int) -> None:
        self._area_changes.append((time.time(), area_number))
        area_id, name, budget = self.area(area_number)
        self.send("AreaChanged", {"area_id": area_id, "area_name": name, "budget_minutes": budget},
                  source="coordinator")

    def time_note(self, text: str) -> None:
        self.send("TimeNoteSent", {"text": text}, source="coordinator")

    def skip(self, area_number: int, question_id: str | None = None) -> None:
        """C-64's shape: Skip(area_id, question_id=None), source coordinator."""
        area_id, _, _ = self.area(area_number)
        self.send("Skip", {"area_id": area_id, "question_id": question_id}, source="coordinator")

    def coordinator_record(self, kind: str) -> None:
        for payload_name, fields in _COORDINATOR_EVENTS.get(kind, []):
            self.send(payload_name, fields, source="coordinator")

    def turn(self, *, speaker: str, text: str, start_wall: float | None, end_wall: float | None,
             cut: bool = False, item_id: str | None = None) -> int:
        start = self.at_seconds(start_wall) if start_wall else None
        end = self.at_seconds(end_wall) if end_wall else None
        if start is not None and end is not None and end < start:
            end = start
        index = self._next_turn
        self._next_turn += 1
        if item_id:
            self.turn_index_of_item[item_id] = index
        # C-A1: the area the turn belongs to, the coordinator's current area when it
        # completes (area_now is set by RealtimeCoordinator.attach_events). A candidate's
        # answer belongs to the area of the question it answers: the area in effect when
        # they STARTED speaking. Its transcript can arrive after Mitra has moved on (A-116:
        # an area-2 "I don't know" was filed under area 3, 0.19 s after the move).
        area_number = self.area_now() if self.area_now is not None else None
        if speaker == "candidate" and start_wall and area_number is not None:
            area_number = self.area_at(start_wall, default=area_number)
        area_id = self.area(area_number)[0] if area_number is not None and self._blueprint else None
        if self.send("TranscriptTurnEvent", {"turn_index": index, "speaker": speaker, "text": text,
                                             "audio_start_seconds": start, "audio_end_seconds": end,
                                             "cut": cut, "area_id": area_id}, source="bot",
                     at_seconds=end if end is not None else None):
            self.turns_sent += 1
        return index

    # -- rejoin (phase 3 wave 1, C-114) --------------------------------------------

    def candidate_dropped(self, detected_by: str, participant_id: str | None) -> None:
        """Only for drops the bot detects (daily_left, daily_error); Session C's
        detector sends status_readings drops itself (C-121)."""
        self.send("CandidateDropped", {"detected_by": detected_by, "participant_id": participant_id or None},
                  source="bot")

    def candidate_returned(self, gap_seconds: float, same_participant: bool, counted: bool,
                           rejoin_number: int | None) -> None:
        self.send("CandidateReturned", {"gap_seconds": max(0.0, gap_seconds), "same_participant": same_participant,
                                        "counted": counted, "rejoin_number": rejoin_number}, source="bot")

    def rejoin_limit_reached(self, rejoins: int, limit: int) -> None:
        """Session C's exchange route reads this to refuse further rejoins (C-126)."""
        self.send("RejoinLimitReached", {"rejoins": rejoins, "limit": limit}, source="bot")

    def silence_prompt(self, stage: int, action: str, dead_air_seconds: float) -> None:
        self.send("SilencePrompt", {"stage": stage, "action": action,
                                    "dead_air_seconds": max(0.0, dead_air_seconds)}, source="bot")

    def participant(self, change: str, role: str, participant_id: str, reason: str | None = None) -> None:
        self.send("Participant", {"change": change, "role": role, "participant_id": participant_id,
                                  "reason": reason or None}, source="bot")

    def reply_waits(self, waits: list[dict]) -> None:
        """After the call: each wait keyed by the Mitra turn it preceded (the live turn index)."""
        def seconds(ms):
            return None if ms is None or ms < 0 else round(ms / 1000, 3)

        for wait in waits:  # TurnLatencyObserver turns (bot/observers.py: to_dict)
            index = self.turn_index_of_item.get(wait.get("reply_item_id"))
            if index is None:
                continue
            self.send("ReplyWait", {
                "turn_index": index,
                "end_of_speech_detect_seconds": seconds(wait.get("endpointing_ms")),
                "model_to_first_sound_seconds": seconds(wait.get("generation_ms")),
                "total_seconds": seconds(wait.get("ttfa_ms")),
                "interrupted": bool(wait.get("was_interrupted")),
            }, source="bot")

    def cost(self, usage: dict, model: str) -> None:
        """The call's realtime usage (RealtimeUsageObserver.summary), once at the end."""
        self.send("CostUpdate", {"component": "realtime", "model": model,
                                 "input_tokens": int(usage.get("input_tokens") or 0),
                                 "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                                 "output_tokens": int(usage.get("output_tokens") or 0),
                                 "usd": usage.get("estimated_usd")}, source="bot")

    def error(self, component: str, code: str, message: str, fatal: bool) -> None:
        self.send("ErrorEvent", {"component": component, "code": code, "message": message[:300],
                                 "fatal": fatal}, source="bot")

    def interview_ended(self, ended_because: str, duration_seconds: float,
                        ending: str | None = None, hang_up_by: str | None = None) -> None:
        """C-A2: how the ending ran and what hung up, beside the reason (C-A3: the End
        button's own reason, candidate_ended_button)."""
        from shared.interview_contracts import EndReason

        reason = ended_because if ended_because in {r.value for r in EndReason} else "session_ended"
        self.send("InterviewEnded", {"ended_because": reason, "duration_seconds": max(0.0, duration_seconds),
                                     "ending": str(ending)[:64] if ending else None,
                                     "hang_up_by": str(hang_up_by)[:64] if hang_up_by else None},
                  source="bot")


class TurnEventObserver(BaseObserver):
    """Sends each finished turn as it completes, from the same timeline the
    transcript is built from (bot/realtime_timeline.py):

    - a candidate turn when its transcription arrives (OpenAI's speech window);
    - a Mitra turn when the bot stops speaking (playback start and end; `cut`
      when the candidate spoke over it).

    The index is the running count of turns sent, in completion order. The
    saved transcript orders turns by spoken time; run_realtime compares the two
    and records any mismatch. Audio before Mitra first speaks is the
    pre-interview slot and is not sent. Not a dataclass: observers must stay
    hashable (bot/ending.py).
    """

    def __init__(self, *, events: LiveEvents, timeline, on_turn=None):
        """`on_turn(speaker, index, text, end_seconds)`: told of every turn sent
        (the auditor). Called synchronously; it must not wait (put_nowait)."""
        super().__init__()
        self._events = events
        self._on_turn = on_turn
        self._timeline = timeline
        self._interviewer_spoke = False
        self._sent_replies: set[str] = set()
        self._seen: set = set()

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if not isinstance(frame, (TranscriptionFrame, BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            return
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)
        try:
            if isinstance(frame, BotStartedSpeakingFrame):
                self._interviewer_spoke = True
            elif isinstance(frame, TranscriptionFrame) and frame.text.strip() and self._interviewer_spoke:
                item_id = getattr(getattr(frame, "result", None), "item_id", None)
                start, end = self._timeline.user_window_wall(item_id) if item_id else (None, None)
                index = self._events.turn(speaker="candidate", text=frame.text.strip(), start_wall=start,
                                          end_wall=end, item_id=item_id)
                await self._tell_async("candidate", index, frame.text.strip(), end)
            elif isinstance(frame, BotStoppedSpeakingFrame):
                # TranscriptObserver records the playback end on this same frame;
                # observers may run concurrently, so read the timeline a moment later.
                asyncio.create_task(self._flush_soon())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"turn event not sent: {exc!r}")

    async def _flush_soon(self) -> None:
        await asyncio.sleep(FLUSH_DELAY_SECONDS)
        try:
            for pending in self.flush_replies():
                await pending
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"turn event not sent: {exc!r}")

    def flush_replies(self) -> list:
        """Send every reply the timeline has finished playing and not sent yet.
        Returns any awaitables a listener handed back (none in the call)."""
        pending = []
        replies = [a for a in self._timeline.assistants.values()
                   if a.item_id not in self._sent_replies and a.audio_bytes and a.text
                   and a.playback_start_wall is not None]
        for reply in sorted(replies, key=lambda a: a.playback_start_wall):
            self._sent_replies.add(reply.item_id)
            end = reply.playback_end_wall
            if reply.cut and reply.cut_wall is not None:
                end = reply.cut_wall if end is None else min(end, reply.cut_wall)
            index = self._events.turn(speaker="interviewer", text=reply.heard(),
                                      start_wall=reply.playback_start_wall, end_wall=end,
                                      cut=bool(reply.cut), item_id=reply.item_id)
            result = self._tell("interviewer", index, reply.heard(), end)
            if asyncio.iscoroutine(result):
                pending.append(result)
        return pending

    async def _tell_async(self, speaker, index, text, end_wall) -> None:
        """As _tell, awaiting a listener that returns an awaitable (none does in the call)."""
        result = self._tell(speaker, index, text, end_wall)
        if asyncio.iscoroutine(result):
            await result

    def _tell(self, speaker, index, text, end_wall):
        if self._on_turn is None:
            return
        try:
            return self._on_turn(speaker, index, text, self._events.at_seconds(end_wall) if end_wall else None)
        except Exception as exc:  # noqa: BLE001  (the auditor never breaks the call)
            logger.warning(f"turn listener failed: {exc!r}")
            return None
