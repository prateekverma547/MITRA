"""Proactive silence handling.

Live testing found the bot had no idle behaviour: it asked a question and, if
the candidate never spoke, waited forever. This module adds a three-stage
escalation ladder.

**The critical constraint** is that a nudge must never fire during a mid-answer
thinking pause. If it did, we would reintroduce the interruption problem that
`bot/turn_taking.py` exists to prevent — and we would do it at the worst
possible moment, while the candidate is composing a real answer.

That constraint is enforced structurally rather than by timing luck. Pipecat's
`UserIdleController` only arms its timer on `BotStoppedSpeakingFrame` **and
only when no user turn is in progress**, and cancels on
`UserStartedSpeakingFrame`. A mid-answer pause happens after the user turn has
started and before it has ended, so the timer is not running at all. The
smart-turn layer owns that silence; this module never sees it.

The ladder therefore measures *dead air*: silence after the bot has finished
speaking and before the candidate has begun. Thresholds are cumulative dead air
from the end of the bot's question, excluding the time the bot spends
delivering the nudges themselves.
"""

import time
from dataclasses import dataclass, field

from loguru import logger
from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    LLMMessagesAppendFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
    UserStartedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed


@dataclass(frozen=True)
class SilenceLadder:
    """When to escalate, and what to say. All timings configurable."""

    #: Cumulative seconds of dead air at which each stage fires.
    nudge_at_seconds: float = 20.0
    audio_check_at_seconds: float = 40.0
    close_at_seconds: float = 120.0

    #: Stage 1. Spoken verbatim — no LLM round trip, so it lands promptly and
    #: says exactly what we intend.
    nudge_text: str = "Take your time, there's no rush."

    #: Stage 2. Needs the LLM because rephrasing requires knowing the question.
    audio_check_instruction: str = (
        "The candidate has been silent for a while. Briefly rephrase your last "
        "question in simpler words. Then tell them that if they can hear you, "
        "you may not be receiving their audio, and suggest they check their "
        "microphone. Keep the whole thing to three sentences."
    )

    #: Stage 3. Spoken verbatim, then the session ends cleanly.
    closing_text: str = (
        "I haven't been able to hear anything for a couple of minutes, so I'll "
        "close the session here. If this was a technical problem, please rejoin "
        "and we can start again. Thanks for your time."
    )

    #: Said instead of closing when the room is empty. Nobody hears it, and that
    #: is the point: it goes in the log and the transcript so the record shows
    #: the interview ended because the call dropped, not because the candidate
    #: sat there saying nothing.
    left_the_call_text: str = (
        "The candidate is no longer in the call, so I'll end the session here."
    )

    #: Multipliers on the first rung only, by how much thinking the question
    #: deserves. A uniform twenty seconds treats "what are you working on?" and
    #: "tell me about a decision you regret" as the same question, and they are
    #: not: the second is someone searching their memory, and prompting into
    #: that is the interruption problem in slow motion.
    #:
    #: Only the first rung moves. The later rungs are about the channel and the
    #: session, not about thinking, and they stay where they are.
    easy_question_factor: float = 0.75
    deep_question_factor: float = 1.6

    def thresholds(self, patience: float = 1.0) -> list[float]:
        """Rung positions, with the first stretched or shortened by `patience`.

        When the first rung moves, the later ones move with it by the same
        spacing they were configured with, rather than being pushed to some
        arbitrary minimum. At `patience=1.0` this is exactly the configured
        ladder, which is what keeps a deliberately tight test configuration
        tight.
        """
        first = self.nudge_at_seconds * max(0.1, patience)
        to_check = self.audio_check_at_seconds - self.nudge_at_seconds
        to_close = self.close_at_seconds - self.audio_check_at_seconds
        second = max(self.audio_check_at_seconds, first + to_check)
        return [first, second, max(self.close_at_seconds, second + to_close)]

    def gaps(self, patience: float = 1.0) -> list[float]:
        """Wait before each stage, given the previous stage already elapsed."""
        out, previous = [], 0.0
        for threshold in self.thresholds(patience):
            out.append(max(0.0, threshold - previous))
            previous = threshold
        return out


@dataclass
class SilenceEvent:
    """One entry in the session's silence log."""

    at_seconds: float
    stage: int
    action: str
    dead_air_seconds: float
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "at_seconds": round(self.at_seconds, 2),
            "stage": self.stage,
            "action": self.action,
            "dead_air_seconds": round(self.dead_air_seconds, 1),
            "detail": self.detail,
        }


