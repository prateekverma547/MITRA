"""Noticing that the interviewer has gone deaf.

Session `int_3e4f8bb5b499` lost its speech-to-text mid-interview. Deepgram's socket
stalled ("keepalive ping timeout"), Pipecat retried every ten seconds for the rest of
the call, and not one retry succeeded. Nothing else in the bot treats that as a
problem: the error is non-fatal, so the pipeline carries on, and every other part of
the system reads "no transcript" as "the candidate said nothing". The silence ladder
told a candidate who was talking to take their time, the interviewer moved on to a
new question without having heard the answer, and the candidate eventually left.

That is the channel being read as evidence about the person, the exact confusion
`ConversationHealth` exists to prevent, so this decides it from evidence instead.
Voice activity detection runs locally on the candidate's audio and keeps working when
the STT vendor does not. So the question is concrete: **is the candidate audibly
speaking while no words are coming back?** One unheard utterance is a cough or a
murmur. Several, adding up to many seconds of speech, with not a single word
transcribed, is not something a working STT produces.

When it decides hearing is lost it says so, plainly and as our fault, and ends the
call. Carrying on is worse than stopping: every further question is asked of
someone whose answers are going nowhere, and the record fills up with what looks like
a candidate who never replied. The session is saved as a failure, which is never
scored.

An observer, not a processor, for the reason `bot/ending.py` records: it has to see
frames from both ends of the pipeline (errors from the STT service, VAD frames from
the aggregator), and only observers see every frame wherever they sit. A plain class
rather than a dataclass, because Pipecat keeps observers in a set.

**The thresholds are not measured.** They are set wide on purpose: wrongly ending a
working interview costs a candidate their slot, so the bar is "unmistakable", not
"early". Revisit them against real sessions (`hearing_events` in session metrics).
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    EndWorkerFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.stt_service import STTService

from bot.lip_activity import MouthState


@dataclass(frozen=True)
class HearingThresholds:
    """When unheard speech stops being noise and becomes a dead STT."""

    #: With no STT error reported: this many utterances in a row, adding up to at
    #: least this much speech, without one transcribed word. A healthy STT returns
    #: words for the first real sentence, so this only trips when nothing is getting
    #: through at all. Twelve seconds is several answers' worth of openings.
    unheard_utterances: int = 4
    unheard_speech_seconds: float = 12.0

    #: After the STT service has itself reported an error, the evidence is already
    #: half in, so less unheard speech is needed. Still more than one utterance, so a
    #: reconnect that succeeds a moment later is given its chance.
    after_error_utterances: int = 2
    after_error_speech_seconds: float = 4.0

    #: How long after the candidate stops speaking to wait for the words. Deepgram's
    #: finals land ~0.3s after the end of speech (docs/DECISIONS.md), OpenAI's ~1s;
    #: three seconds keeps a slow transcript from being mistaken for a missing one.
    transcript_grace_seconds: float = 3.0

    #: Spoken verbatim, so it does not depend on the LLM. Says whose fault it is,
    #: because a candidate who has been talking into nothing will otherwise assume
    #: it is theirs.
    message: str = (
        "I'm sorry, I'm having a technical problem on my side and I can't hear your "
        "answers. I'm going to end the interview here. This is not a reflection on "
        "you, and you'll be contacted to reschedule. Thank you for your patience."
    )


class HearingMonitor(BaseObserver):
    """Ends the call, honestly, when the candidate is speaking and nothing is heard."""

    def __init__(
        self,
        *,
        session_id: str = "",
        thresholds: HearingThresholds | None = None,
        on_lost: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__()
        self._session_id = session_id
        self._t = thresholds or HearingThresholds()
        #: Told the reason once hearing is lost, so the session is saved as a failure.
        self._on_lost = on_lost
        self._clock = clock
        self._started = clock()
        self._worker = None
        self._seen: set[int] = set()

        self._speech_started_at: float | None = None
        self._heard_this_utterance = False
        self._unheard_utterances = 0
        self._unheard_speech = 0.0
        self._stt_error_at: float | None = None
        self._stt_errors = 0
        self._lost = False
        self._pending: asyncio.Task | None = None
        self._events: list[dict] = []
        #: Camera evidence for the utterance in progress. Only ever removes an
        #: unheard count (see `note_mouth`); absent a camera, both stay False.
        self._mouth_spoke_in_utterance = False
        self._mouth_silent_in_utterance = False

    def attach(self, worker) -> None:
        """Give it a way to speak and end the session. Called once at wiring time."""
        self._worker = worker

    @property
    def lost(self) -> bool:
        return self._lost

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def summary(self) -> dict:
        """For session metrics: what the channel did, kept apart from the candidate."""
        return {
            "hearing_lost": self._lost,
            "stt_errors": self._stt_errors,
            "hearing_events": self.events,
        }

    async def settle(self) -> None:
        """Wait for a pending check. For tests and orderly shutdown."""
        if self._pending is not None and not self._pending.done():
            await self._pending

    def _record(self, event: str, detail: str = "") -> None:
        self._events.append(
            {
                "at_seconds": round(self._clock() - self._started, 2),
                "event": event,
                "unheard_utterances": self._unheard_utterances,
                "unheard_speech_seconds": round(self._unheard_speech, 2),
                "detail": detail,
            }
        )

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        # The same frame is pushed by more than one processor.
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)
        if self._lost:
            return

        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            if (frame.text or "").strip():
                self._heard()

        elif isinstance(frame, ErrorFrame):
            if isinstance(frame.processor, STTService) or isinstance(data.source, STTService):
                self._stt_errors += 1
                if self._stt_error_at is None:
                    self._stt_error_at = self._clock()
                    self._record("stt_error", str(frame.error)[:200])
                    logger.warning(
                        f"[{self._session_id}] speech-to-text reported an error; "
                        f"watching whether the candidate can still be heard"
                    )

        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._speech_started_at = self._clock()
            self._heard_this_utterance = False
            self._mouth_spoke_in_utterance = False
            self._mouth_silent_in_utterance = False

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._speech_started_at is None:
                return
            # Words that arrived while they were still talking (interim results, as
            # a working STT sends them) mean this utterance was heard.
            if self._heard_this_utterance:
                self._speech_started_at = None
                return
            # The VAD confirms silence only after `stop_secs` of it, so that tail is
            # not speech.
            spoke = self._clock() - self._speech_started_at - (frame.stop_secs or 0.0)
            self._speech_started_at = None
            if spoke <= 0:
                return
            # The camera saw a still mouth through the whole utterance and never a
            # moving one: the audio was noise, not an unheard answer. Only a judged
            # silence counts; an unjudged camera (no face, moving head) changes nothing.
            if self._mouth_silent_in_utterance and not self._mouth_spoke_in_utterance:
                self._record("not_counted_lips_still", f"{spoke:.2f}s of audio, lips still")
                return
            self._unheard_utterances += 1
            self._unheard_speech += spoke
            # Judged once the words have had time to arrive. A later utterance
            # replaces the pending check; the counts it reads are cumulative.
            if self._pending is not None and not self._pending.done():
                self._pending.cancel()
            self._pending = asyncio.create_task(self._check_later())

    def note_mouth(self, state: MouthState) -> None:
        """Latest reading from the candidate's camera (see bot/camera_hearing.py).

        Only ever removes an unheard count: a judged still mouth through an utterance
        means the audio was not an answer. A moving mouth, or no reading, changes nothing,
        so the camera can make the bot more patient but never quicker to give up.
        """
        if self._speech_started_at is None:
            return
        if state is MouthState.SPEAKING:
            self._mouth_spoke_in_utterance = True
        elif state is MouthState.SILENT:
            self._mouth_silent_in_utterance = True

    def _heard(self) -> None:
        if self._stt_error_at is not None:
            self._record("stt_recovered")
            logger.info(f"[{self._session_id}] speech-to-text recovered; the candidate is heard again")
        self._stt_error_at = None
        self._heard_this_utterance = True
        self._unheard_utterances = 0
        self._unheard_speech = 0.0
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()

    async def _check_later(self) -> None:
        try:
            await asyncio.sleep(self._t.transcript_grace_seconds)
        except asyncio.CancelledError:
            return
        if self._lost or self._unheard_utterances == 0:
            return

        t = self._t
        if self._stt_error_at is not None:
            if (
                self._unheard_utterances >= t.after_error_utterances
                and self._unheard_speech >= t.after_error_speech_seconds
            ):
                await self._lose("after a speech-to-text error")
        elif (
            self._unheard_utterances >= t.unheard_utterances
            and self._unheard_speech >= t.unheard_speech_seconds
        ):
            await self._lose("with no speech-to-text error reported")

    async def _lose(self, how: str) -> None:
        self._lost = True
        reason = (
            "Speech recognition stopped working: the candidate spoke "
            f"{self._unheard_utterances} times ({self._unheard_speech:.0f}s of speech) "
            f"{how}, and none of it was transcribed. The interview was ended early so "
            "they would not keep answering questions nobody could hear. The transcript "
            "is partial. This is a technical failure, not evidence about the candidate."
        )
        self._record("hearing_lost", reason)
        logger.error(f"[{self._session_id}] {reason}")
        if self._on_lost is not None:
            try:
                self._on_lost(reason)
            except Exception as exc:  # noqa: BLE001
                # Recording the reason must never stop the call from ending.
                logger.warning(f"[{self._session_id}] could not record the hearing loss: {exc!r}")

        if self._worker is None:
            logger.warning(
                f"[{self._session_id}] hearing monitor not attached to a worker; "
                f"the call will not end by itself"
            )
            return
        # Silence the ladder first, so it cannot nudge over the explanation.
        await self._inject(UserIdleTimeoutUpdateFrame(timeout=0))
        await self._inject(TTSSpeakFrame(self._t.message))
        # EndWorkerFrame flushes what is queued, so the explanation is heard.
        await self._inject(EndWorkerFrame(reason="speech_recognition_failed"))

    async def _inject(self, frame: Frame) -> None:
        await self._worker.queue_frames([frame])
