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

import re
from dataclasses import dataclass, field

from loguru import logger

from shared.contracts import (
    UnexplainedTerm,
    Contradiction,
    CoverageLevel,
    InterviewBlueprint,
    KeyClaim,
    SectionKind,
    SectionOutcome,
)

from bot.brain.refusal import is_substantive, looks_like_refusal
from bot.brain.repair import (
    RepairKind,
    asks_yes_or_no,
    classify as classify_repair,
    classify_interjection,
    confirms_dont_know,
)
from bot.brain.withdrawal import (
    StopIntent,
    asked_to_skip_question,
    classify as classify_stop,
    confirms_stopping,
    declines_stopping,
)
from bot.brain.state import BrainConfig, Section, build_sections
from bot.brain.terms import asked_about
from shared.branding import BOT_NAME

#: Two attempts at the same question, then move on. A third time is an
#: interrogation, and recording that the ground was never covered is more
#: honest than grinding at someone who cannot hear it.
MAX_REPAIR_ATTEMPTS = 2

#: How many times the interviewer may ask about one unexplained term.
#:
#: **Two, matching the repair cap, and for the same reason written there: a
#: third time is an interrogation.** Measured on the transcripts that prompted
#: this. With no cap at all, gpt-4.1 asked the identical escalated sentence four
#: times in a row while the candidate gave the identical non-answer, which is
#: worse for a candidate who genuinely cannot explain something than the failure
#: it replaced. In every run where the candidate could explain, the first ask was
#: enough; the second ask exists because the second ask is what caught the
#: deflection. Nothing in any transcript was ever resolved by a third.
#:
#: **Lowered to one and put back.** A term cost three turns — ask, escalate, let
#: go — in a topic that gets four or five, so the escalation looked like the
#: thing to cut. It was not. Distinct subjects per topic read 2.5 before and 2.5
#: after: the freed turns went straight to the depth ramp, which asked about the
#: same term again unprompted. And on a deflecting candidate it cost more, not
#: less, because one ask per term means a topic's allowance buys two different
#: terms rather than two attempts at one, each with its own let-go. 19% of turns
#: became 29%. What was eating the topic was the ramp, not this.
MAX_TERM_ATTEMPTS = 2

#: How many questions a whole interview may spend unpacking terms.
#:
#: **The per-section allowance cannot bound this and five attempts proved it.**
#: A section has two probes and the judge always has something unexplained to
#: nominate, because every answer contains a term it does not define. So the
#: allowance is always spent, on whatever is left. Explaining `bf16` in the
#: fixture took its ten probes to zero and handed all ten to `Mistral jinja
#: template`: 31 probes before, 31 after. Changing WHICH term gets probed has
#: been tried five times; only a total budget addresses the fact that something
#: always does.
#:
#: **Eight, from reading every probe in six interviews and judging whether a
#: human would have asked it.** Worth-asking came to 7, 4 and 8 on the competent
#: candidate and 6, 6, 6 on the deflecting one. Set at the top of that range
#: rather than the mean, for the reason `MIN_COMPETENCY_TURNS` is the old flat
#: ceiling: a cap must not be able to remove a question that was worth asking.
#:
#: Spent front to back, which the same reading says is safe: the worthwhile
#: probes fall early, and the latest of them sat at position seven of twelve.
#:
#: **Nothing is said when it runs out.** The let-go exists because a candidate
#: pushed twice on one word and then met with silence is left wondering whether
#: they failed something. A budget quietly ending is not that: nobody has been
#: pushed, and "I have run out of questions about what words mean" is not a
#: sentence a person says. The section simply asks its own questions.
MAX_TERM_PROBES_PER_INTERVIEW = 8

#: How many questions one section may spend unpacking terms, whatever they are
#: called.
#:
#: **The per-term cap is not enough, because the judge does not name one term
#: once.** Replaying a real interview through it, seven judgements returned
#: "management concurrency", then that plus "session management", then those
#: plus "concurrency control", all three alive at once and none ever dropped.
#: Three strings for one idea, each with its own allowance, is six sanctioned
#: questions about a single word. The candidate got four in ninety seconds and it
#: only stopped because they changed the subject themselves.
#:
#: Bounded per section rather than by matching the strings up. Matching needs a
#: similarity rule, and a rule loose enough to join "session management" to
#: "concurrency control" is loose enough to join things that are not the same at
#: all; this repo has been caught by that shape twice. A section allowance
#: bounds the harm directly instead: the interrogation the candidate feels, and
#: the section spending itself on vocabulary. `technical_depth` in that
#: interview spent 132 seconds and hit its turn ceiling with its own material
#: unasked.
#:
#: It also does not depend on the judge behaving. The judge listed "idempotency
#: keys" as unexplained on the same judgement where the candidate explained them
#: competently, so the stopping condition must not be "the judge stopped
#: reporting it".
MAX_TERM_PROBES_PER_SECTION = 2

#: Seconds of a section's budget that one competency turn is worth.
#:
#: **Measured, not chosen.** Across the twelve competency sections run under the
#: current code, seconds per competency turn: median 21.6, mean 23.4, p10 18.0,
#: p90 32.9. The eight sections from before the turn-counting fix read 7.0s
#: median and are excluded, because that is the bug where fifty-two candidate
#: utterances were counted as twelve turns.
#:
#: A section allocated N seconds can therefore hold roughly N/22 exchanges. That
#: is what turns the employer's time allocation into a number of questions.
#:
#: Re-measure from `section_outcomes` if turn-taking, endpointing or the
#: twenty-word turn rule change: all three move how long an exchange takes.
SECONDS_PER_COMPETENCY_TURN = 22

#: Turns a competency may use beyond its planned material.
#:
#: The cap is not about the candidate's patience and not a round number. It
#: bounds what happens when the allocation is wrong: a large duration with few
#: competencies hands one topic a budget worth thirty or more exchanges, and the
#: budget alone would let it run for twelve minutes on one subject.
#:
#: Derived from what the section actually has to ask. Each seed question carries
#: the three stages of the depth ramp (broad, one layer in, the hard part), and
#: the section's term allowance adds `MAX_TERM_PROBES_PER_SECTION` on top. Past
#: that the interviewer has run out of planned material and is improvising, and
#: improvising is not what a competency budget was allocated for.
_DEPTH_RAMP_STAGES = 3

#: Bounds on how many seed questions one competency is generated with. The floor is
#: the count the blueprint has always asked for; the ceiling keeps a long interview
#: from asking one competency for more questions than a candidate can usefully hear.
MIN_SEED_QUESTIONS = 3
MAX_SEED_QUESTIONS = 8