class SilenceEscalation(BaseObserver):
    """Drives the escalation ladder and records what it did.

    Two halves:

    - As an **observer** it watches for the candidate starting to speak, so a
      pending escalation is abandoned and the ladder resets to the bottom.
    - As an **idle handler** it is called by the aggregator's
      `on_user_turn_idle` event and decides which rung to fire.

    Frames are injected through the worker rather than pushed from here, so they
    enter at the top of the pipeline and reach every processor that needs them —
    including the idle controller itself, which lives on the aggregator and only
    sees frames arriving from upstream.
    """

    def __init__(
        self,
        *,
        ladder: SilenceLadder | None = None,
        session_id: str = "",
        presence=None,
        patience=None,
    ):
        super().__init__()
        self._ladder = ladder or SilenceLadder()
        self._session_id = session_id
        #: Optional. Without it the ladder behaves exactly as it did before,
        #: which keeps the text-mode tests honest and means a transport that
        #: cannot report presence degrades to guessing rather than to breaking.
        self._presence = presence
        #: Callable returning how much thinking the current question deserves.
        #: The brain knows; the ladder does not.
        self._patience = patience
        self._worker = None
        self._stage = 0
        self._started_at = time.time()
        self._dead_air_before_stage = 0.0
        self._events: list[SilenceEvent] = []
        self._seen: set[int] = set()

    def attach(self, worker) -> None:
        """Give the ladder a way to inject frames. Called once at wiring time."""
        self._worker = worker

    def _current_patience(self) -> float:
        if self._patience is None:
            return 1.0
        try:
            return float(self._patience())
        except Exception:  # noqa: BLE001
            # A bad patience reading must never stop the ladder running.
            return 1.0

    @property
    def initial_timeout(self) -> float:
        """Idle timeout to configure on the aggregator."""
        return self._ladder.gaps(self._current_patience())[0]

    @property
    def events(self) -> list[dict]:
        return [event.to_dict() for event in self._events]

    def _record(self, stage: int, action: str, detail: str = "") -> None:
        event = SilenceEvent(
            at_seconds=time.time() - self._started_at,
            stage=stage,
            action=action,
            dead_air_seconds=self._dead_air_before_stage,
            detail=detail,
        )
        self._events.append(event)
        logger.info(
            f"[{self._session_id}] silence stage {stage}: {action} "
            f"(after ~{self._dead_air_before_stage:.0f}s dead air)"
        )

    async def on_push_frame(self, data: FramePushed):
        """Reset the ladder the moment the candidate starts speaking."""
        frame = data.frame
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)

        if isinstance(frame, UserStartedSpeakingFrame) and self._stage > 0:
            recovered_from = self._stage
            self._stage = 0
            self._dead_air_before_stage = 0.0
            self._record(recovered_from, "candidate_resumed", "ladder reset")
            # Restore the first-rung timeout for the next question.
            await self._inject(UserIdleTimeoutUpdateFrame(timeout=self.initial_timeout))

    async def handle_idle(self, aggregator) -> None:
        """Called when the candidate has been silent past the current threshold."""
        patience = self._current_patience()
        thresholds = self._ladder.thresholds(patience)
        gaps = self._ladder.gaps(patience)

        self._dead_air_before_stage = thresholds[min(self._stage, len(thresholds) - 1)]
        stage = self._stage
        self._stage += 1

        # Certain knowledge beats a guess. If the room is empty there is nobody
        # to nudge, and closing while they reconnect would end an interview over
        # a network blip.
        if self._presence is not None and not self._presence.candidate_present:
            self._record(stage + 1, "waiting_for_rejoin", "the candidate is not in the call")
            if self._dead_air_before_stage >= self._ladder.close_at_seconds:
                self._record(3, "ended_call_dropped", self._ladder.left_the_call_text)
                await self._inject(UserIdleTimeoutUpdateFrame(timeout=0))
                await self._inject(EndWorkerFrame(reason="candidate_left_the_call"))
                return
            # Keep listening at the same cadence in case they come back.
            await self._inject(
                UserIdleTimeoutUpdateFrame(timeout=gaps[min(stage, len(gaps) - 1)])
            )
            return

        if stage == 0:
            self._record(1, "nudge", self._ladder.nudge_text)
            await self._inject(TTSSpeakFrame(self._ladder.nudge_text))
            await self._inject(UserIdleTimeoutUpdateFrame(timeout=gaps[1]))

        elif stage == 1:
            self._record(2, "rephrase_and_audio_check")
            await self._inject(
                LLMMessagesAppendFrame(
                    [{"role": "developer", "content": self._ladder.audio_check_instruction}],
                    run_llm=True,
                )
            )
            await self._inject(UserIdleTimeoutUpdateFrame(timeout=gaps[2]))

        else:
            self._record(3, "graceful_close", self._ladder.closing_text)
            # Stop the ladder before we begin closing, so a late timer cannot
            # fire again while the goodbye is still being spoken.
            await self._inject(UserIdleTimeoutUpdateFrame(timeout=0))
            await self._inject(TTSSpeakFrame(self._ladder.closing_text))
            # EndWorkerFrame flushes queued frames before shutting down, so the
            # goodbye is actually heard rather than cut off by the disconnect.
            await self._inject(EndWorkerFrame(reason="abandoned_by_candidate"))

    async def _inject(self, frame: Frame) -> None:
        if self._worker is None:
            logger.warning(
                f"[{self._session_id}] silence ladder not attached to a worker; "
                f"dropping {type(frame).__name__}"
            )
            return
        await self._worker.queue_frames([frame])
