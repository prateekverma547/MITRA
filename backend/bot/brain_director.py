"""Pipecat adapter for the sectioned brain.

This is the seam. `bot/brain/` is pure and Pipecat-free; this file is the only
place that knows about both.

**The planner-not-generator contract is preserved here under live conditions.**
The director rewrites the *context* just before the LLM service sees it, then
lets the frame continue downstream. It never generates or intercepts the
response, so tokens still stream LLM -> TTS exactly as before. If this file ever
starts awaiting a completed response, time-to-first-audio roughly doubles and
the whole latency budget is blown.

Frame flow it participates in::

    user_aggregator --LLMContextFrame--> [BrainDirector] --> llm --> tts

On each context frame the director:

1. reads the candidate's new utterance and the bot's previous one,
2. calls `brain.observe(...)`, which may transition the section,
3. calls `brain.plan_turn()` and replaces the context messages with the
   section-scoped window,
4. retargets the LLM's system instruction at the new section,
5. spawns any off-path judgement work **without awaiting it**.
"""

import asyncio

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMUpdateSettingsFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService

from bot.brain.brain import InterviewBrain
from bot.brain.drivers import OpenAIJudge
from bot.brain.prompting import reflect_back
from bot.config import FILLER_ENABLED_ENV, filler_enabled

#: Turns between one spoken acknowledgement and the next.
#:
#: **Not every turn.** Something said before every single question stops being
#: an acknowledgement and becomes a tic, and a candidate hears it as the bot
#: stalling. Three means it lands on the turns where there is something worth
#: repeating and stays quiet the rest of the time.
FILLER_COOLDOWN_TURNS = 3


