"""Ending the interview when the employer's time is up, whoever is talking.

Before this, the brain's clock only moved when the candidate spoke. The brain
was ticked from the director, which runs on each candidate turn, and the hard
stop was checked inside `observe()`, which also runs on candidate turns. A quiet
candidate therefore never reached the deadline, and the interview ran on.

This module is the clock. Every second it:

- ticks the brain from the pipeline clock (the same source the director uses),
- starts the close at the scheduled end, even with nobody answering,
- ends the call at the hard stop (scheduled time plus grace) if it is still open.

It is an observer for one reason: it needs to know whether the candidate is
mid-answer and whether the interviewer is mid-sentence, so the close is never
spoken over either of them. Frames are injected through the worker, the same
route the silence ladder uses.
"""

import asyncio

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from shared.contracts import SectionKind

#: How often the clock is read and the brain ticked.
POLL_SECONDS = 1.0

#: The candidate must have been quiet this long before we ask for the close.
#: Smart-turn can hold a turn open through a pause, so a bare "not speaking" flag
#: is not enough to know the answer is finished.
QUIET_SECONDS_BEFORE_CLOSE = 4.0

#: Developer instruction for the model. It is spoken as the interviewer's own
#: turn, so it asks for a goodbye and forbids a question: a closing question
#: would invite an answer the interview no longer has time for.
CLOSE_INSTRUCTION = (
    "The scheduled time for this interview is over. Close the interview now: "
    "thank the candidate briefly for their time and say goodbye. Do not ask any "
    "further questions."
)

END_REASON = "time_limit_reached"

#: Spoken at the hard stop, before the call is ended. Fixed text, because the model
#: may be mid-question when the wall arrives and cannot be relied on to close.
HARD_STOP_GOODBYE = "Thank you for your time. Goodbye."


#: A plain class with BaseObserver, not a dataclass. Pipecat keeps observers in a
#: set, and a generated __eq__ would make this unhashable (see bot/ending.py).
class TimeLimit(BaseObserver):
    def __init__(self, *, brain, clock, session_id: str = "", poll_seconds: float = POLL_SECONDS):
        super().__init__()
        self._brain = brain
        self._clock = clock
        self._session_id = session_id
        self._poll = poll_seconds
        self._worker = None
        self._seen: set[int] = set()

        self._candidate_speaking = False
        self._bot_speaking = False
        self._llm_busy = False
        self._quiet_since: float | None = None

        self._close_requested = False
        self._ended = False

    def attach(self, worker) -> None:
        self._worker = worker

    @property
    def ended(self) -> bool:
        return self._ended

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._candidate_speaking = True
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._candidate_speaking = False
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._llm_busy = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._llm_busy = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

    async def run(self) -> None:
        """Tick until the call ends. Started as a task beside the pipeline."""
        while not self._ended:
            await asyncio.sleep(self._poll)
            try:
                await self.step()
            except Exception as exc:  # noqa: BLE001
                # The clock must never take the interview down, but a failure
                # here must not be silent either: it would be the hard stop
                # disappearing without a trace.
                logger.error(f"[{self._session_id}] time limit check failed: {exc!r}")

    async def step(self) -> None:
        """One clock reading. Public so the tests can drive it without sleeping."""
        if self._ended:
            return

        self._brain.tick(self._clock())

        # The interview has finished on its own (goodbye delivered, or withdrawn).
        # The ending observer hangs up; there is nothing for the clock to do.
        if self._brain.is_finished or self._brain.withdrew:
            return

        if self._brain.elapsed_seconds >= self._brain.hard_stop_seconds:
            # The call is cut here whatever the interviewer was doing, so it says
            # goodbye first. Without this the candidate was hung up on mid-question.
            await self._inject(TTSSpeakFrame(HARD_STOP_GOODBYE))
            await self._end("the hard stop was reached")
            return

        # A silent candidate must not hold a section past its budget. The budget is
        # otherwise only checked on candidate turns.
        self._brain.section_budget_spent()
        self._brain.begin_time_close()

        await self._request_close_when_quiet()

    async def _request_close_when_quiet(self) -> None:
        # Only once the close has started, and only once: the model should
        # deliver one goodbye, not be asked again each second.
        if self._close_requested or self._brain.closing_bot_turns > 0:
            return
        if self._brain.current_section.kind != SectionKind.CLOSING:
            return

        busy = self._candidate_speaking or self._bot_speaking or self._llm_busy
        if busy:
            self._quiet_since = None
            return

        now = self._clock()
        if self._quiet_since is None:
            self._quiet_since = now
            return
        if now - self._quiet_since < QUIET_SECONDS_BEFORE_CLOSE:
            return

        self._close_requested = True
        logger.info(f"[{self._session_id}] scheduled time is up; asking for the close")
        await self._inject(
            LLMMessagesAppendFrame(
                [{"role": "developer", "content": CLOSE_INSTRUCTION}],
                run_llm=True,
            )
        )

    async def _end(self, why: str) -> None:
        self._ended = True
        logger.warning(f"[{self._session_id}] ending the call: {why}")
        # EndWorkerFrame flushes anything already queued before it shuts down.
        await self._inject(EndWorkerFrame(reason=END_REASON))

    async def _inject(self, frame) -> None:
        if self._worker is None:
            logger.warning(
                f"[{self._session_id}] time limit not attached to a worker; "
                f"dropping {type(frame).__name__}"
            )
            return
        await self._worker.queue_frames([frame])
