"""The sectioned interview brain.

A **context planner and state machine — never a text generator** (CLAUDE.md).

    plan_turn()  -> what the model should see right now
    observe()    -> update coverage, decide transitions

Generation stays in the streaming Pipecat pipeline. If the brain returned
finished text, the first token could not reach TTS until the whole response was
generated, roughly doubling time-to-first-audio.

The brain is **pure and synchronous**. It never awaits and never blocks a spoken
turn. Off-path LLM work — depth judgement and claim extraction — is *requested*
by the brain via `pending_judgment_request()` and delivered later by its driver
via `apply_judgment()`. If a verdict has not arrived, heuristics rule that turn.

This module must not import Pipecat.
"""

from dataclasses import dataclass, field

from loguru import logger

from shared.contracts import (
    Contradiction,
    CoverageLevel,
    InterviewBlueprint,
    KeyClaim,
    SectionKind,
    SectionOutcome,
)

from bot.brain.refusal import is_substantive, looks_like_refusal
from bot.brain.repair import RepairKind, classify as classify_repair
from bot.brain.withdrawal import (
    StopIntent,
    classify as classify_stop,
    confirms_stopping,
    declines_stopping,
)
from bot.brain.state import BrainConfig, Section, build_sections
from shared.branding import BOT_NAME

#: Two attempts at the same question, then move on. A third time is an
#: interrogation, and recording that the ground was never covered is more
#: honest than grinding at someone who cannot hear it.
MAX_REPAIR_ATTEMPTS = 2

#: The interviewer gets at most this many turns in the closing: one to thank
#: them and invite a last question, one to answer it and say goodbye. Any more
#: and it is keeping somebody on a call that is already over.
MAX_CLOSING_TURNS = 2


@dataclass
class Turn:
    """One exchange in the interview transcript."""

    index: int
    speaker: str  # "interviewer" | "candidate"
    text: str
    section_id: str
    at_seconds: float


@dataclass
class JudgmentRequest:
    """Off-path work the brain would like done. The driver fulfils it."""

    section_id: str
    kind: str  # "depth" | "section_end"
    target_depth: str | None
    transcript: list[Turn]
    carried_claims: list[KeyClaim]


@dataclass
class Judgment:
    """The driver's answer to a JudgmentRequest."""

    section_id: str
    coverage: CoverageLevel | None = None
    rationale: str | None = None
    claims: list[KeyClaim] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)


@dataclass
class TurnPlan:
    """What the model should see for the next spoken turn.

    This is the whole point of the sectioned design: `messages` holds only the
    current section's recent turns, not the entire interview. Cross-section
    memory arrives as carried claims in the system instruction, which is far
    cheaper than replaying history and keeps the system prompt from being buried
    under thousands of tokens of chat.
    """

    section_id: str
    section_name: str
    system_instruction: str
    messages: list[dict]
    is_final_section: bool

    def token_estimate(self) -> int:
        """Rough size, for telemetry. ~4 chars per token."""
        total = len(self.system_instruction)
        total += sum(len(m.get("content", "")) for m in self.messages)
        return total // 4