class BrainDirector(FrameProcessor):
    """Drives the brain from the live pipeline."""

    def __init__(
        self,
        *,
        brain: InterviewBrain,
        llm: OpenAILLMService,
        judge: OpenAIJudge | None = None,
        session_id: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._brain = brain
        self._llm = llm
        self._judge = judge
        self._session_id = session_id
        self._last_section_id = brain.current_section.id
        self._judgment_tasks: set[asyncio.Task] = set()
        self._events: list[dict] = []
        self._turn_count = 0
        #: Judgement outcomes, counted here because this is what spawns the work.
        #: All three are explicit on purpose, and they do not have to add up:
        #:
        #:     attempted - succeeded - failed = cancelled at teardown
        #:
        #: `cleanup()` cancels whatever is still in flight when the pipeline
        #: comes down, which is correct — but `asyncio.CancelledError` inherits
        #: from `BaseException`, so neither handler in `_run_judgment` nor the
        #: one inside `assess` catches it. A cancelled judgement therefore
        #: increments `attempted` and nothing else. Judgements spawned near the
        #: close of an interview are exactly the ones likely to end that way.
        #:
        #: Read the gap as cancelled, never as success. `attempted` equal to
        #: `failed` means no claims were extracted, nothing was carried between
        #: sections and no contradictions were noticed, and the report will read
        #: as a candidate who said nothing specific.
        self._judgments_attempted = 0
        self._judgments_succeeded = 0
        self._judgments_failed = 0
        #: How many messages we wrote into the context last turn. Anything past
        #: this index on the next frame is new.
        self._written_count = 0
        #: When something was last said out loud while the model composed.
        self._last_filler_turn = -FILLER_COOLDOWN_TURNS
        # Ending the call is not done here. This processor sits before
        # transport.output(), so BotStoppedSpeakingFrame never reaches it. See
        # bot/ending.py, which is an observer for exactly that reason.

    @property
    def events(self) -> list[dict]:
        """Section transitions and judgement results, for the session log.

        Long sessions are otherwise undiagnosable: without these you cannot tell
        whether a bad interview came from bad questions, a bad transition, or a
        judge that never answered.
        """
        return list(self._events)

    def judgment_summary(self) -> dict:
        """Judge attempts and failures, for the session record.

        Shaped like `RoomPresence.summary()` so `run_bot` can spread it into
        `session_metrics` alongside the latency numbers.
        """
        return {
            "judgments_attempted": self._judgments_attempted,
            "judgments_succeeded": self._judgments_succeeded,
            "judgments_failed": self._judgments_failed,
        }

    def _record(self, kind: str, **fields) -> None:
        event = {"kind": kind, "at_seconds": round(self._brain.elapsed_seconds, 2), **fields}
        self._events.append(event)
        logger.info(f"[{self._session_id}] brain {kind}: {fields}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not isinstance(frame, LLMContextFrame) or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        context = frame.context
        candidate_text, bot_text = self._extract_last_exchange(context)

        self._brain.tick(self._elapsed())
        if candidate_text or bot_text:
            self._brain.observe(candidate_text=candidate_text, bot_text=bot_text)

        self._note_transition()

        # The brain counts an attempt when it hears what the interviewer
        # actually said, on the next frame. Counting it here, at the moment the
        # term was handed over, told a candidate they had been asked twice when
        # they had been asked once.
        #
        # Contradictions work the same way now: the brain records which ones the
        # prompt showed and marks them probed when the interviewer next speaks.
        # Until that, only the text harness marked them, and live a
        # contradiction was re-rendered every turn (`int_8e3db77a60a9`: seven).
        offered_term = self._brain.unexplained_terms()
        offered_term = offered_term[0] if offered_term else None

        plan = self._brain.plan_turn()
        context.set_messages(plan.messages)

        # Spoken now, while the model is still composing. Nothing awaits it and
        # nothing awaits the model: a TTSSpeakFrame goes straight past the LLM
        # to the synthesiser, the same route the opening greeting takes, so it
        # is already being said by the time generation starts. Composition was
        # measured at 1082 to 1268ms median and 3746ms at worst, and that gap is
        # what this covers. The judge is not the problem; it never blocked.
        filler = self._filler_for(offered_term)
        system_instruction = plan.system_instruction
        if filler:
            # Told to the model as well, or it says the same thing again and the
            # candidate hears the acknowledgement twice.
            system_instruction += (
                f'\n\nYOU HAVE ALREADY SAID THIS OUT LOUD: "{filler}"\n'
                "It has been spoken. Do not acknowledge or repeat what they said. "
                "Ask your question directly, as your first words.\n"
            )
        self._written_count = len(plan.messages)

        self._turn_count += 1
        logger.debug(
            f"[{self._session_id}] turn {self._turn_count} | section={plan.section_id} "
            f"| ctx~{plan.token_estimate()} tokens | msgs={len(plan.messages)}"
        )

        # Retarget the system instruction at the current section. Sent ahead of
        # the context frame so the service has it before it runs inference.
        if filler:
            await self.push_frame(TTSSpeakFrame(filler), direction)

        await self.push_frame(
            LLMUpdateSettingsFrame(
                delta=OpenAILLMService.Settings(system_instruction=system_instruction),
                service=self._llm,
            ),
            direction,
        )
        await self.push_frame(frame, direction)

        # Fire and forget: judgement must never sit on the spoken-turn path.
        self._spawn_judgments()

    def _filler_for(self, term) -> str | None:
        """What to say while the answer is being composed, or nothing.

        Three conditions, all of them narrowing rather than widening:

        - There is a phrase of the candidate's own to repeat. The only source is
          the term the judge pulled out of what they just said, so this cannot
          reflect back something they did not say.
        - It has been a few turns since the last one. See FILLER_COOLDOWN_TURNS.
        - It is not the closing. Filling a pause before "thank you for your
          time" is worse than the pause.

        **It is never the whole turn, but it can be left as the whole turn if
        generation fails**, and that is worth stating rather than hiding: the
        candidate would hear an acknowledgement and then silence. The silence
        ladder is the backstop, and it nudges at fifteen to twenty seconds. It
        is a real gap in this and it is one sentence wide.
        """
        # **Off unless switched on.** See `FILLER_ENABLED_ENV`: it was built to
        # cover a delay that turned out not to exist, and on a real interview it
        # interrupted a candidate 0.2s into their sentence. Their answer arrived
        # as eight fragments and the report then scored them 2.0 out of 5 for
        # communication, quoting five of the pieces. That is the failure
        # `ConversationHealth` exists to prevent, arriving by a route it cannot
        # see: it looks for fragmentation caused by the channel, and this was
        # caused by us.
        if not filler_enabled():
            return None
        if term is None or self._brain.current_section.kind.value != "competency":
            return None
        if self._turn_count - self._last_filler_turn < FILLER_COOLDOWN_TURNS:
            return None
        text = reflect_back(term.term)
        if text:
            self._last_filler_turn = self._turn_count
        return text

    # -- helpers ----------------------------------------------------------

    def _elapsed(self) -> float:
        clock = self.get_clock()
        if clock is None:
            return self._brain.elapsed_seconds
        return clock.get_time() / 1_000_000_000  # pipeline clock is nanoseconds

    def clock_seconds(self) -> float:
        """The brain's clock, for the time limit. Same source as `tick`, so the two agree."""
        return self._elapsed()

    def _extract_last_exchange(self, context) -> tuple[str | None, str | None]:
        """Pull the candidate's newest utterance and the bot's previous one.

        We know exactly how many messages we wrote last turn, so anything beyond
        that count is new. Positional, not textual.

        An earlier version de-duplicated by comparing text against the recorded
        transcript. That silently dropped repeated utterances: a candidate
        answering "I don't want to." four times was counted as one turn, so the
        section never reached its ceiling and the interview would not move on.
        People repeat themselves — "yeah", "I don't know", "right" — and turn
        counts drive every transition the brain makes.
        """
        messages = context.get_messages()
        # Clamped: if the context ever comes back shorter than what we wrote
        # (an interruption discarding messages, say) we skip this turn's
        # bookkeeping rather than re-consuming the whole window and
        # double-counting it.
        fresh = messages[min(self._written_count, len(messages)) :]

        candidate_text = None
        bot_text = None
        for message in reversed(fresh):
            role = message.get("role") if isinstance(message, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "user" and candidate_text is None:
                candidate_text = content.strip()
            elif role == "assistant" and bot_text is None:
                bot_text = content.strip()
            if candidate_text and bot_text:
                break
        return candidate_text, bot_text

    def _note_transition(self) -> None:
        current = self._brain.current_section
        if current.id == self._last_section_id:
            return

        previous = next(
            (s for s in self._brain.sections if s.id == self._last_section_id), None
        )
        if previous is not None:
            self._record(
                "section_ended",
                section=previous.id,
                coverage=previous.coverage.value,
                turns=previous.turns_spent,
                seconds=round(previous.elapsed(self._brain.elapsed_seconds), 1),
                shortfall=previous.coverage_shortfall,
            )
        self._record("section_started", section=current.id, budget_s=round(current.budget_seconds))
        self._last_section_id = current.id

    def _spawn_judgments(self) -> None:
        if self._judge is None:
            return
        while (request := self._brain.pending_judgment_request()) is not None:
            task = asyncio.create_task(self._run_judgment(request))
            self._judgment_tasks.add(task)
            task.add_done_callback(self._judgment_tasks.discard)

    async def _run_judgment(self, request) -> None:
        self._judgments_attempted += 1
        try:
            judgment = await self._judge.assess(request)
        except Exception as exc:  # noqa: BLE001
            # A judge that raises out of `assess` rather than swallowing it.
            self._judgments_failed += 1
            logger.warning(f"[{self._session_id}] judgement failed for {request.section_id}: {exc}")
            self._record(
                "judgment_failed",
                section=request.section_id,
                request_kind=request.kind,
                error=type(exc).__name__,
            )
            return
        if judgment is None:
            # `OpenAIJudge.assess` handles its own errors and reports them by
            # returning None, so this is the branch a real failure arrives on.
            # It used to return silently, which is why a dead judge left no
            # trace anywhere: no event, and the warning above cannot fire
            # because nothing propagated.
            self._judgments_failed += 1
            self._record(
                "judgment_failed",
                section=request.section_id,
                request_kind=request.kind,
            )
            return

        # Late is fine — the brain falls back to heuristics until this lands.
        self._brain.apply_judgment(judgment)
        self._judgments_succeeded += 1
        self._record(
            "judgment",
            section=judgment.section_id,
            request_kind=request.kind,
            coverage=judgment.coverage.value if judgment.coverage else None,
            claims=len(judgment.claims),
            contradictions=len(judgment.contradictions),
        )

    async def cleanup(self):
        for task in list(self._judgment_tasks):
            task.cancel()
        await super().cleanup()