def seed_questions_needed(budget_seconds: float) -> int:
    """How many seed questions a competency of this length needs to fill its turn cap.

    The inverse of the turn cap in `_turn_bounds`: a section's cap is
    `_DEPTH_RAMP_STAGES * len(seeds) + MAX_TERM_PROBES_PER_SECTION`, so a section
    that can hold N exchanges needs `(N - probes) / stages` seeds. Without enough
    seeds the cap falls below what the time allows, and the interview ends early.

    This is the single definition the blueprint generator asks the model for, so the
    number it requests and the number the brain will use cannot drift apart.
    """
    turns = int(budget_seconds // SECONDS_PER_COMPETENCY_TURN)
    needed_turns = turns - MAX_TERM_PROBES_PER_SECTION
    if needed_turns <= 0:
        return MIN_SEED_QUESTIONS
    seeds = -(-needed_turns // _DEPTH_RAMP_STAGES)  # ceiling division
    return min(MAX_SEED_QUESTIONS, max(MIN_SEED_QUESTIONS, seeds))


#: No competency gets fewer turns than it did before this change. The old flat
#: ceiling becomes the floor of the derived one, so the change can only ever add
#: questions to a section, never take them away.
MIN_COMPETENCY_TURNS = 6

#: The interviewer gets at most this many turns in the closing: one to thank
#: them and invite a last question, one to answer it and say goodbye. Any more
#: and it is keeping somebody on a call that is already over.
MAX_CLOSING_TURNS = 2


def _claim_key(text: str) -> str:
    """An earlier claim, compared without case, punctuation or spacing."""
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())

#: Said in the closing, these mean the interviewer is waiting for a reply even
#: without a question mark. "?" alone was the test, and the real model invited
#: questions with "If you have any brief questions about the role, please ask
#: now." The call took that for a goodbye and hung up on the candidate.
_INVITATION = re.compile(
    r"\b(any|anything|some|other|further|last|final)\b[^.!]{0,20}\bquestions?\b"
    r"|\b(please|feel free to|go ahead and|happy to)\s+(ask|answer|take)\b"
    r"|\bask (away|now|me)\b"
    r"|\bwhat would you like to (ask|know)\b",
    re.IGNORECASE,
)

#: A turn that says goodbye is a goodbye, even if it mentions questions on the way
#: out ("if you have any questions later, the hiring team will help. Goodbye.").
_GOODBYE = re.compile(
    r"\b(good\s*-?\s*bye|bye|take care|all the best|best of luck|good luck|"
    r"have a (good|great|nice|lovely) (day|evening|afternoon|one))\b",
    re.IGNORECASE,
)


def invites_a_reply(said: str) -> bool:
    """Whether a closing turn is waiting for the candidate, or is the goodbye.

    A question mark always waits. Otherwise a goodbye is a goodbye, and an
    invitation phrased as a statement still waits. Anything else is a goodbye,
    because the closing turn cap backs it up either way.
    """
    text = said or ""
    if "?" in text:
        return True
    if _GOODBYE.search(text):
        return False
    return bool(_INVITATION.search(text))


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
    #: Replaced wholesale, never merged. See `apply_judgment`.
    unexplained_terms: list[UnexplainedTerm] = field(default_factory=list)


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


#: Lines the interviewer says that are not questions: the silence nudge and
#: the wait reply. Matched loosely, because the nudge text is configurable.
_FILLER_LINE = re.compile(r"^\W*(?:of course[,.]?\s*)?take your time\b[^?]*$", re.IGNORECASE)


def _is_filler_line(text: str) -> bool:
    return bool(_FILLER_LINE.match((text or "").strip()))


class InterviewBrain:
    """Runs one interview against one blueprint."""

    def __init__(
        self,
        blueprint: InterviewBlueprint,
        *,
        config: BrainConfig | None = None,
        now: float = 0.0,
        time_of_day: str | None = None,
        live_speech: bool = False,
    ):
        self.blueprint = blueprint
        self.config = config or BrainConfig()
        #: True in a live call. There the closing's turns are counted once, by
        #: `bot_finished_speaking` when each utterance stops. Counting them again
        #: from the context frame double-counted every goodbye and let a closing
        #: question end the interview before the candidate had answered it.
        self._live_speech = live_speech
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
        #: Earlier claims already raised, normalised. A judge re-reads the whole
        #: window every turn and words the same inconsistency differently each
        #: time, so keying on `later_statement` alone let one inconsistency come
        #: back under four wordings.
        self._probed_earlier_claims: set[str] = set()
        #: Contradictions the last rendered prompt actually put in front of the
        #: model. Marked probed once the interviewer has spoken, the same way
        #: `_offered_term` works: that turn was its one opportunity.
        self._offered_contradictions: list[Contradiction] = []
        #: Still-unexplained terms for the section in progress, as of the last
        #: judgement. Not accumulated: see `apply_judgment`.
        self._open_terms: list[UnexplainedTerm] = []
        # `term_raises` and `closed_terms` live on the Section. See `state.py`.
        #: Term probes spent across the whole interview, against
        #: `MAX_TERM_PROBES_PER_INTERVIEW`. Kept beside the per-section count
        #: rather than derived from it: the section counts reset and this must
        #: not.
        self._term_probes_spent = 0
        #: Terms the candidate was asked about and then explained, anywhere in
        #: the interview. Interview-wide on purpose: "could they explain this"
        #: is a fact about a person, not about a topic, and an answer given two
        #: topics late is still an answer.
        self._explained_terms: set[str] = set()
        #: Terms already set aside out loud. Was inferred from the report list
        #: until the two came apart.
        self._let_go_said: set[str] = set()
        #: Terms put to the candidate since the last verdict landed. The only
        #: window in which the judge's silence about a term means they answered
        #: it: see `_note_what_they_explained`.
        self._asked_since_judged: set[str] = set()

        #: One term, waiting for the interviewer to say out loud that it is
        #: setting it aside. Consumed by the turn that does it.
        self._term_to_let_go: UnexplainedTerm | None = None
        #: The term the last rendered prompt put in front of the model. It is
        #: an offer, not an ask: the count only moves once the interviewer has
        #: spoken and the question turns out to have been about it.
        self._offered_term: UnexplainedTerm | None = None
        self._consecutive_refusals = 0
        #: What the candidate needs repeated, and how many times we have
        #: already tried. Reset the moment a real answer arrives.
        self._pending_repair: RepairKind = RepairKind.NONE
        self._repair_attempts = 0
        self._question_to_repair: str = ""
        self._repairs_requested = 0
        #: A reply that cannot be what they said ("I know." to an open question)
        #: is checked once per question. Not a repair request, so counted apart.
        self._unclear_checked = False
        self._unclear_replies = 0
        #: The candidate said "yes" to "did you say you don't know?" at least
        #: once. From then on "I know." to an open question is taken as the "I
        #: don't know" they have already told us it is, without asking again:
        #: checking every time is the interrogation loop `int_8e3db77a60a9` ran.
        #: The transcript still keeps exactly what was heard.
        self._dont_know_confirmed = False
        #: Their latest answer is an "I don't know" that reached us as "I know"
        #: or "Yes". Tells the next prompt to take it as an answer and move on.
        #: Cleared once the interviewer has spoken.
        self._answer_is_dont_know = False
        #: The words of a question the candidate spoke over, set by the live
        #: pipeline when the interviewer is cut off, and consumed by the
        #: candidate's next utterance. None when nothing was interrupted.
        self._interrupted_question: str | None = None
        self._interruption_attempts = 0
        self._interruptions_recovered = 0
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
        #: Set by the silence ladder just before it speaks to a silent candidate
        #: during the closing. That utterance is neither a goodbye nor an
        #: invitation, so it must not spend one of the closing's turns. Cleared by
        #: the candidate speaking, so a stale flag cannot swallow a real turn.
        self._silence_prompt_pending = False

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

    @property
    def scheduled_seconds(self) -> float:
        """The interview length the employer configured, excluding grace."""
        return self.blueprint.evaluation_spec.duration_minutes * 60

    @property
    def closing_begins_seconds(self) -> float:
        """When the close must begin so it finishes at the configured duration.

        The closing budget is reserved inside `duration_minutes`, so competencies
        stop here and the goodbye lands on the employer's time. Grace is not used
        for planning: it is only the absolute latest (`hard_stop_seconds`).
        """
        closing = next((s for s in self._sections if s.kind == SectionKind.CLOSING), None)
        closing_budget = closing.budget_seconds if closing is not None else 0.0
        return max(0.0, self.scheduled_seconds - closing_budget)

    @property
    def time_is_up(self) -> bool:
        """True once the scheduled time is spent and the close has not started."""
        if self._finished or self.current_section.kind == SectionKind.CLOSING:
            return False
        return self.elapsed_seconds >= self.closing_begins_seconds

    @property
    def closing_bot_turns(self) -> int:
        return self._closing_bot_turns

    @property
    def session_remaining_seconds(self) -> float:
        """Time left in the scheduled interview, across the whole session.

        Distinct from `section_remaining_seconds` (one topic) and from
        `hard_stop_seconds` (the wall, which includes grace). This is the figure the
        interviewer is given every turn so it can pace the remaining topics.
        """
        return max(0.0, self.scheduled_seconds - self.elapsed_seconds)

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

        # Consumed only when it can actually be said. A contradiction takes the
        # slot ahead of it, and reading it there would throw it away unspoken.
        # Never in the closing. Its mandatory block demands a question, and the
        # closing's last turn must not ask one: `int_8e3db77a60a9` spent its
        # goodbye on "how do those two fit together?" and the call ended on it.
        unprobed = (
            []
            if section.kind == SectionKind.CLOSING
            else self.unprobed_contradictions()
        )
        # Only what the model will actually see counts as offered. A repair or
        # an offer to stop replaces the whole body, contradictions included.
        prompt_shows_body = not self._stop_offered and not (
            self._pending_repair is not RepairKind.NONE and self._question_to_repair
        )
        self._offered_contradictions = list(unprobed) if prompt_shows_body else []
        let_go = self.term_to_let_go() if not unprobed else None
        open_terms = self.unexplained_terms()
        # Which term the prompt will actually carry, by the same precedence the
        # renderer uses. Anything else offered nothing and must not be counted.
        self._offered_term = (
            open_terms[0] if not unprobed and let_go is None and open_terms else None
        )

        return render_section_prompt(
            blueprint=self.blueprint,
            section=section,
            carried_claims=self._carried_claims,
            unprobed_contradictions=unprobed,
            unexplained_terms=open_terms,
            term_to_let_go=let_go,
            remaining_seconds=self.section_remaining_seconds(),
            session_remaining_seconds=self.session_remaining_seconds,
            session_scheduled_minutes=self.blueprint.evaluation_spec.duration_minutes,
            is_last_competency=self._is_last_competency(section),
            has_substantive_answers=self.has_substantive_answers,
            consecutive_refusals=self._consecutive_refusals,
            time_of_day=self.time_of_day,
            pending_repair=self._pending_repair,
            question_to_repair=self._question_to_repair,
            answer_is_dont_know=self._answer_is_dont_know,
            offer_to_stop=self._stop_offered,
            withdrew=self._withdrew,
            # The turn about to be planned is the last one the closing allows.
            final_closing_turn=self._closing_bot_turns >= MAX_CLOSING_TURNS - 1,
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
            # Cleared BEFORE counting, not after. Counting this turn may queue a
            # new let-go, and clearing afterwards would throw it away in the same
            # breath, which is how the sentence went missing in the first place.
            # Anything queued before this turn has now had its chance: the
            # interviewer has spoken.
            if self._term_to_let_go is not None:
                logger.info(f"let go of {self._term_to_let_go.term!r}")
                self._term_to_let_go = None
            self._count_if_it_asked(bot_text)
            # The interviewer has had its one opportunity to raise these. Marked
            # whether or not it took it: the policy bounds re-raising, it does
            # not guarantee the question was asked. Only the text harness used
            # to do this, so live a contradiction was re-rendered every turn and
            # `int_8e3db77a60a9` asked the same one seven times, into the close.
            offered, self._offered_contradictions = self._offered_contradictions, []
            for contradiction in offered:
                self.mark_contradiction_probed(contradiction)
            self._answer_is_dont_know = False

        # Audio captured before the interviewer has said a word is not an
        # answer. A live session picked up a bystander in the room saying
        # "Enjoyment"; it was counted as the candidate's first turn, which
        # advanced the opening past its greeting stage — so the bot never
        # introduced itself, and the candidate had to ask who it was talking to.
        if candidate_text and not self._interviewer_has_spoken:
            self._ignored_before_start.append(candidate_text)
            return

        if candidate_text:
            # The candidate is talking, so any silence prompt we announced is over.
            self._silence_prompt_pending = False
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

            # Spoken over an unfinished question. Handled before ordinary repairs,
            # because "sorry?" here means they never heard the end of it, not that
            # a whole question failed to land: the reply is the full question.
            if self._interrupted_question is not None:
                cut, self._interrupted_question = self._interrupted_question, None
                kind = classify_interjection(candidate_text)
                if kind is not RepairKind.NONE and self._interruption_attempts < MAX_REPAIR_ATTEMPTS:
                    # Like a repair, this carries no answer, so it spends no turn
                    # and the depth ramp does not advance on it.
                    self._pending_repair = kind
                    self._question_to_repair = cut or "(you were cut off before the question itself)"
                    self._interruption_attempts += 1
                    self._interruptions_recovered += 1
                    if kind is RepairKind.HOLD:
                        # They have still not heard it. Keep it for when they are
                        # back: "okay, go ahead" then gets the question in full.
                        self._interrupted_question = cut
                    logger.info(
                        f"[{section.id}] interviewer was cut off ({kind.value}); "
                        f"the question will be {'held' if kind is RepairKind.HOLD else 'asked again in full'}"
                    )
                    return
                # Otherwise they were already answering, or it has been tried
                # twice: take what they said as the answer, below.

            repair = classify_repair(candidate_text)

            # **A reply that cannot be what they said gets one check, and only
            # one.** See `_UNCLEAR_REPLIES` in `repair.py`: "I know." to an open
            # question is a misheard "I don't know". Asking costs a sentence;
            # guessing puts words in a candidate's mouth either way.
            #
            # Kept out of the hearing path below on purpose. It is not the
            # candidate asking us to repeat, so it is not counted in
            # `repairs_requested`, and it must not use up the two attempts a
            # genuine "sorry, what?" is entitled to. After a closed question
            # ("Do you know...?") "I know." is a fine answer and is left alone,
            # and once checked, whatever comes back is taken as said: a second
            # check is the interrogation the repair cap exists to prevent.
            if repair is RepairKind.UNCLEAR:
                question = self._last_interviewer_text()
                if self._unclear_checked or asks_yes_or_no(question):
                    repair = RepairKind.NONE
                elif self._dont_know_confirmed:
                    # They have already told us this is their "I don't know".
                    # Asking again every time is the loop; taking it as said
                    # ("I know" as a claim of knowledge) is the other failure.
                    repair = RepairKind.NONE
                    self._unclear_replies += 1
                    self._answer_is_dont_know = True
                    logger.info(
                        f"[{section.id}] reply {candidate_text!r} taken as the "
                        f"\"I don't know\" they confirmed earlier"
                    )
                else:
                    self._unclear_checked = True
                    self._unclear_replies += 1
                    self._pending_repair = RepairKind.UNCLEAR
                    self._question_to_repair = question
                    logger.info(
                        f"[{section.id}] reply {candidate_text!r} cannot be what "
                        f"they meant; checking once"
                    )
                    return

            if repair is RepairKind.SMALL_TALK and self._repair_attempts >= MAX_REPAIR_ATTEMPTS:
                # Not a hearing problem, so "they could not hear it twice, set
                # it aside" would be false. Past the cap it is taken as said.
                repair = RepairKind.NONE
            if repair is not RepairKind.NONE:
                # "How are you?" says nothing about the line. Counting it here
                # would read in the report as a connection problem.
                if repair is not RepairKind.SMALL_TALK:
                    self._repairs_requested += 1
                self._question_to_repair = self._last_interviewer_text()
                if self._repair_attempts < MAX_REPAIR_ATTEMPTS:
                    # A request to try again carries no answer. Counting it as a
                    # turn would let the depth ramp advance on nothing and would
                    # spend the section's budget on silence.
                    self._pending_repair = repair
                    self._repair_attempts += 1
                    return

                # They still could not hear it after two attempts. This used to
                # fall through to the answer path, which reset the counter and
                # passed "sorry?" to the model as what the candidate said. The
                # model then followed up on a question nobody had heard, and the
                # cycle restarted: repeat, repeat, follow-up, repeat...
                #
                # Now it is a turn spent with nothing said, not an answer. The
                # counter resets because the interviewer is about to ask a NEW
                # question, which gets its own two attempts.
                self._pending_repair = RepairKind.GIVE_UP
                self._repair_attempts = 0
                section.turns_spent += 1
                logger.info(
                    f"[{section.id}] candidate could not hear the question after "
                    f"{MAX_REPAIR_ATTEMPTS} attempts; setting it aside"
                )
                self._maybe_advance(section)
                return

            # "Yes" to "did you say you don't know?" is their answer to the
            # ORIGINAL question, and that answer is "I don't know". Left as a
            # bare "Yes." the model read it as "you misheard", said "sorry, my
            # mistake", and asked the same question again.
            if self._unclear_checked and confirms_dont_know(candidate_text):
                self._dont_know_confirmed = True
                self._answer_is_dont_know = True
                logger.info(f"[{section.id}] they confirmed \"I don't know\"")

            # A real answer. Any repair still pending is over.
            self._pending_repair = RepairKind.NONE
            self._repair_attempts = 0
            self._question_to_repair = ""
            self._interruption_attempts = 0
            self._unclear_checked = False

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

            # **A skip is a declined question, and that machinery already
            # exists.** `looks_like_refusal` holds "skip it" but matches whole
            # utterances only, so the live one — "right now, I don't know the
            # answer of this question. Can we skip it?" — was read as an answer
            # here and as a withdrawal by the interviewer model's tool. Routing
            # it here costs nothing new: one declined turn, and the refusal
            # block already says acknowledge briefly and ask a different, easier
            # question on the same topic, never re-ask the declined one. That is
            # what a skip should get.
            refused = looks_like_refusal(candidate_text) or asked_to_skip_question(
                candidate_text
            )

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
        if self.current_section.kind == SectionKind.CLOSING and bot_text and not self._live_speech:
            self._closing_bot_turns += 1
            # A closing turn that asks something is inviting a last question, so
            # wait for it. One that does not is a goodbye, and there is nothing
            # left to wait for.
            invited_a_question = invites_a_reply(bot_text)
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
        # Skip the interviewer's own filler. Live in `int_f1b33f1c0472` and
        # `int_39845b4313d4`, "Can you please repeat?" straight after a "Take
        # your time, there's no rush." nudge found the nudge, had nothing to
        # repeat, and the model asked a new question instead.
        fallback = ""
        for turn in reversed(self._transcript):
            if turn.speaker != "interviewer":
                continue
            if _is_filler_line(turn.text):
                fallback = fallback or turn.text
                continue
            return turn.text
        return fallback

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

    def section_budget_spent(self) -> bool:
        """Move on when the current section's time is spent, whether or not anyone talks.

        The budget used to be checked only when the candidate spoke, so a silent
        candidate held a section open past its time. In one measured interview
        `campaign_strategy` ran 336 s against a 196 s budget, and the silence ladder
        ended the call before the next section began. Called by the clock, the same
        way as `begin_time_close`. The closing has its own path and is left alone.
        """
        if self._finished or not self._interviewer_has_spoken:
            return False
        section = self.current_section
        if section.kind not in (SectionKind.OPENING, SectionKind.COMPETENCY):
            return False
        if section.elapsed(self._now) < section.budget_seconds:
            return False
        self._advance("budget_exhausted")
        return True

    def begin_time_close(self) -> bool:
        """Start the close because the scheduled time is spent. Returns whether it did.

        Called by the clock, not by a candidate turn, so a silent candidate still
        reaches the close. Idempotent: once closing has started it does nothing.
        """
        if not self.time_is_up:
            return False
        self._advance("time_up")
        return True

    def silence_prompt_expected(self) -> None:
        """The silence ladder is about to speak to a silent candidate.

        Only the closing needs this. Elsewhere a nudge was never counted as a turn,
        and setting the flag there would leave it lingering into the closing.
        """
        if self.current_section.kind == SectionKind.CLOSING:
            self._silence_prompt_pending = True

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

        # A nudge to a silent candidate is not a closing turn. Counting it let a
        # "Take your time" end the interview with the closing question unanswered.
        if self._silence_prompt_pending:
            self._silence_prompt_pending = False
            logger.info("closing: silence prompt is not a closing turn")
            return

        self._closing_bot_turns += 1
        # A closing turn that asks something is inviting a last question and
        # waits for it. One that does not is a goodbye.
        invited_a_question = invites_a_reply(said)
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
    def interruptions_recovered(self) -> int:
        """How many times a question was spoken over and then asked again in full."""
        return self._interruptions_recovered

    def bot_was_interrupted(self, spoken: str = "") -> None:
        """The candidate spoke over the interviewer before it finished. Called live.

        `spoken` is what actually reached them before the audio stopped, which may
        be nothing, or "Okay." with the question itself never started.

        Without this the cut-off question went into the record as if it had been
        asked, the candidate's "hmm" or "sorry, one second" was taken as the
        answer, a turn was spent, and the interviewer moved on to a new question.
        Measured against the real model: six times out of six. The question was
        never heard in full and its ground was never covered.

        Ignored once the interview is over or the candidate is leaving: nothing
        should be asked again then.
        """
        if self._finished or self._withdrew:
            return
        self._interrupted_question = (spoken or "").strip()

    @property
    def repairs_requested(self) -> int:
        """How many times the candidate asked for something again.

        Reported so a poor connection is visible in the feedback report rather
        than showing up as a candidate who could not answer.
        """
        return self._repairs_requested

    @property
    def unclear_replies(self) -> int:
        """How many replies were checked because what we heard could not be it.

        A hearing fact, not a candidate one: kept apart from `repairs_requested`
        because the candidate did not ask for anything.
        """
        return self._unclear_replies

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
        had_verdict = judgment.section_id in self._verdicts
        self._verdicts[judgment.section_id] = judgment

        section = self._section_by_id(judgment.section_id)
        if section is None:
            return

        # **A closed section keeps the verdict it closed on.**
        #
        # `_advance` queues one last `section_end` judgement for the topic it is
        # leaving, and that judgement lands after the section is over. It used to
        # overwrite `coverage`, which meant the verdict recorded for a section
        # was never the verdict the brain acted on. Measured across ten runs: of
        # 8 sections that ended BECAUSE the judge said `sufficient`, only 2 still
        # said so afterwards. Five said `partial` and one said `insufficient` —
        # including a section that ended at the two-turn floor, written up as a
        # topic the candidate had failed rather than one that had enough.
        #
        # It answers differently to the same transcript because it is asked a
        # different question: `_advance` runs `_carry_claims_from` first, so the
        # section's own claims arrive labelled "claims from earlier sections".
        # **Freezing the verdict does not fix that mislabelling** — it only stops
        # it rewriting history. See `_advance`.
        #
        # Harmless until the verdict was promoted above the ceiling in
        # `_advance_reason`; from that point a section could end on a verdict and
        # then be recorded as the opposite.
        #
        # Everything else on a late judgement is still applied. Claims and
        # contradictions are additive and a contradiction found at section end is
        # worth having; only the verdict, and the sentence explaining it, are
        # decided once.
        #
        # **"Closed" is not enough on its own, and the suite said so.** A `depth`
        # judgement requested while a section was live can also land after the
        # ceiling has moved us on, and that one carries the only reading anybody
        # ever made of the topic — refusing it would lose the assessment
        # entirely and leave the heuristic fallback in its place. So the rule is
        # narrower: a closed section keeps a verdict it ALREADY HAS. A closed
        # section that never got one still accepts a late reading, which is
        # information rather than a rewrite, and is exactly what
        # `test_late_judgment_for_a_finished_section_is_still_recorded` pins.
        decided = section.ended_at is not None and had_verdict
        if not decided and judgment.coverage is not None:
            section.coverage = judgment.coverage
        if not decided and judgment.rationale:
            section.depth_rationale = judgment.rationale

        for claim in judgment.claims:
            if claim.text not in {c.text for c in section.key_claims}:
                section.key_claims.append(claim)

        # **Replaced, never merged, and that is the whole persistence rule.**
        # The judge is asked each time for the terms that are STILL unexplained
        # as of now, so a term dropping off its list is how the interviewer
        # learns it can stop asking. Accumulating instead would leave a term
        # open for ever once it had been reported once, and the interviewer
        # would keep asking about something the candidate had since explained.
        #
        # The raise count survives the replacement, so a term that has been
        # asked about and deflected is still known to have been asked about.
        # That is the case this exists for.
        if judgment.section_id == self.current_section.id:
            for term in judgment.unexplained_terms:
                term.times_raised = section.term_raises.get(term.term.lower(), 0)
            self._open_terms = judgment.unexplained_terms
            section.unexplained_terms = list(judgment.unexplained_terms)
            self._note_what_they_explained(section, judgment)
            self._let_go_of_what_was_asked_and_not_answered(section)

        for contradiction in judgment.contradictions:
            if contradiction.later_statement not in {
                c.later_statement for c in self._all_contradictions
            }:
                if _claim_key(contradiction.earlier_claim) in self._probed_earlier_claims:
                    contradiction.probed = True
                section.contradictions.append(contradiction)
                self._all_contradictions.append(contradiction)

    def _maybe_request_judgment(self, section: Section) -> None:
        """Ask for a fresh read after every answer in a competency section.

        **This used to run once per section and the verdict was then applied to
        the rest of it.** It fired only inside a turn band, and returned early
        forever once a verdict had landed, so a section's assessment was formed
        from its first couple of minutes. Measured on the scripted runs with a
        three-turn ceiling, that meant the verdict existed for exactly the last
        turn of each section, by which point the section was ending anyway.

        Now every answer is judged. The cost is one off-path call per turn, which
        has been considered and accepted; what it buys is a verdict about the
        conversation as it currently stands rather than as it opened.

        Still nothing awaits it. The request is queued here and drained by the
        driver, which spawns it without waiting, exactly as before. A section
        keeps whatever verdict it has until a newer one arrives, so a slow or
        dead judge degrades to the old behaviour rather than to none.

        The single-flight guard stays: one request outstanding at a time. Without
        it a slow judge would have several assessments of the same section racing
        each other, and the last to land would win regardless of which was
        freshest.
        """
        if section.kind != SectionKind.COMPETENCY:
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

        # An explicit value still pins it. Only the deterministic tests do that,
        # to drive a section to its end without playing out fourteen turns.
        if self.config.ceiling_turns is not None:
            return self.config.floor_turns, self.config.ceiling_turns

        # **Proportional to what this section was actually given.** A flat six
        # meant an eleven minute competency and a four minute one got the same
        # number of questions, and across fifteen interviews the ceiling ended
        # fourteen of twenty sections while the budget ended none. Interviews ran
        # at a fifth of the length the employer had configured and the candidate
        # had been told out loud.
        earned = int(section.budget_seconds // SECONDS_PER_COMPETENCY_TURN)
        planned = (
            _DEPTH_RAMP_STAGES * len(section.seed_questions) + MAX_TERM_PROBES_PER_SECTION
        )
        return self.config.floor_turns, max(MIN_COMPETENCY_TURNS, min(earned, planned))

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
            and _claim_key(c.earlier_claim) not in self._probed_earlier_claims
        ]

    # -- unexplained terms ------------------------------------------------

    def unexplained_terms(self) -> list[UnexplainedTerm]:
        """Terms the candidate has used without saying what they do.

        Modelled on `unprobed_contradictions` deliberately. A contradiction is
        held as a specific item until it has been raised, and the block that
        renders it names that item and supplies the question; measured at 80% on
        the mini-tier model. The verdict block added earlier described a state
        instead and was measured at no effect at all. Shape, not emphasis.

        The difference from contradictions is when an item closes. A
        contradiction closes when it has been *raised*, because raising it twice
        is prosecutorial. A term closes when it has been *explained*, which is
        not the same thing and is the entire failure being fixed: the
        interviewer asked once, got a restatement, and treated having asked as
        having found out.

        Only the judge can tell whether an answer explained anything, so the
        judge decides: it is asked each turn for what is still unexplained, and
        this returns whatever it last said. Ordered so the term the interviewer
        has already asked about comes first.

        **A second sort key was tried here and reverted.** It preferred a term
        no earlier section had spent its asks on, to stop every section opening
        on the same word. It cannot work from here: `_open_terms` is assigned
        only in `apply_judgment` and only for the current section, so at a
        section's first turn the list still holds the previous section's terms.
        All of them were asked elsewhere, the tie-break marks them equally
        stale, and the judge's order decides again. Correct inside a section,
        where nothing needed changing, and inert at the boundary, which is the
        only moment it was for. See the changelog for what it cost.
        """
        # Imported here rather than at module scope, the way this file already
        # imports the prompt renderer: `bot/brain/` stays free of anything that
        # reads the environment at import time.
        from bot.config import term_probing_disabled

        if term_probing_disabled():
            # **One gate, and it is the only place the feature can be switched
            # off from.** Everything downstream reads this: the prompt block
            # renders from what this returns, `_offered_term` is taken from it,
            # and the director's filler asks it too. Returning nothing here
            # means no term is put in front of the model, so none is ever
            # counted, capped, escalated, let go of, or written to the report.
            #
            # What carries on is everything else. The judge still runs after
            # every answer and still names the terms it thinks are unexplained;
            # `section.unexplained_terms` still records what it said. The depth
            # ramp still fires, contradictions still take the mandatory slot,
            # and the section's own questions are untouched.
            return []
        if self._term_probes_spent >= MAX_TERM_PROBES_PER_INTERVIEW:
            # The interview's whole budget is gone. Whatever is still unexplained
            # is a fact for the report rather than another question, and the
            # per-section allowance below no longer gets a say: it is the thing
            # that could not bound this.
            return []
        section = self.current_section
        if section.kind != SectionKind.COMPETENCY:
            return []
        if section.term_probes_spent >= MAX_TERM_PROBES_PER_SECTION:
            # Spent. Whatever is still unexplained is a fact for the report, not
            # another question for the candidate.
            return []
        # **`times_raised` is a cached copy of this section's count, and at a
        # section's first turn the cache is a section out of date.**
        #
        # `_open_terms` is only replaced in `apply_judgment`, and only for the
        # section being judged, so at the first turn of a new section it still
        # holds the previous section's term objects. Those carry the previous
        # section's raise count. `section.term_raises` is correct and says zero;
        # the field on the object says one or two, and the field is what the
        # prompt reads.
        #
        # So the block rendered "you have said X twice now" as a section's FIRST
        # ask, 29 times across six runs, and the brain logged "raised 1x" on the
        # same turn. That is the falsehood `terms.py` exists to prevent —
        # telling somebody they have been asked twice when they have been asked
        # once — and it was not the model imitating anything. Checked: the
        # phrase was nowhere in its context on any of the 29, not in the bridge
        # and not in the window. The prompt told it to say that.
        #
        # Refreshed from the section, which is the authoritative count, before
        # anything reads it. `apply_judgment` already makes exactly this
        # assignment; doing it here too means it no longer depends on a fresh
        # judgement having arrived. It also fixes the ordering below, which was
        # sorting by the same stale field.
        for term in self._open_terms:
            term.times_raised = section.term_raises.get(term.term.lower(), 0)

        return sorted(
            (t for t in self._open_terms if t.term.lower() not in section.closed_terms),
            key=lambda t: -t.times_raised,
        )

    def terms_asked_about(self) -> set[str]:
        """Every term actually put to the candidate, lowercased.

        Public because a measurement script was reading `_term_raises` off the
        brain and broke silently the day that state moved onto the Section. What
        it wants is this question, not that field.
        """
        return {key for section in self._sections for key in section.term_raises}

    def mark_term_raised(self, term: UnexplainedTerm) -> None:
        """Record that the interviewer was told to ask about this.

        Not that it did ask, and not that it was answered. It is the count of
        opportunities given, which is what lets the wording escalate from "walk
        me through how that works" to "you have said this twice and I still do
        not know what it does".
        """
        key = term.term.lower()
        section = self.current_section
        self._term_probes_spent += 1
        section.term_raises[key] = section.term_raises.get(key, 0) + 1
        self._asked_since_judged.add(key)
        term.times_raised = section.term_raises[key]
        section.term_probes_spent += 1
        logger.info(
            f"term probe spent: {term.term!r} raised {term.times_raised}x, "
            f"section {section.id} has spent {section.term_probes_spent} of "
            f"{MAX_TERM_PROBES_PER_SECTION}"
        )
        if section.term_probes_spent >= MAX_TERM_PROBES_PER_SECTION:
            # The section is done asking, whichever term was last. Said out loud
            # once: a cap that quietly stops asking leaves the candidate working
            # out whether they failed something.
            self._term_to_let_go = term

        if term.times_raised >= MAX_TERM_ATTEMPTS:
            # Closed to further asking IN THIS SECTION. It has had its
            # allowance here; a later section starts it again from nothing,
            # which is what lets a communication topic ask how they explained
            # something a technical topic asked the workings of.
            section.closed_terms.add(key)

        # **The let-go is NOT queued here**, and that is kept from the one-ask
        # experiment because it is right at any cap. This runs as the question
        # is spoken, before anybody has answered it, so queueing here says "let's
        # leave that one there" on the strength of having asked rather than on
        # the strength of the answer. At two asks that was survivable only by
        # accident: the second ask happened to give the judge a turn to rule on
        # the first answer.
        #
        # It is queued in `apply_judgment` instead, the first moment we know
        # whether the answer explained anything. Explained, and nothing is said
        # and nothing reaches the report. Still unexplained, and the sentence is
        # queued and the term is recorded as asked-and-unanswered. It also fixed
        # a count that had been wrong for days: let-gos actually spoken on the
        # deflecting candidate went from 3, 3, 4 to 9, 5, 5.

    def _note_what_they_explained(self, section: Section, judgment: Judgment) -> None:
        """A term was asked about here and the judge has stopped reporting it.

        That is the only positive evidence an explanation ever produces: the
        judge is asked each turn for what is STILL unexplained, so a term
        dropping off its list after being asked about is the candidate having
        answered it.

        **Gated on the term having been asked about since the last verdict, and
        that window is the whole of the soundness.** A first version looked at
        everything this section had ever asked and struck anything missing from
        the judge's current list. That is wrong, and measurably: a term leaves
        the list when the conversation moves on at least as often as when it is
        explained, and it struck two of the three terms a run recorded. The
        judge answers "what is unexplained in what I can see", so its silence
        only means an answer on the one verdict that read the answer to the
        question about that term.

        Once explained it is struck from every topic's record, including topics
        that recorded it earlier. The report says what a person could and could
        not do, and answering two topics late is still answering. It also fails
        in the direction this repo chooses everywhere else: an unstable judge
        drops a claim about somebody rather than inventing one.
        """
        asked, self._asked_since_judged = self._asked_since_judged, set()
        still_open = {t.term.lower() for t in judgment.unexplained_terms}
        for key in asked:
            if key in still_open or key in self._explained_terms:
                continue
            self._explained_terms.add(key)
            logger.info(f"explained after asking: {key!r}, struck from the record")
            for other in self._sections:
                other.unexplained_after_asking = [
                    t for t in other.unexplained_after_asking if t.term.lower() != key
                ]

    def _let_go_of_what_was_asked_and_not_answered(self, section: Section) -> None:
        """A term was asked about, and the answer did not explain it. Say so.

        Called only from `apply_judgment`, because only the judge can tell an
        explanation from a restatement, and it is the judge still listing a term
        after it was asked about that makes the let-go true.

        Queued once per term. The judge re-reports an unexplained term on every
        subsequent turn, and re-queueing would have the interviewer set the same
        thing aside repeatedly.
        """
        for term in self._open_terms:
            key = term.term.lower()

            # **The RECORD turns on having been asked, not on the allowance.**
            # Those were one condition and they answer different questions. A
            # term asked once here and once two topics later never spends either
            # topic's allowance, so under the old rule it reached no report at
            # all: measured at five terms recorded falling to two. Being asked
            # once and not answering is the fact a reader wants, wherever it
            # happened, and the section it happened in is where it is written.
            if section.term_raises.get(key, 0) >= 1 and key not in self._explained_terms:
                if all(t.term.lower() != key for t in section.unexplained_after_asking):
                    section.unexplained_after_asking.append(term)

            # **The LET-GO turns on the allowance, and does not change.** It is
            # the sentence said out loud when this topic has finished asking, so
            # it belongs to the topic that ran out of asks.
            if key not in section.closed_terms:
                continue
            if key in self._let_go_said:
                continue
            self._let_go_said.add(key)
            self._term_to_let_go = term
            logger.info(f"asked and not answered, letting go of {term.term!r}")

    def _count_if_it_asked(self, bot_text: str) -> None:
        """Spend an attempt only if the question was actually about the term.

        The model is handed a term and may ask about something else entirely.
        Counting the offer told a candidate they had been asked twice when they
        had been asked once, and burned the two-attempt allowance on turns where
        nothing was put to them.

        A term that was offered and not used stays open and unspent, so it comes
        back next turn with the same first-ask wording.
        """
        offered, self._offered_term = self._offered_term, None

        # **Naming is read before pointing, across every open term.**
        #
        # The real probes look like "You said bf16. Walk me through how that
        # actually works." They name one term and point with the same breath, so
        # checking the offered term with both tests first charged the wrong term
        # whenever the model unpacked something other than what it was handed.
        # A question that says a term's name is about that term; the deictic
        # phrase after it is the sentence the block supplied, not a second
        # subject. Caught by the test below, not by reading.
        # **The widened search respects the same gates as the offer.**
        #
        # `_term_probes_spent` is both "how many probes happened" and "how much
        # budget is left", and widening the count without widening the gates set
        # the two against each other: three runs of ten spent 9, 10 and 12
        # against a budget of 8, and five sections went past an allowance of 2.
        # A counter that breaks its own ceiling is worse than one that
        # undercounts, because everything downstream reads it as budget.
        #
        # The arithmetic that settles it: clipped to the budget, the widened
        # count totals exactly what the narrow one did, 67 across ten runs.
        # Every probe this found was one the budget had already refused to pay
        # for. See the changelog: the gap is not a counting problem.
        section = self.current_section
        gated = (
            self._term_probes_spent >= MAX_TERM_PROBES_PER_INTERVIEW
            or section.term_probes_spent >= MAX_TERM_PROBES_PER_SECTION
        )
        candidates = ([offered] if offered is not None else []) + (
            [] if gated else [t for t in self._open_terms if t is not offered]
        )
        for term in candidates:
            if term is not offered and term.term.lower() in section.closed_terms:
                continue
            if asked_about(term.term, bot_text, allow_deictic=False):
                self.mark_term_raised(term)
                return

        # Nothing named. A deictic question points back at whatever was put in
        # front of the model this turn, and at nothing else.
        if offered is not None and asked_about(offered.term, bot_text):
            self.mark_term_raised(offered)
            return

        # **Why the search widened beyond the offered term.**
        #
        # The count used to stop at the offered term, so a probe about a
        # different term happened uncounted, uncapped, and with no let-go. The
        # allowance therefore bounded one term's appearances rather than term
        # probing: measured across ten runs, 89 probe-shaped questions against
        # 67 counted, and ten of sixty sections holding more probes than the
        # per-section allowance permits, the worst holding six with none
        # recorded at all.
        #
        # Nothing new is needed to recognise these. The judge returns a LIST of
        # open terms and only one of them is offered; a question that names any
        # of the others is a question about a term the brain already knows. That
        # is `asked_about`'s first branch applied to a wider set, not a
        # similarity rule: the comparison is still "does this question contain a
        # distinctive word of this exact term", with no threshold and no scoring.
        #
        # The deictic branch is deliberately NOT extended. "How does that
        # actually work" refers to whatever was just offered, and charging it to
        # some other open term would be a guess in a mechanism whose whole design
        # is to fail towards not counting.
        #
        # `_open_terms` is ordered most-raised first, so a probe that names two
        # open terms is charged to the one nearest its cap. That is the one whose
        # let-go is owed to the candidate.

    def term_to_let_go(self) -> "UnexplainedTerm | None":
        """A term that has had its allowance and is being set aside.

        **Not consumed on read**, and that is a fix rather than an oversight. It
        was consumed on read, and in a live interview a term reached its cap,
        queued this, and the sentence was never spoken: the queue was emptied by
        a render whose output never reached the candidate. Reading a prompt is
        not the same event as saying something.

        It is cleared instead when the interviewer next actually speaks, in
        `observe`, so it survives any number of renders and is said exactly once.
        """
        return self._term_to_let_go

    def mark_contradiction_probed(self, contradiction: Contradiction) -> None:
        self._probed_contradictions.add(contradiction.later_statement)
        self._probed_earlier_claims.add(_claim_key(contradiction.earlier_claim))
        contradiction.probed = True
        # Every other wording of the same inconsistency was raised by this one.
        for other in self._all_contradictions:
            if _claim_key(other.earlier_claim) == _claim_key(contradiction.earlier_claim):
                other.probed = True

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

        # The employer's scheduled time is spent. Stop topics and close now, even
        # if the candidate is still talking: the configured length is the end, and
        # grace is only the absolute latest.
        if section.kind != SectionKind.CLOSING and self.time_is_up:
            return "time_up"

        # The candidate is declining everything. Stop interviewing them.
        if self._consecutive_refusals >= self.config.refusals_before_closing:
            return "candidate_disengaged"

        # Declined this topic twice. Change the subject rather than ask again.
        if (
            section.kind == SectionKind.COMPETENCY
            and self._consecutive_refusals >= self.config.refusals_before_topic_change
        ):
            return "declined_topic"

        # **The verdict is checked BEFORE the ceiling, and that ordering is the
        # whole of this rule.**
        #
        # It used to sit fifth, below the ceiling and the budget. The ceiling is
        # derived from the section's own allocation, so a section reached it at
        # the moment it had spent everything it was given, and the verdict was
        # then consulted at a point where the only thing it could do was agree
        # with what had already happened. Measured across roughly 320 stored
        # competency sections, "sufficient" was recorded 8 times, and the only
        # shape that could produce an early end was a short low-weight
        # competency whose judge answered before a four-turn ceiling arrived.
        #
        # Nothing about the judge changes here. "Enough" was made explicit when
        # the judge prompt was written; the check was simply below one that
        # always won.
        #
        # **The floor moves with it, and that is not optional.** It used to be a
        # bare `turns_spent < floor -> None` guard sitting above this check, and
        # a verdict promoted above the ceiling without it could end a competency
        # on its first answer. It is a condition on this branch now rather than
        # a separate gate, which leaves every other branch reading exactly as it
        # did: the ceiling and the budget were already above the old guard, so
        # nothing below floor behaves differently.
        #
        # Above this, and staying above it: the hard stop, because an interview
        # that is out of time ends whatever the judge thinks; and both refusal
        # branches, because a candidate declining is a fact about the person and
        # must not be read as a section that went well.
        verdict = self._verdicts.get(section.id)
        if (
            section.turns_spent >= floor
            and verdict
            and verdict.coverage in (CoverageLevel.SUFFICIENT,)
        ):
            return "depth_reached"

        if section.turns_spent >= ceiling:
            return "ceiling"

        if section.elapsed(self._now) >= section.budget_seconds:
            return "budget_exhausted"

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
        if reason in ("hard_stop", "time_up", "candidate_disengaged"):
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

        # **The open-term list belongs to the section that produced it.**
        #
        # It is assigned only in `apply_judgment`, and only for the section being
        # judged, so at a new section's first turn it still held the last
        # section's terms. That is the carry that let one term take a fresh
        # allowance in section after section, and it is why an earlier attempt to
        # change which term gets picked was reverted as inert: the selection rule
        # could not fire at the boundary because the list it sorted was a section
        # out of date.
        #
        # Measured before clearing it: of 13 probes on a section's first turn,
        # **13 were about a term the candidate had not said in that section.**
        # `ml_service_deployment` opened with "You said prompt-level rules. Walk
        # me through how that actually works", carried from `applied_llm_depth`.
        # None was a legitimate "you said this here".
        #
        # What fills that turn instead is what should have had it: the section's
        # own broadest seed question, under the ramp's "START THIS TOPIC BROADLY,
        # ask the widest version of it". The term block firing at turn zero was
        # fighting that instruction.
        #
        # **Cross-section probing is delayed, not lost.** The judge re-reads each
        # section and re-nominates from its transcript, so a term the candidate
        # uses again here comes back with a judgement and can be asked about
        # properly. What goes is asking about a word they have not said in this
        # topic, which is what makes "you said X" false. The authored
        # cross-section material was never here anyway: it is in the seeds, which
        # say things like "how did you communicate the limitations of the
        # language ID prototype to stakeholders".
        self._open_terms = []
        self._offered_term = None

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

        if reason in ("hard_stop", "time_up", "budget_exhausted") and section.coverage in (
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

        # Time handed back by sections that ended early goes forward to the sections
        # still to come, by weight. It is measured against the scheduled length, not
        # the hard stop: grace is a margin for overrun, not time to plan for.
        scheduled_available = self.scheduled_seconds - self.elapsed_seconds - closing_need
        if planned > 0 and scheduled_available > planned:
            surplus = scheduled_available - planned
            total_weight = sum(s.weight or 1.0 for s in competencies)
            for section in competencies:
                section.budget_seconds += surplus * ((section.weight or 1.0) / total_weight)
            return

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
