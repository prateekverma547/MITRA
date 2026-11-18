"""Keeps a realtime interview on time and ends it cleanly.

Step 1 of docs/REALTIME_ARCHITECTURE.md. The realtime model runs the
conversation from one whole-interview prompt and replies without waiting for
us. It cannot feel time passing, so this coordinator watches the clock and
inserts a note at each section boundary. It also owns the two ways an
interview ends (the candidate leaving, and the natural goodbye) and hangs up
once the goodbye has actually been heard.

What it deliberately does not do yet: coverage judgement, contradictions,
refusal counting. That is step 3, where the brain and judge run as the
supervisor over an independent transcript.

It is an **observer**, for the reason `bot/ending.py` documents: it must see
`BotStoppedSpeakingFrame`, which comes from the output transport, and no
processor upstream of that ever does. Not a dataclass, for the reason
`bot/ending.py` also documents: observers live in a set and must stay hashable.
"""

import asyncio
import time
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.llm_service import FunctionCallResultProperties

from bot.brain.withdrawal import asked_explicitly
from bot.realtime_prompt import NOTE_PREFIX
from bot.tools import _END_INTERVIEW_SCHEMA, END_INTERVIEW

FINISH_INTERVIEW = "finish_interview"

_FINISH_INTERVIEW_SCHEMA = FunctionSchema(
    name=FINISH_INTERVIEW,
    description=(
        "Call this in the same reply as your final goodbye, once the interview "
        "is complete. It ends the call after your goodbye has been heard. Never "
        "call it before you have said goodbye, and never call it because the "
        "candidate asked to stop; use end_interview for that."
    ),
    properties={},
    required=[],
)

REALTIME_TOOLS = ToolsSchema(standard_tools=[_END_INTERVIEW_SCHEMA, _FINISH_INTERVIEW_SCHEMA])

#: If the goodbye was already over by the time we learned the interview had
#: ended, there will be no BotStoppedSpeaking to end on. Hang up after this.
_ENDED_WHILE_SILENT_SECONDS = 2.0

#: How long to wait for a goodbye that never comes before hanging up anyway.
_GOODBYE_TIMEOUT_SECONDS = 20.0

#: After the greeting stops playing, wait this long before listening, for the
#: tail of the audio to clear the room (the Burj kiosk's SPEAKER_TAIL_MS).
_GREETING_TAIL_SECONDS = 0.3

#: Never stay deaf longer than this, even if the greeting never finishes.
_GREETING_MAX_SECONDS = 20.0