class InterviewBrain:
    """Runs one interview against one blueprint."""

    def __init__(
        self,
        blueprint: InterviewBlueprint,
        *,
        config: BrainConfig | None = None,
        now: float = 0.0,
        time_of_day: str | None = None,
    ):
        self.blueprint = blueprint
        self.config = config or BrainConfig()
        #: "morning" | "afternoon" | "evening". Injected rather than read from
        #: the clock so the brain stays pure and testable.
        self.time_of_day = time_of_day
        self._sections = build_sections(blueprint)
        self._index = 0
        self._transcript: list[Turn] = []
        self._carried_claims: list[KeyClaim] = []
        self._all_contradictions: list[Contradiction] = []
        self._pending_request: JudgmentRequest | None = None
        self._verdicts: dict[str, Judgment] = {}
        self._probed_contradictions: set[str] = set()
        self._consecutive_refusals = 0
        #: What the candidate needs repeated, and how many times we have
        #: already tried. Reset the moment a real answer arrives.
        self._pending_repair: RepairKind = RepairKind.NONE
        self._repair_attempts = 0
        self._question_to_repair: str = ""
        self._repairs_requested = 0
        #: Set when they have said they want to stop and we have offered.
        #: Nothing ends on a single detection: a false positive would end
        #: somebody's interview, and that cannot be undone.
        self._stop_offered = False
        self._withdrew = False
        #: How many times the interviewer has spoken in the closing.
        self._closing_bot_turns = 0
        #: An interview begins when the interviewer opens it. Anything picked
        #: up before that is ambient room noise, not an answer.
        self._interviewer_has_spoken = False
        #: True once the interviewer has asked something nobody has answered
        #: yet. A turn is an *exchange*, not an utterance: everything the
        #: candidate says between two interviewer turns is one answer.
        self._awaiting_answer = False
        #: Per-exchange bookkeeping, so a later fragment can correct what the
        #: first one looked like without counting twice.
        self._exchange_refused = False
        self._credited_substantive = False
        self._ignored_before_start: list[str] = []
        self._started_at = now
        self._now = now
        self._finished = False

        self._sections[0].started_at = now

    # -- clock ------------------------------------------------------------

    def tick(self, now: float) -> None:
        """Advance the brain's notion of time.

        Injected rather than read from a system clock so the deterministic suite
        can drive a 40-minute interview instantly.
        """
        self._now = now

    @property
    def elapsed_seconds(self) -> float:
        return self._now - self._started_at

    @property
    def hard_stop_seconds(self) -> float:
        return self.blueprint.evaluation_spec.hard_stop_minutes * 60

    # -- section access ---------------------------------------------------

    @property
    def current_section(self) -> Section:
        return self._sections[min(self._index, len(self._sections) - 1)]

    @property
    def sections(self) -> list[Section]:
        return list(self._sections)

    def _section_by_id(self, section_id: str) -> Section | None:
        return next((s for s in self._sections if s.id == section_id), None)

    @property
    def is_finished(self) -> bool:
        return self._finished

    @property
    def transcript(self) -> list[Turn]:
        return list(self._transcript)

    @property
    def carried_claims(self) -> list[KeyClaim]:
        return list(self._carried_claims)

    def outcomes(self) -> list[SectionOutcome]:
        return [s.to_outcome(self._now) for s in self._sections]

    # -- planning ---------------------------------------------------------

    def plan_turn(self) -> TurnPlan:
        """Assemble the context for the next spoken turn."""
        section = self.current_section
        return TurnPlan(
            section_id=section.id,
            section_name=section.name,
            system_instruction=self._render_system_instruction(section),
            messages=self._section_messages(section),
            is_final_section=section.kind == SectionKind.CLOSING,
        )

    def _section_messages(self, section: Section) -> list[dict]:
        """This section's turns, plus a short factual bridge from the last one.

        The bridge exists because of a real failure. With a strictly empty
        window at a section boundary, the model was told to "continue naturally"
        from a conversation it could not see — so it invented one, opening with
        "you mentioned earlier that you shaped a key product strategy" to a
        candidate who had said only "I don't want to."

        Carrying the last exchange verbatim removes the pressure to confabulate
        by supplying the truth instead. It is bounded at one exchange, so
        context still does not grow with the interview.
        """
        turns = [t for t in self._transcript if t.section_id == section.id]

        bridge: list[Turn] = []
        if not turns:
            previous = [t for t in self._transcript if t.section_id != section.id]
            bridge = previous[-2:]

        window = bridge + turns[-self.config.verbatim_turns :]
        return [
            {
                "role": "assistant" if t.speaker == "interviewer" else "user",
                "content": t.text,
            }
            for t in window
        ]

    @property
    def ignored_before_start(self) -> list[str]:
        """Audio heard before the interview opened. Surfaced for diagnosis."""
        return list(self._ignored_before_start)

    @property
    def has_substantive_answers(self) -> bool:
        """Has the candidate actually told us anything yet?"""
        return any(
            is_substantive(t.text) for t in self._transcript if t.speaker == "candidate"
        )

    def _render_system_instruction(self, section: Section) -> str:
        from bot.brain.prompting import render_section_prompt

        return render_section_prompt(
            blueprint=self.blueprint,
            section=section,
            carried_claims=self._carried_claims,
            unprobed_contradictions=self.unprobed_contradictions(),
            remaining_seconds=self.section_remaining_seconds(),
            is_last_competency=self._is_last_competency(section),
            has_substantive_answers=self.has_substantive_answers,
            consecutive_refusals=self._consecutive_refusals,
            time_of_day=self.time_of_day,
            pending_repair=self._pending_repair,
            question_to_repair=self._question_to_repair,
            offer_to_stop=self._stop_offered,
            withdrew=self._withdrew,
            already_greeted=self._interviewer_has_spoken,
        )

    def _is_last_competency(self, section: Section) -> bool:
        competencies = [s for s in self._sections if s.kind == SectionKind.COMPETENCY]
        return bool(competencies) and section is competencies[-1]

    def section_remaining_seconds(self) -> float:
        section = self.current_section
        return max(0.0, section.budget_seconds - section.elapsed(self._now))

    # -- observation ------------------------------------------------------

    def observe(
        self,
        *,
        candidate_text: str | None = None,
        bot_text: str | None = None,
    ) -> None:
        """Record what was said and decide whether to move on."""
        section = self.current_section

        if bot_text:
            self._append(section, "interviewer", bot_text)
            self._interviewer_has_spoken = True
            self._awaiting_answer = True

        # Audio captured before the interviewer has said a word is not an
        # answer. A live session picked up a bystander in the room saying
        # "Enjoyment"; it was counted as the candidate's first turn, which
        # advanced the opening past its greeting stage — so the bot never
        # introduced itself, and the candidate had to ask who it was talking to.
        if candidate_text and not self._interviewer_has_spoken:
            self._ignored_before_start.append(candidate_text)
            return

        if candidate_text:
            self._append(section, "candidate", candidate_text)

            # Leaving outranks everything else. Somebody who has said they want
            # to stop must not be asked another question, and must certainly not
            # be asked to repeat themselves.
            # Already leaving. Repeating themselves must not re-trigger the
            # withdrawal path, which returned early and so never reached the
            # closing check below: the goodbye was said over and over and the
            # interview never registered as finished.
            intent = StopIntent.NONE if self._withdrew else classify_stop(candidate_text)

            if self._stop_offered:
                if confirms_stopping(candidate_text):
                    self._withdrew = True
                    # Cleared, or the offer block keeps winning and the bot asks
                    # "would you like to stop?" again instead of saying goodbye.
                    self._stop_offered = False
                    self._skip_to_closing("candidate_withdrew")
                    return
                if declines_stopping(candidate_text):
                    self._stop_offered = False
                    # Carry on as though it had not come up.
                else:
                    # Neither yes nor no. Take the answer at face value and keep
                    # going rather than pressing them about leaving.
                    self._stop_offered = False
            elif intent is not StopIntent.NONE:
                # The instant path. Same handling as the model's tool call, so
                # the two cannot drift into different behaviour.
                self.candidate_asked_to_stop(
                    said=candidate_text, explicit=intent is StopIntent.EXPLICIT
                )
                return

            repair = classify_repair(candidate_text)
            if repair is not RepairKind.NONE and self._repair_attempts < MAX_REPAIR_ATTEMPTS:
                # A request to try again carries no answer. Counting it as a
                # turn would let the depth ramp advance on nothing and would
                # spend the section's budget on silence.
                self._pending_repair = repair
                self._repair_attempts += 1
                self._repairs_requested += 1
                self._question_to_repair = self._last_interviewer_text()
                return

            # Either a real answer, or we have already tried twice. Two goes at
            # the same question is the limit; a third is an interrogation, and
            # it is better to move on and record that it was never reached.
            self._pending_repair = RepairKind.NONE
            self._repair_attempts = 0
            self._question_to_repair = ""

            # A turn is an *exchange*, not an utterance. Speech reaches us in
            # fragments — "So that" / "I get to know what their businesses are."
            # / "I try to make a conversation with a client" is one answer, and
            # counting each piece as its own turn spends the section on a single
            # reply. Live, `int_0a7ca5d0aca5` ran eight fragments of one answer
            # straight through the opening and most of the section after it
            # while the interviewer said nothing at all, and a 40-minute
            # interview reached its closing in 291 seconds with every section
            # marked `insufficient`.
            #
            # So: only the first utterance after an interviewer turn spends a
            # turn. The rest are the same answer still arriving.
            continuation = not self._awaiting_answer
            self._awaiting_answer = False

            refused = looks_like_refusal(candidate_text)

            if not continuation:
                section.turns_spent += 1
                self._exchange_refused = refused
                self._credited_substantive = False

            if refused:
                if not continuation:
                    self._consecutive_refusals += 1
                    section.declined_turns += 1
            else:
                # A leading "No." followed by "that's not how it went, what we
                # did was…" is an answer, and the fragments arrive separately.
                # Reading it as a refusal is the error that matters here.
                if continuation and self._exchange_refused and is_substantive(candidate_text):
                    self._exchange_refused = False
                    self._consecutive_refusals = max(0, self._consecutive_refusals - 1)
                    section.declined_turns = max(0, section.declined_turns - 1)
                self._consecutive_refusals = 0
                if is_substantive(candidate_text) and not self._credited_substantive:
                    section.substantive_turns += 1
                    self._credited_substantive = True

        # The interview ends when the interviewer has finished closing it, not
        # when the candidate happens to speak again.
        #
        # This is what kept every call open. The closing section advanced on
        # candidate turns like any other, so after a goodbye nobody replied to,
        # `turns_spent` never reached its ceiling, `is_finished` stayed False,
        # and the call sat there with both of them in it. Reported live twice.
        # Read fresh: the candidate's turn above may have moved us here. This
        # is the text-mode path; live, `bot_finished_speaking` does it, because
        # nothing observes an utterance nobody answers.
        if self.current_section.kind == SectionKind.CLOSING and bot_text:
            self._closing_bot_turns += 1
            # A closing turn that asks something is inviting a last question, so
            # wait for it. One that does not is a goodbye, and there is nothing
            # left to wait for.
            invited_a_question = "?" in bot_text
            if (
                self._withdrew
                or self._closing_bot_turns >= MAX_CLOSING_TURNS
                or not invited_a_question
            ):
                self._advance("candidate_withdrew" if self._withdrew else "closing_delivered")
                return

        if candidate_text:
            self._maybe_request_judgment(section)
            self._maybe_advance(section)

    def _last_interviewer_text(self) -> str:
        """The question that needs repeating.

        Held explicitly because the model cannot be trusted to remember what it
        just asked. A live run showed it answering a repair by abandoning the
        question and asking a different one, which loses the answer and the
        coverage with it.
        """
        for turn in reversed(self._transcript):
            if turn.speaker == "interviewer":
                return turn.text
        return ""

    def opening_line(self) -> str:
        """The first thing the candidate hears, written here rather than generated.

        This sentence was already spelled out word for word in the opening
        prompt, and the model was reproducing it almost verbatim. Paying a full
        round trip for a line we had already written cost several seconds of
        somebody sitting in silence wondering whether the call was working.

        Speaking it directly has a second benefit worth more than the speed. The
        introduction can no longer be skipped, garbled, or pre-empted by a
        candidate who says hello first: whoever is about to be interviewed by a
        machine is told so in the first breath, every time.
        """
        first_name = (
            self.blueprint.candidate_name.split()[0]
            if self.blueprint.candidate_name
            else None
        )
        greeting = f"Good {self.time_of_day}" if self.time_of_day else "Hello"
        who = f"{greeting}, {first_name}." if first_name else f"{greeting}."
        return (
            f"{who} I'm {BOT_NAME}, an AI interviewer. "
            f"I'll be speaking with you today. How are you doing?"
        )

    def bot_finished_speaking(self, said: str = "") -> None:
        """The interviewer has stopped talking. Called from the live pipeline.

        The brain otherwise only learns what the interviewer said when the
        *candidate* speaks next, because that is what produces a new context
        frame. The closing goodbye is the one utterance nobody replies to, so it
        was never observed: the interview stayed unfinished and the call hung
        until the silence backstop closed it fifteen seconds later. Reported
        live, with the goodbye visible in the transcript at 20.9 seconds and the
        session ending at 36.

        Idempotent, and a no-op outside the closing, so it is safe to call after
        every utterance.
        """
        if self._finished:
            return
        section = self.current_section
        if section.kind != SectionKind.CLOSING:
            return

        self._closing_bot_turns += 1
        # A closing turn that asks something is inviting a last question and
        # waits for it. One that does not is a goodbye.
        invited_a_question = "?" in (said or "")
        if (
            self._withdrew
            or self._closing_bot_turns >= MAX_CLOSING_TURNS
            or not invited_a_question
        ):
            self._advance("candidate_withdrew" if self._withdrew else "closing_delivered")

    def last_candidate_text(self) -> str:
        """What the candidate most recently said, verbatim."""
        for turn in reversed(self._transcript):
            if turn.speaker == "candidate":
                return turn.text
        return ""

    def candidate_asked_to_stop(self, *, said: str = "", explicit: bool = True) -> None:
        """The candidate wants to leave. Called by both paths.

        One entry point on purpose. The patterns in `withdrawal.py` catch the
        obvious wording instantly and for free; the live model calls this
        through its `end_interview` tool for everything a phrase list was never
        going to contain. Sharing this method is what stops the two drifting
        into different behaviour.

        `explicit` decides whether they are taken at their word or asked once.
        Somebody who said "just end this interview" and then gets asked whether
        they are sure has been ignored, which is what happened live.
        """
        if self._withdrew:
            return
        if explicit:
            self._withdrew = True
            self._stop_offered = False
            self._skip_to_closing("candidate_withdrew")
        else:
            self._stop_offered = True
        if said:
            logger.debug(f"candidate asked to stop (explicit={explicit}): {said!r}")

    @property
    def withdrew(self) -> bool:
        """True when the candidate asked to end the interview and confirmed it.

        Recorded separately from every other ending. "Chose to stop" says
        something about a decision; it says nothing about their ability, and a
        report must not blur the two. Same reason `declined_turns` is kept apart
        from low coverage.
        """
        return self._withdrew

    @property
    def patience(self) -> float:
        """How much thinking the question just asked deserves, as a multiplier.

        A uniform silence threshold treats "what are you working on?" and "tell
        me about a decision you regret" as the same question. The second is
        somebody searching their memory, and prompting into that is the
        interruption problem arriving slowly. The brain is the only thing that
        knows which kind of question it just planned.

        Only the first rung of the ladder moves. The later rungs are about the
        channel and the session, not about thinking.
        """
        from bot.silence import SilenceLadder

        section = self.current_section
        if section.kind in (SectionKind.OPENING, SectionKind.CLOSING):
            # Pleasantries and wrap-up. Silence here means something is wrong,
            # not that they are thinking hard about their own name.
            return SilenceLadder.easy_question_factor

        if section.kind == SectionKind.COMPETENCY and section.turns_spent >= 1:
            # Past the opening question of a section the interviewer is probing
            # for specifics: a named decision, a number, what they would change.
            # That is recall, and recall is slow.
            return SilenceLadder.deep_question_factor

        return 1.0

    @property
    def repairs_requested(self) -> int:
        """How many times the candidate asked for something again.

        Reported so a poor connection is visible in the feedback report rather
        than showing up as a candidate who could not answer.
        """
        return self._repairs_requested

    def _append(self, section: Section, speaker: str, text: str) -> None:
        self._transcript.append(
            Turn(
                index=len(self._transcript),
                speaker=speaker,
                text=text,
                section_id=section.id,
                at_seconds=round(self.elapsed_seconds, 2),
            )
        )

    # -- off-path judgement ----------------------------------------------

    def pending_judgment_request(self) -> JudgmentRequest | None:
        """Work the driver should do off the critical path. Consumes the request."""
        request, self._pending_request = self._pending_request, None
        return request

    def apply_judgment(self, judgment: Judgment) -> None:
        """Deliver a verdict. Safe to call late — or never."""
        self._verdicts[judgment.section_id] = judgment

        section = self._section_by_id(judgment.section_id)
        if section is None:
            return

        if judgment.coverage is not None:
            section.coverage = judgment.coverage
        if judgment.rationale:
            section.depth_rationale = judgment.rationale

        for claim in judgment.claims:
            if claim.text not in {c.text for c in section.key_claims}:
                section.key_claims.append(claim)

        for contradiction in judgment.contradictions:
            if contradiction.later_statement not in {
                c.later_statement for c in self._all_contradictions
            }:
                section.contradictions.append(contradiction)
                self._all_contradictions.append(contradiction)

    def _maybe_request_judgment(self, section: Section) -> None:
        """Ask for a depth read once the section is inside the decision band.

        Below the floor there is nothing to judge; at the ceiling the heuristic
        decides anyway. Only the band in between is worth an LLM call.
        """
        if section.kind != SectionKind.COMPETENCY:
            return
        floor, ceiling = self._turn_bounds(section)
        if not (floor <= section.turns_spent < ceiling):
            return
        if section.id in self._verdicts:
            return
        if self._pending_request is not None:
            return

        self._pending_request = JudgmentRequest(
            section_id=section.id,
            kind="depth",
            target_depth=section.target_depth,
            transcript=[t for t in self._transcript if t.section_id == section.id],
            carried_claims=list(self._carried_claims),
        )

    def _turn_bounds(self, section: Section) -> tuple[int, int]:
        if section.kind == SectionKind.OPENING:
            return self.config.opening_floor_turns, self.config.opening_ceiling_turns
        if section.kind == SectionKind.CLOSING:
            return 1, self.config.closing_ceiling_turns
        return self.config.floor_turns, self.config.ceiling_turns

    # -- contradictions ---------------------------------------------------

    def unprobed_contradictions(self) -> list[Contradiction]:
        """Contradictions the interviewer has not yet raised.

        Probed at most once each: raising the same inconsistency twice reads as
        prosecutorial, and this bot gathers evidence rather than delivering
        verdicts.
        """
        return [
            c
            for c in self._all_contradictions
            if c.later_statement not in self._probed_contradictions
        ]

    def mark_contradiction_probed(self, contradiction: Contradiction) -> None:
        self._probed_contradictions.add(contradiction.later_statement)
        contradiction.probed = True

    @property
    def contradictions(self) -> list[Contradiction]:
        return list(self._all_contradictions)

    # -- transitions ------------------------------------------------------

    def _maybe_advance(self, section: Section) -> None:
        reason = self._advance_reason(section)
        if reason:
            self._advance(reason)

    def _advance_reason(self, section: Section) -> str | None:
        floor, ceiling = self._turn_bounds(section)

        # The whole interview is out of time. Closing still gets to happen.
        if self.elapsed_seconds >= self.hard_stop_seconds:
            return "hard_stop"

        # The candidate is declining everything. Stop interviewing them.
        if self._consecutive_refusals >= self.config.refusals_before_closing:
            return "candidate_disengaged"

        # Declined this topic twice. Change the subject rather than ask again.
        if (
            section.kind == SectionKind.COMPETENCY
            and self._consecutive_refusals >= self.config.refusals_before_topic_change
        ):
            return "declined_topic"

        if section.turns_spent >= ceiling:
            return "ceiling"

        if section.elapsed(self._now) >= section.budget_seconds:
            return "budget_exhausted"

        if section.turns_spent < floor:
            return None

        # Inside the band: a verdict rules if one has arrived, otherwise
        # heuristics keep the section open. Never wait on the judge.
        verdict = self._verdicts.get(section.id)
        if verdict and verdict.coverage in (CoverageLevel.SUFFICIENT,):
            return "depth_reached"
        return None

    def _advance(self, reason: str) -> None:
        section = self.current_section
        section.ended_at = self._now
        self._finalise(section, reason)

        if section.kind == SectionKind.CLOSING:
            self._finished = True
            return

        self._carry_claims_from(section)

        # Out of time: go straight to the close rather than stepping through the
        # remaining sections one turn at a time, which would keep interviewing
        # well past the wall. Everything skipped is recorded as a shortfall so
        # the report can say which competencies never got a hearing.
        if reason in ("hard_stop", "candidate_disengaged"):
            self._skip_to_closing(reason)
            return

        # Ask for the end-of-section extraction now that the section is closed.
        if section.kind == SectionKind.COMPETENCY:
            self._pending_request = JudgmentRequest(
                section_id=section.id,
                kind="section_end",
                target_depth=section.target_depth,
                transcript=[t for t in self._transcript if t.section_id == section.id],
                carried_claims=list(self._carried_claims),
            )

        self._index += 1
        self._rebalance_remaining()
        self.current_section.started_at = self._now

    def _skip_to_closing(self, reason: str = "hard_stop") -> None:
        """Abandon remaining competencies and go to the close, recording the gap."""
        closing_index = next(
            (i for i, s in enumerate(self._sections) if s.kind == SectionKind.CLOSING),
            len(self._sections) - 1,
        )
        for skipped in self._sections[self._index + 1 : closing_index]:
            skipped.coverage = CoverageLevel.NOT_STARTED
            skipped.coverage_shortfall = True
            # Three different endings, three different sentences. A candidate
            # who chose to leave must never be written up as one who ran out of
            # time, and neither of those is a candidate who went quiet.
            skipped.shortfall_reason = {
                "candidate_withdrew": (
                    "Candidate chose to end the interview before this competency "
                    "was covered; no signal gathered. This reflects their "
                    "decision to stop, not their ability."
                ),
                "candidate_disengaged": (
                    "Candidate declined to continue before this competency was "
                    "covered; no signal gathered."
                ),
            }.get(
                reason,
                "Interview reached its time limit before this competency was "
                "covered; no signal gathered.",
            )
        self._index = closing_index
        self.current_section.started_at = self._now

    def _finalise(self, section: Section, reason: str) -> None:
        """Assign a coverage level if the judge never got there first."""
        if section.kind != SectionKind.COMPETENCY:
            # Not scored, but must not lie. A live session recorded the opening
            # as "sufficient" for a candidate who answered "No. I don't want to."
            section.coverage = (
                CoverageLevel.SUFFICIENT
                if section.substantive_turns
                else CoverageLevel.INSUFFICIENT
            )
            if section.declined_turns and not section.substantive_turns:
                section.coverage_shortfall = True
                section.shortfall_reason = "Candidate declined to answer."
            return

        if section.coverage == CoverageLevel.NOT_STARTED:
            floor, _ = self._turn_bounds(section)
            if section.turns_spent == 0:
                section.coverage = CoverageLevel.NOT_STARTED
            elif section.turns_spent < floor:
                section.coverage = CoverageLevel.INSUFFICIENT
            else:
                section.coverage = CoverageLevel.PARTIAL

        # Declining is not the same as answering badly, and a report that
        # conflates them says something untrue about a person.
        if section.declined_turns and not section.substantive_turns:
            section.coverage = CoverageLevel.INSUFFICIENT
            section.coverage_shortfall = True
            section.shortfall_reason = (
                f"Candidate declined to answer on this topic "
                f"({section.declined_turns} time(s)); no signal gathered."
            )
            return

        if reason in ("hard_stop", "budget_exhausted") and section.coverage in (
            CoverageLevel.NOT_STARTED,
            CoverageLevel.INSUFFICIENT,
        ):
            section.coverage_shortfall = True
            section.shortfall_reason = (
                f"Section ended on {reason} after {section.turns_spent} candidate "
                f"turn(s); insufficient signal gathered."
            )

    def _carry_claims_from(self, section: Section) -> None:
        for claim in section.key_claims:
            if claim.text not in {c.text for c in self._carried_claims}:
                self._carried_claims.append(claim)
        # Newest first, bounded.
        self._carried_claims = self._carried_claims[-self.config.max_carried_claims :]

    # -- over-run squeeze -------------------------------------------------

    def _rebalance_remaining(self) -> None:
        """Weighted squeeze of the sections still to come.

        Policy (CLAUDE.md): squeeze proportionally by competency weight against
        the configured duration plus grace. If a section would fall below its
        floor, shrink the lowest-weight section first and record a coverage
        shortfall — silently blowing past the time limit is worse than honestly
        reporting a gap.
        """
        remaining = [s for s in self._sections[self._index :]]
        if not remaining:
            return

        closing = [s for s in remaining if s.kind == SectionKind.CLOSING]
        competencies = [s for s in remaining if s.kind == SectionKind.COMPETENCY]

        time_left = self.hard_stop_seconds - self.elapsed_seconds
        # The close is protected: running out of time is not a reason to hang up
        # on someone mid-sentence.
        closing_need = sum(s.budget_seconds for s in closing)
        available = time_left - closing_need

        if not competencies:
            return

        planned = sum(s.budget_seconds for s in competencies)
        if available >= planned or planned <= 0:
            return  # No squeeze needed.

        total_weight = sum(s.weight for s in competencies) or float(len(competencies))
        for section in competencies:
            if section.original_budget_seconds is None:
                section.original_budget_seconds = section.budget_seconds

        share = {
            s.id: available * ((s.weight or 1.0) / total_weight) for s in competencies
        }
        floors = {
            s.id: (s.original_budget_seconds or s.budget_seconds)
            * self.config.squeeze_floor_fraction
            for s in competencies
        }

        # Anything squeezed under its floor is pulled back up to the floor; the
        # deficit that creates is taken from the lowest-weight sections first.
        for section in competencies:
            section.budget_seconds = max(share[section.id], floors[section.id])

        overshoot = sum(s.budget_seconds for s in competencies) - available
        if overshoot <= 0:
            return

        for section in sorted(competencies, key=lambda s: s.weight):
            if overshoot <= 0:
                break
            reducible = section.budget_seconds
            take = min(reducible, overshoot)
            section.budget_seconds -= take
            overshoot -= take
            section.coverage_shortfall = True
            section.shortfall_reason = (
                "Time budget squeezed below floor by earlier over-run; "
                "expect insufficient signal for this competency."
            )
