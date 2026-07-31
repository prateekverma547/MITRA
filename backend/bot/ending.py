"""Hanging up when the interview is over.

Written as an **observer**, deliberately, after the first attempt failed live.

That attempt put the check in `BrainDirector`, which is a processor sitting
between the user aggregator and the LLM. `BotStoppedSpeakingFrame` comes from
`transport.output()`, which is further down the pipeline, so the director never
saw it: the candidate said they wanted to stop, the bot said goodbye, and the
call stayed open with both of them sitting in it.

Observers see every frame regardless of where they sit, which is why the silence
ladder has worked from the start. This borrows its shape exactly, including
queueing the end through the worker rather than pushing it as a frame, because
a frame pushed from the wrong position travels the wrong way.

Ends on the bot having *stopped* speaking rather than on the brain finishing, so
the goodbye is actually heard. `EndWorkerFrame` flushes what is queued before
shutting down, so nothing in flight is cut off either.
"""

from dataclasses import dataclass, field

from loguru import logger
from pipecat.frames.frames import BotStoppedSpeakingFrame, EndWorkerFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed


#: `eq=False` keeps the identity hash. Pipecat stores observers in a set, and a
#: plain @dataclass generates __eq__, which makes the class unhashable: the
#: pipeline then dies with "unhashable type" while registering it, and the call
#: never ends. `SilenceEscalation` works because it is not a dataclass at all.
@dataclass(eq=False)
class SessionEnder(BaseObserver):
    """Ends the call once the interview is finished and the goodbye is spoken.

    Before this, nothing ended a call at all. `is_finished` existed on the brain
    and only the text-mode harness read it, so even a normally completed
    interview sat there until the candidate hung up, or until the silence ladder
    closed it two minutes later and recorded it as an abandonment.
    """

    brain: object = None
    session_id: str = ""

    _worker: object = field(default=None, init=False)
    _ended: bool = field(default=False, init=False)
    _seen: set = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        super().__init__()

    def attach(self, worker) -> None:
        """Give it a way to end the session. Called once at wiring time."""
        self._worker = worker

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def reason(self) -> str:
        if self.brain is None:
            return "interview_complete"
        return "candidate_withdrew" if self.brain.withdrew else "interview_complete"

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if self._ended or self.brain is None:
            return
        if not isinstance(frame, BotStoppedSpeakingFrame):
            return
        # The same frame is pushed by more than one processor.
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)

        if not self.brain.is_finished:
            return

        self._ended = True
        logger.info(f"[{self.session_id}] interview over ({self.reason}); ending the call")
        if self._worker is None:
            logger.warning(
                f"[{self.session_id}] no worker attached; the call will not end "
                f"by itself and the candidate will have to hang up"
            )
            return
        await self._worker.queue_frames([EndWorkerFrame(reason=self.reason)])