class RealtimeCoordinator(BaseObserver):
    """Clock notes, ending tools and hanging up, for the realtime engine."""

    def __init__(self, *, blueprint, llm, session_id: str = ""):
        super().__init__()
        self._blueprint = blueprint
        self._llm = llm
        self._session_id = session_id
        self._worker = None

        self._started_at: float | None = None
        self._clock_task: asyncio.Task | None = None
        self._last_note_id: str | None = None
        self._events: list[dict] = []
        self._seen: set = set()

        self._last_candidate_text = ""
        self._offered_to_stop = False
        self._bot_speaking = False

        self._over = False
        self._reason = "interview_complete"
        #: Waiting for the reply that carries the goodbye to begin.
        self._awaiting_goodbye = False
        #: The goodbye is (or is about to be) playing; hang up when it stops.
        self._end_on_next_stop = False
        self._ended = False
        #: The candidate is not heard until the greeting has finished.
        self._greeting_done = False

    # -- wiring -----------------------------------------------------------

    def attach(self, worker) -> None:
        self._worker = worker

    def register_tools(self) -> None:
        self._llm.register_function(END_INTERVIEW, self._handle_end_interview)
        self._llm.register_function(FINISH_INTERVIEW, self._handle_finish_interview)

    def start(self) -> None:
        """The interview clock starts when the greeting does."""
        if self._started_at is not None:
            return
        self._started_at = time.monotonic()
        self._clock_task = asyncio.create_task(self._run_clock())
        # Deaf until the greeting is over. Found live: a candidate saying
        # "hello? hello?" while the greeting played interrupted it, and the
        # model started the greeting again from the top, three times. Nobody
        # needs to barge in on a five-second hello. The Burj kiosk does the
        # same by muting its microphone for the welcome.
        self._llm.set_audio_input_paused(True)
        asyncio.create_task(self._start_listening_after(_GREETING_MAX_SECONDS))
        duration = self._blueprint.evaluation_spec.duration_minutes
        if duration + self._blueprint.evaluation_spec.overrun_grace_minutes >= 60:
            logger.warning(
                f"[{self._session_id}] booked {duration} min plus grace reaches "
                f"OpenAI's 60-minute Realtime session cap; the session will be "
                f"cut off before the hard stop"
            )

    async def _start_listening_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self._greeting_done and seconds == _GREETING_MAX_SECONDS:
            return
        self._greeting_done = True
        self._llm.set_audio_input_paused(False)
        self._record("listening", after_seconds=seconds)

    async def stop(self) -> None:
        if self._clock_task:
            self._clock_task.cancel()

    # -- state the rest of the bot reads ---------------------------------

    @property
    def elapsed_seconds(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    @property
    def is_over(self) -> bool:
        return self._over

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def _record(self, kind: str, **fields) -> None:
        event = {"kind": kind, "at_seconds": round(self.elapsed_seconds, 2), **fields}
        self._events.append(event)
        logger.info(f"[{self._session_id}] coordinator {kind}: {fields}")

    # -- the clock --------------------------------------------------------

    def _schedule(self) -> list[tuple[float, str]]:
        """(seconds into the interview, note) for every boundary in the plan."""
        bp = self._blueprint
        plans = bp.competency_plans
        spec = bp.evaluation_spec
        marks: list[tuple[float, str]] = []

        cursor = bp.opening_minutes
        if plans:
            marks.append((cursor, f"Move on now to the first area: {plans[0].name}."))
        for index, plan in enumerate(plans):
            cursor += plan.time_budget_minutes
            following = (
                f"the next area: {plans[index + 1].name}"
                if index + 1 < len(plans)
                else "the closing"
            )
            marks.append(
                (
                    cursor,
                    f"Time for {plan.name} is up. Finish this thread in one more "
                    f"exchange at most, then move on to {following}.",
                )
            )
        marks.append(
            (
                float(spec.duration_minutes),
                "The booked time is over. Close the interview now: ask whether "
                "they have any questions, then thank them and say goodbye.",
            )
        )
        marks.append(
            (
                float(spec.hard_stop_minutes),
                "Out of time. Thank them and say goodbye now, then call "
                "finish_interview.",
            )
        )
        return [(minutes * 60, text) for minutes, text in marks]

    async def _run_clock(self) -> None:
        try:
            for at_seconds, text in self._schedule():
                delay = at_seconds - self.elapsed_seconds
                if delay > 0:
                    await asyncio.sleep(delay)
                if self._over:
                    return
                minutes = int(self.elapsed_seconds // 60)
                await self.note(f"About {minutes} minutes in. {text}")
        except asyncio.CancelledError:
            pass

    async def note(self, text: str) -> None:
        """Insert a note, replacing the previous clock note.

        Notes stay in the conversation until deleted, and an old "move on to X"
        sitting beside a new "move on to Y" is a contradiction the model has to
        resolve. The newest one is the truth, so it is the only one kept.
        """
        previous = self._last_note_id
        self._last_note_id = await self._llm.insert_note(f"{NOTE_PREFIX} {text}")
        if previous:
            await self._llm.delete_note(previous)
        self._record("note", text=text)

    # -- ending ----------------------------------------------------------

    def _mark_over(self, reason: str, *, goodbye_in_this_reply: bool) -> None:
        if self._over:
            return
        self._over = True
        self._reason = reason
        self._record("interview_over", reason=reason)
        if goodbye_in_this_reply:
            self._end_on_next_stop = True
            if not self._bot_speaking:
                asyncio.create_task(self._end_after(_ENDED_WHILE_SILENT_SECONDS))
        else:
            self._awaiting_goodbye = True
        asyncio.create_task(self._end_after(_GOODBYE_TIMEOUT_SECONDS))

    async def close_with(self, instructions: str, reason: str) -> None:
        """Speak a closing and hang up after it. Used by the silence ladder."""
        self._mark_over(reason, goodbye_in_this_reply=False)
        await self._llm.say(instructions)

    async def _end_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self._end_on_next_stop and self._bot_speaking:
            return  # BotStoppedSpeaking will end it
        await self._end_call()

    async def _end_call(self) -> None:
        if self._ended:
            return
        self._ended = True
        if self._clock_task:
            self._clock_task.cancel()
        logger.info(f"[{self._session_id}] interview over ({self._reason}); ending the call")
        if self._worker is None:
            logger.warning(f"[{self._session_id}] no worker attached; cannot end the call")
            return
        await self._worker.queue_frames([EndWorkerFrame(reason=self._reason)])

    async def _handle_end_interview(self, params) -> None:
        arguments: dict[str, Any] = params.arguments or {}
        said = str(arguments.get("said", "")).strip()
        explicit = bool(arguments.get("explicit", True))

        # Same corroboration as the pipeline engine (bot/tools.py): recall from
        # the model, precision from the patterns. A second request after we
        # have already offered counts as accepting, because being asked twice
        # is the insult.
        corroborated = asked_explicitly(said) or asked_explicitly(self._last_candidate_text)
        act_now = (explicit and corroborated) or self._offered_to_stop
        self._record(
            "end_interview_called",
            said=said,
            model_explicit=explicit,
            patterns_agree=corroborated,
            acting=act_now,
        )

        if act_now:
            self._mark_over("candidate_withdrew", goodbye_in_this_reply=False)
            await params.result_callback(
                {
                    "ended": True,
                    "next": "The interview is over. Thank them for their time in "
                    "one or two short sentences and say goodbye. Do not ask "
                    "anything and do not invite questions.",
                }
            )
            return

        self._offered_to_stop = True
        await params.result_callback(
            {
                "ended": False,
                "next": "Ask, in one short sentence, whether they would like to "
                "end here or carry on. Do not ask why.",
            }
        )

    async def _handle_finish_interview(self, params) -> None:
        self._record("finish_interview_called")
        self._mark_over("interview_complete", goodbye_in_this_reply=True)
        # No follow-up reply: the goodbye has been said, and another response
        # here would be the interviewer talking after its own goodbye.
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    # -- observing -------------------------------------------------------

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        # Only the handful of frame types we act on are remembered: every audio
        # frame of a forty-minute call passes through here too.
        if not isinstance(
            frame,
            (
                TranscriptionFrame,
                LLMFullResponseStartFrame,
                BotStartedSpeakingFrame,
                BotStoppedSpeakingFrame,
            ),
        ):
            return
        # The same frame is pushed by more than one processor.
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)

        if isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                self._last_candidate_text = frame.text.strip()
        elif isinstance(frame, LLMFullResponseStartFrame):
            if self._awaiting_goodbye:
                self._awaiting_goodbye = False
                self._end_on_next_stop = True
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if not self._greeting_done and self._started_at is not None:
                self._greeting_done = True
                asyncio.create_task(self._start_listening_after(_GREETING_TAIL_SECONDS))
            if self._end_on_next_stop:
                await self._end_call()
