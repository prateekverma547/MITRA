"""Data contracts — the single source of truth for shapes crossing module lines.

`app/`, `blueprint/` and `bot/` all import from here. Never redefine these
shapes elsewhere (CLAUDE.md).

Every contract carries a `schema_version`. These are drafts and will iterate;
bumping the version is how we stay honest about that once records exist in
Postgres.

The brain wins contract disputes: these shapes exist to be consumed, and the
brain is the real consumer. Milestone 2's generator adapts to them, not the
reverse.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

SCHEMA_VERSION = 1

#: Employer-configurable interview length. Nothing may treat 40 as more than a
#: default — it is set per role in the admin panel.
MIN_DURATION_MINUTES = 20
MAX_DURATION_MINUTES = 90
DEFAULT_DURATION_MINUTES = 40

#: How long an interview recording is kept before it is deleted.
#:
#: This number is a promise made to a candidate on the consent notice, so it
#: lives here rather than being typed into the copy: the page is rendered with
#: this value substituted in, exactly as the interview-length rule is, and the
#: sweep that actually deletes recordings reads the same constant. A promise on
#: a screen and a job that enforces it must not be able to disagree.
RECORDING_RETENTION_DAYS = 10


class Competency(BaseModel):
    """One thing the interview is meant to evaluate."""

    id: str = Field(description="Stable slug, e.g. 'prioritization'.")
    name: str
    description: str = Field(description="What good looks like, in the employer's words.")
    weight: float = Field(gt=0.0, le=1.0, description="Relative importance within the spec.")


class EvaluationSpec(BaseModel):
    """What the employer wants evaluated.

    Produced by JD upload plus the employer clarification chat (Milestone 2).
    Fixtures hand-write it.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    role_title: str
    seniority: str = Field(description="e.g. 'Senior', 'Staff', 'Director'.")
    experience_expectation: str = Field(description="e.g. '10-11 years in product management'.")
    competencies: list[Competency]
    red_flags: list[str] = Field(default_factory=list)

    #: Employer-configured via the admin panel. Required — the default exists so
    #: the field is ergonomic, not so code can assume it.
    duration_minutes: int = Field(
        default=DEFAULT_DURATION_MINUTES,
        ge=MIN_DURATION_MINUTES,
        le=MAX_DURATION_MINUTES,
    )
    #: How far past `duration_minutes` the interview may run before the brain
    #: forces a close. Set to 0 for a hard wall.
    overrun_grace_minutes: int = Field(default=5, ge=0, le=15)

    language: str = "en"
    tone: str = "warm, professional, conversational"

    @model_validator(mode="after")
    def _spec_can_actually_be_interviewed(self) -> "EvaluationSpec":
        """The two things a spec cannot be and still produce an interview.

        This class had no validator at all. A spec with **zero competencies** was
        accepted and produces an interview that asks nothing; so were weights
        summing to 1.8. `clarify.py` normalises weights and then says in a
        comment that "a genuinely malformed spec still fails validation in the
        contract" — which was not true, because there was nothing here to fail
        it.

        **At least one competency, not the four-to-seven CLAUDE.md asks for.**
        Four-to-seven is a statement about interview quality — "fewer cannot
        cover a role" — and the contract is the wrong place to enforce a quality
        opinion, because it would reject work that already exists and an
        employer's genuine choice. Checked before writing this: `sre_staff.json`
        ships with **3**, and two stored specs in the local database have **2**.
        A range would reject all of them, and none of them is malformed. The
        guidance already lives where it belongs: the clarification prompt asks
        for four to seven, and `generate.py` warns when the competencies are too
        thin for the duration. Zero is different in kind — it is not a thin
        interview, it is no interview.

        **Weights within one percent of 1.0, not exactly 1.0.** Every consumer
        that uses a weight divides by the total (`_recommendation`,
        `_rebalance_remaining`), so the sum is cosmetic to behaviour and the
        check exists to catch gross authoring error rather than arithmetic.
        Erring toward accepting matters here because the system's own repair
        step rounds to four decimals, so a strict equality would reject a spec
        `clarify.py` had just finished normalising. Verified: all three fixtures
        and all six stored specs sum to exactly 1.0000.
        """
        if not self.competencies:
            raise ValueError(
                f"Spec for '{self.role_title}' has no competencies. An interview "
                "with nothing to assess has nothing to ask."
            )
        total = sum(c.weight for c in self.competencies)
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Spec for '{self.role_title}' has competency weights summing to "
                f"{total:g}, not 1.0. Weights are the employer's statement of what "
                "matters and are read as a share of the whole."
            )
        return self

    @property
    def hard_stop_minutes(self) -> int:
        """The wall the brain must not cross."""
        return self.duration_minutes + self.overrun_grace_minutes


class CompetencyPlan(BaseModel):
    """How to actually interview for one competency."""

    competency_id: str
    name: str
    target_depth: str = Field(
        description="What depth of answer counts as sufficient for this seniority."
    )
    seed_questions: list[str] = Field(
        description="Openers. The brain adapts and follows up; it does not read these verbatim."
    )
    time_budget_minutes: float


class ClaimToVerify(BaseModel):
    """Something the CV asserts that the interview should test."""

    claim: str
    source: str = Field(default="cv", description="Where the claim came from.")


class InterviewRegister(BaseModel):
    """How someone in this field actually talks about the work.

    A generic interview sounds like it was written by someone who has never done
    the job. An interviewer for a business analyst should say "requirement
    gathering" and "sign-off", not "process improvement activities".

    **Every term is drawn from the job description or the CV and verified against
    them in code.** Jargon used wrongly is far worse than plain language: a
    specialist hears one misused term and stops trusting the interview. Borrowing
    the documents' own words makes that structurally impossible, in the same way
    that verifying quotes against the transcript makes a fabricated quote
    impossible in the feedback report.
    """

    #: One short phrase: "business analysis in retail banking".
    domain: str = ""
    #: Terms the interviewer may use, every one of them found in the source
    #: documents. Empty is a fine answer and means plain language.
    vocabulary: list[str] = Field(default_factory=list)


class InterviewBlueprint(BaseModel):
    """The candidate-specific interview plan the bot executes.

    The bot treats this as read-only input and must never invent a role outside
    it.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    blueprint_id: str
    evaluation_spec: EvaluationSpec

    candidate_name: str | None = None
    candidate_summary: str | None = None
    claims_to_verify: list[ClaimToVerify] = Field(default_factory=list)

    #: Explicit allocations so competency budgets cannot silently consume the
    #: greeting and the wrap-up. Before these existed the PM fixture's
    #: competencies summed to the entire duration, leaving the brain no room to
    #: open or close.
    opening_minutes: float = Field(default=2.0, ge=0.5)
    closing_minutes: float = Field(default=2.0, ge=0.5)

    competency_plans: list[CompetencyPlan]
    suggested_opening: str
    interviewing_guidance: list[str] = Field(default_factory=list)

    #: The vocabulary of the field, borrowed from the JD and the CV. Optional:
    #: an interview with none of it just speaks plainly, which is never wrong.
    #: Not named `register`, which shadows a Pydantic attribute.
    domain_language: InterviewRegister | None = None

    @model_validator(mode="after")
    def _budgets_fit_configured_duration(self) -> "InterviewBlueprint":
        planned = (
            self.opening_minutes
            + self.closing_minutes
            + sum(p.time_budget_minutes for p in self.competency_plans)
        )
        if planned > self.evaluation_spec.duration_minutes + 1e-6:
            raise ValueError(
                f"Blueprint '{self.blueprint_id}' budgets {planned:g} minutes "
                f"(opening {self.opening_minutes:g} + closing {self.closing_minutes:g} + "
                f"competencies {planned - self.opening_minutes - self.closing_minutes:g}) "
                f"but the spec allows {self.evaluation_spec.duration_minutes}."
            )
        return self

    @model_validator(mode="after")
    def _every_competency_has_a_plan(self) -> "InterviewBlueprint":
        declared = {c.id for c in self.evaluation_spec.competencies}
        planned = {p.competency_id for p in self.competency_plans}
        if declared != planned:
            raise ValueError(
                f"Blueprint '{self.blueprint_id}' competency mismatch. "
                f"In spec but unplanned: {sorted(declared - planned)}. "
                f"Planned but not in spec: {sorted(planned - declared)}."
            )
        return self

    @property
    def role_title(self) -> str:
        return self.evaluation_spec.role_title

    @property
    def total_duration_minutes(self) -> int:
        return self.evaluation_spec.duration_minutes

    def weight_for(self, competency_id: str) -> float:
        for competency in self.evaluation_spec.competencies:
            if competency.id == competency_id:
                return competency.weight
        return 0.0


# --------------------------------------------------------------------------
# Brain outputs
# --------------------------------------------------------------------------


class SectionKind(StrEnum):
    """What a section is for."""

    OPENING = "opening"
    COMPETENCY = "competency"
    CLOSING = "closing"


class CoverageLevel(StrEnum):
    """How well a section was covered.

    `INSUFFICIENT` is a first-class outcome, not a failure state. A report that
    says "we did not get enough signal here" is more useful — and more honest —
    than one that scores a competency on two sentences.
    """

    NOT_STARTED = "not_started"
    INSUFFICIENT = "insufficient"
    PARTIAL = "partial"
    SUFFICIENT = "sufficient"


class KeyClaim(BaseModel):
    """Something the candidate asserted, anchored to where they said it.

    The anchor matters twice over: it lets the brain quote accurately when
    calling back in a later section, and it is exactly the evidence Milestone 4
    needs to cite. A claim without an anchor is a rumour.
    """

    text: str
    section_id: str
    turn_index: int = Field(description="Index into the interview transcript.")
    at_seconds: float | None = None


class UnexplainedTerm(BaseModel):
    """A term the candidate leaned on without saying what it does.

    Tracked and rendered exactly the way a `Contradiction` is, and for a measured
    reason. A mandatory instruction that names a specific item and supplies the
    question was measured at 80% compliance on the mini-tier model; one that
    describes a state and leaves the model to work out what to do about it was
    measured at zero. "Coverage is insufficient, do not move on" is the second
    shape. "They said session management and never said what it does, ask about
    that" is the first.
    """

    #: The candidate's own words, so the interviewer can quote them back.
    term: str
    #: The sentence it appeared in, for the interviewer to reflect accurately.
    said: str = ""
    section_id: str
    turn_index: int
    #: How many times the interviewer has been told to ask about this. Being
    #: asked once is not enough: a term that was asked about and deflected is
    #: exactly the failure this exists for, so the wording escalates.
    times_raised: int = 0


class Contradiction(BaseModel):
    """A later statement that sits awkwardly against an earlier claim.

    Recorded always. Probed at most once, and neutrally — the bot is gathering
    evidence for a human, never delivering a verdict.
    """

    earlier_claim: str
    earlier_section_id: str
    later_statement: str
    section_id: str
    turn_index: int
    probed: bool = False


class SectionOutcome(BaseModel):
    """What happened in one section of the interview."""

    schema_version: Literal[1] = SCHEMA_VERSION
    section_id: str
    kind: SectionKind
    competency_id: str | None = None

    coverage: CoverageLevel = CoverageLevel.NOT_STARTED
    depth_rationale: str | None = Field(
        default=None, description="Why the judge reached that coverage level."
    )

    key_claims: list[KeyClaim] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    #: Terms the candidate used, was asked about twice, and never explained.
    #: Evidence in its own right: it is the difference between a candidate who
    #: was never asked and one who was asked and could not say. A reader of the
    #: report needs to be able to tell those apart, and the transcript alone
    #: makes it easy to miss.
    unexplained_terms: list[UnexplainedTerm] = Field(default_factory=list)

    turns_spent: int = 0
    seconds_spent: float = 0.0
    budget_seconds: float = 0.0

    #: True when the over-run squeeze cut this section below its floor. The
    #: feedback report must surface this rather than scoring the competency as
    #: though it had a fair hearing.
    coverage_shortfall: bool = False
    shortfall_reason: str | None = None

    #: Turns where the candidate declined to answer. A report must distinguish
    #: "declined to answer" from "answered shallowly" — they mean different
    #: things about a person, and only one of them is about their ability.
    declined_turns: int = 0


# --------------------------------------------------------------------------
# Transcript — the auditable ground truth
# --------------------------------------------------------------------------


class Speaker(StrEnum):
    CANDIDATE = "candidate"
    INTERVIEWER = "interviewer"


class TranscriptTurn(BaseModel):
    """One utterance, anchored in time.

    Timestamps are not decoration. Every score in a FeedbackReport must cite
    evidence, and a quote a human cannot locate in the recording is not
    evidence.
    """

    index: int
    speaker: Speaker
    text: str
    #: Seconds from the start of the interview.
    at_seconds: float
    section_id: str | None = None


class Transcript(BaseModel):
    """The full record of an interview.

    This is the auditable ground truth (CLAUDE.md) and is persisted in full. The
    brain's working context is a lossy, section-scoped view of the conversation;
    this is not. Scoring reads from here, never from the model's context.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    turns: list[TranscriptTurn] = Field(default_factory=list)
    duration_seconds: float = 0.0

    def for_section(self, section_id: str) -> list[TranscriptTurn]:
        return [t for t in self.turns if t.section_id == section_id]

    def quote(self, index: int) -> TranscriptTurn | None:
        return next((t for t in self.turns if t.index == index), None)


# --------------------------------------------------------------------------
# Feedback — evidence for a human, never a decision
# --------------------------------------------------------------------------


class EvidenceQuote(BaseModel):
    """A verbatim quote with the anchor needed to verify it."""

    text: str = Field(description="Verbatim from the transcript. Never paraphrased.")
    turn_index: int
    at_seconds: float
    speaker: Speaker = Speaker.CANDIDATE


class CompetencyScore(BaseModel):
    """One competency, scored strictly against observed evidence."""

    competency_id: str
    name: str
    #: None when there was not enough signal to judge. A null score with an
    #: honest explanation is correct; a guessed number is not.
    score: float | None = Field(default=None, ge=0.0, le=5.0)
    coverage: CoverageLevel
    rationale: str
    evidence: list[EvidenceQuote] = Field(default_factory=list)

    #: Set when coverage was insufficient. The report must say so plainly rather
    #: than scoring a competency the interview never really explored.
    insufficient_signal: bool = False

    @model_validator(mode="after")
    def _no_score_without_evidence(self) -> "CompetencyScore":
        """A score with no quotes behind it is an opinion, not a finding."""
        if self.score is not None and not self.evidence:
            raise ValueError(
                f"Competency '{self.competency_id}' has a score but no evidence. "
                "Every score must cite transcript quotes; if there are none, "
                "leave score None and set insufficient_signal."
            )
        if self.insufficient_signal and self.score is not None:
            raise ValueError(
                f"Competency '{self.competency_id}' is marked insufficient_signal "
                "but still carries a score."
            )
        return self


class ObservedRedFlag(BaseModel):
    """A red flag from the EvaluationSpec that was actually observed."""

    description: str
    evidence: list[EvidenceQuote] = Field(default_factory=list)


class DealbreakerState(StrEnum):
    """What the interview was able to establish about one dealbreaker."""

    #: Quoted the candidate doing the thing the employer said would rule them out.
    OBSERVED = "observed"
    #: Quoted the candidate doing the opposite of it.
    CLEARED = "cleared"
    #: Neither. The interview did not put it to them or observe it either way.
    NOT_REACHED = "not_reached"


class DealbreakerFinding(BaseModel):
    """One of the employer's dealbreakers, and what the interview could say.

    **`red_flags_observed` can only ever say that something fired.** Silence
    there means "we asked and they were fine" or "nobody looked", and a reader
    cannot tell which. Measured across 24 stored reports against one spec: of
    four dealbreakers, one fired in 10 of 24 runs and the other three fired
    zero times, with nothing anywhere recording whether they were cleared or
    never put. In one real interview the report cited two of four and said
    nothing about the rest; the story that would have settled one of them was
    written into the blueprint and sat in a topic the interview never reached.

    So this enumerates **every** dealbreaker in the spec, always, in spec order,
    the way `competency_scores` backfills a competency the model skipped. A
    dealbreaker missing from the report is the failure this exists to remove.

    `red_flags_observed` is unchanged and still carries exactly what it did.
    An `observed` finding here restates one of those: consumers reading the old
    field keep working, and this field is the complete picture.

    **`not_reached` is a fact about the interview, never about the candidate**,
    which is the distinction `QuestioningHealth` was built for. Not being asked
    is not the same as failing to answer, and `as_sentence` says so in words
    rather than leaving a reader to infer it from an enum.
    """

    dealbreaker: str = Field(description="Verbatim from EvaluationSpec.red_flags.")
    state: DealbreakerState
    #: Verified against the transcript exactly like every other quote. Empty on
    #: `not_reached`, and required for anything else: see `_check`.
    evidence: list[EvidenceQuote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "DealbreakerFinding":
        """Neither verdict may stand without a quote.

        A cleared dealbreaker is a claim about a person in a hiring record and
        carries the same evidence standard as a flag, in the same direction:
        the repo already drops an unevidenced red flag as an accusation, and an
        unevidenced clearance is the same error wearing a friendlier face.
        """
        if self.state is not DealbreakerState.NOT_REACHED and not self.evidence:
            raise ValueError(
                f"Dealbreaker {self.dealbreaker!r} is '{self.state}' with no "
                "evidence. Only 'not_reached' may be empty."
            )
        return self

    @computed_field
    @property
    def as_sentence(self) -> str:
        """Plain language for the report.

        Computed rather than a property for the reason on
        `ConversationHealth.degraded`: a property is invisible to every consumer
        reading this over JSON, and the panel and the scripts both do.
        """
        if self.state is DealbreakerState.OBSERVED:
            return "Observed in the interview. The evidence is quoted below."
        if self.state is DealbreakerState.CLEARED:
            return (
                "Addressed. The candidate gave evidence against this, quoted below."
            )
        return (
            "The interview did not put this to the candidate, and did not observe "
            "it either way. This is a gap in the interview, not a finding about "
            "the candidate."
        )


class RecommendationSignal(StrEnum):
    """Framing is deliberate: a signal for a human, never a decision.

    Nothing in this system auto-rejects anyone (CLAUDE.md).
    """

    STRONG_EVIDENCE_FOR = "strong_evidence_for"
    SOME_EVIDENCE_FOR = "some_evidence_for"
    MIXED = "mixed"
    LIMITED_EVIDENCE = "limited_evidence"
    INSUFFICIENT_SIGNAL = "insufficient_signal"


class ConversationHealth(BaseModel):
    """Evidence about the channel, not about the candidate.

    Repairs, dead air, echo and disconnects are all the same kind of fact: the
    connection was poor. Without somewhere to put them they silently become
    evidence about the person instead, and a cheap headset reads as an
    incoherent answer inside a hiring record.

    This is the same distinction the brain already makes by keeping
    `declined_turns` apart from low coverage. "Declined to answer" and "answered
    shallowly" say different things about someone, and only one is about their
    ability. **"Could not be heard" is a third thing**, and it has been getting
    scored as the second.

    Derived from the transcript and the session metrics rather than recorded
    live, so it can be recomputed for interviews that already happened when the
    heuristics improve.
    """

    schema_version: Literal[1] = SCHEMA_VERSION

    candidate_turns: int = 0
    #: Turns too short to carry an answer. A few are normal ("yes", "sure"); a
    #: lot of them means the words were not arriving.
    fragmentary_turns: int = 0
    #: Times the interviewer had to ask for something again.
    repair_requests: int = 0
    #: Turns that were our own speech coming back through their microphone,
    #: usually laptop speakers instead of headphones.
    echo_turns: int = 0
    #: Silences long enough that the interviewer had to prompt.
    prompted_silences: int = 0
    dead_air_seconds: float = 0.0
    disconnects: int = 0

    #: A computed field, not a plain property, because the panel reads this over
    #: JSON. As a property it was invisible to every consumer outside Python:
    #: the report rendered, the banner checked `degraded`, got undefined, and
    #: silently never fired. The unit tests all passed, because they held the
    #: object rather than its serialised form.
    @computed_field
    @property
    def degraded(self) -> bool:
        """True when the channel is a plausible explanation for thin answers.

        Driven by the signals that only occur when something is actually wrong:
        the interviewer having to ask again, our own voice returning, silences
        long enough to prompt, a dropped call.

        `fragmentary_turns` is deliberately **not** decisive on its own. It was,
        and against eight real recorded interviews it flagged five, including
        healthy ones, because the STT emits fillers and mid-sentence pieces as
        separate turns in every session. It is still reported, because it is
        useful colour next to a real problem, but a signal that fires on
        everything cannot be the one that decides anything.
        """
        if self.candidate_turns == 0:
            return False
        return (
            self.disconnects > 0
            or self.prompted_silences >= 3
            or self.repair_requests >= 3
            or self.echo_turns >= 2
            or (self.repair_requests + self.echo_turns) >= 3
            # Fragments only matter when something real corroborates them.
            or (
                self.repair_requests + self.echo_turns >= 1
                and self.fragmentary_turns >= max(4, round(0.3 * self.candidate_turns))
            )
        )

    #: A computed field for the same reason `degraded` is one, and it was added
    #: later for a worse reason: the panel wrote its own copy of this sentence
    #: rather than being sent it. The two drifted, so the scoring model was told
    #: one wording and the employer read another, both claiming to describe the
    #: same recording. This is now the only place it is written.
    @computed_field
    @property
    def as_sentence(self) -> str | None:
        """Plain language for the report, or None when there is nothing to say."""
        if not self.degraded:
            return None
        def plural(n: int, one: str, many: str) -> str:
            return one if n == 1 else many

        parts = []
        if self.repair_requests:
            parts.append(
                f"was asked to repeat themselves {self.repair_requests} "
                f"{plural(self.repair_requests, 'time', 'times')}"
            )
        if self.fragmentary_turns:
            parts.append(
                f"had {self.fragmentary_turns} "
                f"{plural(self.fragmentary_turns, 'answer', 'answers')} arrive incomplete"
            )
        if self.echo_turns:
            parts.append("was heard through their own speakers")
        if self.prompted_silences:
            parts.append(
                f"had {self.prompted_silences} long "
                f"{plural(self.prompted_silences, 'silence', 'silences')}"
            )
        if self.disconnects:
            parts.append(
                f"dropped out of the call {self.disconnects} "
                f"{plural(self.disconnects, 'time', 'times')}"
            )
        if not parts:
            return None
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        return (
            f"The recording was poor: the candidate {joined}. Where the evidence "
            f"below is thin, the connection is a likely cause and not a "
            f"conclusion about the candidate."
        )


#: How much of the spec, by employer weight, may lose its analysis before the
#: report stops being allowed to sound confident. The complement of
#: `CONFIDENT_SIGNAL_REQUIRES_WEIGHT` in `feedback/score.py`, deliberately: that
#: constant already draws the line at a quarter of what the employer cares about,
#: and analysis that never ran leaves the same hole as ground that was never
#: covered. Same boundary, no second judgement call.
JUDGMENT_DEGRADED_ABOVE_WEIGHT = 0.25


class JudgmentHealth(BaseModel):
    """Evidence about our own analysis, not about the candidate.

    During an interview a second, off-path model reads each competency and pulls
    out the specific things the candidate claimed. Those extracted claims are
    what carry between sections and what a report has to point at. When that step
    fails, no claims are extracted, and the result is indistinguishable from a
    candidate who said nothing specific.

    This is the same class of problem as `ConversationHealth` one layer up, and
    it is recorded the same way for the same reason. A technical failure must
    never become evidence about a person. Degraded audio is stated at the top of
    the report and caps the recommendation; so is this.

    Derived from the session metrics the bot already writes, so an old interview
    picks up a health record the next time its report is rebuilt.
    """

    schema_version: Literal[1] = SCHEMA_VERSION

    #: Counted by `BrainDirector`. These three need not add up: the difference is
    #: work cancelled when the pipeline came down, which is normal at the close
    #: of an interview and is not a fault.
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0

    #: Competencies, by name, where every judgement failed and none succeeded.
    #: These are the topics the report has no extracted claims for.
    unjudged_competencies: list[str] = Field(default_factory=list)
    #: Their share of the spec by employer weight. Weight rather than count, for
    #: the reason `_recommendation` already gives: losing the analysis on a 60%
    #: competency is not the same as losing it on a 10% one.
    unjudged_weight: float = 0.0

    @computed_field
    @property
    def cancelled(self) -> int:
        """Attempts that were neither completed nor failed.

        `asyncio.CancelledError` inherits from `BaseException`, so a judgement
        stopped at teardown is caught by nothing and recorded as neither. That is
        correct behaviour and this is the honest name for the remainder.
        """
        return max(0, self.attempted - self.succeeded - self.failed)

    #: A computed field rather than a plain property, for the same reason
    #: `ConversationHealth.degraded` is one: the panel reads this over JSON, and
    #: as a property it would be invisible to every consumer outside Python.
    @computed_field
    @property
    def degraded(self) -> bool:
        """True when enough of the analysis failed that the report is incomplete.

        Keyed on failures alone. Cancellation at teardown is routine and must
        never trip this, or the flag fires on healthy interviews and stops
        meaning anything.

        Two ways in. Everything failing needs no weighting to interpret: nothing
        at all was extracted. Otherwise it is measured the way coverage already
        is, by employer weight rather than by count.
        """
        if self.attempted == 0:
            return False
        if self.failed == self.attempted:
            return True
        return self.unjudged_weight > JUDGMENT_DEGRADED_ABOVE_WEIGHT

    #: Computed, like the one on `ConversationHealth` and for the same reason.
    #: The panel renders what it is sent; it does not write its own version.
    @computed_field
    @property
    def as_sentence(self) -> str | None:
        """Plain language for the report, or None when there is nothing to say.

        Says what happened and what it means. It must not cast doubt on the
        claims that did survive, which are real and were checked against the
        transcript like any other, and it must not read as a remark about the
        candidate.
        """
        if not self.degraded:
            return None

        names = self.unjudged_competencies
        if self.failed == self.attempted or not names:
            opening = (
                "None of our own analysis of this interview finished. The step "
                "that picks out what a candidate said failed throughout."
            )
            gap = "No claims or inconsistencies were drawn from any of it."
        else:
            # Listed after a colon rather than joined with "and". A competency
            # can be called "Metrics and Experimentation", and "Stakeholders and
            # Metrics and Experimentation" reads as three topics rather than two.
            noun = "topic" if len(names) == 1 else "topics"
            opening = (
                f"Part of our own analysis is missing. The step that picks out "
                f"what a candidate said did not finish for {len(names)} {noun}: "
                f"{', '.join(names)}."
            )
            gap = f"No claims were drawn from {'that ' + noun if len(names) == 1 else 'those ' + noun}."

        return (
            f"{opening} {gap} The transcript itself is complete, and every quote "
            f"below was checked against it in the usual way. Read the gaps as "
            f"work that did not finish on our side, not as a candidate who had "
            f"little to say."
        )


class QuestioningHealth(BaseModel):
    """Evidence about how the interview was conducted, not about the candidate.

    The third of these, and the same idea each time. `ConversationHealth` keeps
    a bad microphone from reading as an incoherent person. `JudgmentHealth`
    keeps our own analysis failing from reading as a quiet candidate. This keeps
    **the interviewer repeating itself** from reading as a candidate who repeats
    themselves.

    It was found by reading reports rather than transcripts. Three of six said
    things like "many answers were repeated verbatim and lacked technical
    depth". What happened was the interviewer asking the same question four
    times and the candidate correctly giving the same answer each time; one run
    put the same term to the candidate twice in each of six topics. The report
    took the interviewer's defect and wrote it down about a person.

    Derived from the stored transcript, like the other two, so an interview that
    already happened picks this up the next time its report is rebuilt.
    """

    schema_version: Literal[1] = SCHEMA_VERSION

    interviewer_turns: int = 0
    #: Questions that ask again what an earlier question in the same topic
    #: already asked.
    repeated_questions: int = 0
    #: Questions repeated word for word inside one topic. A subset of the above,
    #: counted separately because it is a different kind of evidence: see
    #: `degraded`.
    verbatim_repeats: int = 0
    #: Topics where that happened more than once.
    narrow_sections: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def degraded(self) -> bool:
        """True when the questioning is a plausible explanation for thin answers.

        A computed field rather than a property for the reason written on
        `ConversationHealth.degraded`: as a property it is invisible to every
        consumer reading this over JSON.

        The threshold is measured, not chosen. Across six competent and three
        deflecting stored runs, questions repeating an earlier one in the same
        topic at 0.6 word overlap ran at 12.6% of interviewer turns where the
        reports called the candidate repetitive, and 5.6% where they did not.
        One repeat is a follow-up circling back. A tenth of the interview is the
        interview going nowhere.

        **Two tests, because a word-for-word repeat is not a matter of degree.**
        The rate test above measures a *similarity*, and similarity is exactly
        the thing a threshold is for: at 0.6 overlap "tell me about a time your
        metrics showed the model underperformed, what did you do" following "tell
        me about a time when your evaluation metrics showed your model
        underperformed" is a legitimate tightening after a let-go, and counting
        one of those as a fault would fire on every interview.

        An identical sentence is a different claim entirely. It is not a
        rephrase, not a narrowing and not a follow-up, because there is nothing
        for it to be: the candidate has already answered these exact words. Live
        in `int_7e31aaff4ab2` the interviewer asked "You said session management.
        Walk me through how that actually works." at 511s, got a real answer, and
        asked the same sentence again at 542s; the candidate began their reply
        "So as I said". The rate test scored that interview 2 repeats against a
        floor of 3 and read healthy, so the report was free to call the answers
        repetitive.

        So one verbatim repeat is enough on its own, and no rate is applied to
        it. It stays rare by construction rather than by threshold: across the
        18 stored runs it occurs once, in that interview.
        """
        if self.interviewer_turns == 0:
            return False
        if self.verbatim_repeats >= 1:
            return True
        return self.repeated_questions >= max(3, round(0.10 * self.interviewer_turns))

    @computed_field
    @property
    def as_sentence(self) -> str | None:
        """Written once, here, for the same reason the other two are.

        The panel wrote its own copy of the channel sentence and the two
        drifted, so the scoring model was told one thing and the employer read
        another. This is the only place this wording exists.
        """
        if not self.degraded:
            return None
        where = ""
        if self.narrow_sections:
            where = f" It happened most in: {', '.join(self.narrow_sections)}."
        if self.verbatim_repeats:
            where = (
                f" {self.verbatim_repeats} of them repeated an earlier question "
                f"word for word.{where}"
            )
        return (
            f"The interviewer repeated itself: {self.repeated_questions} of "
            f"{self.interviewer_turns} questions asked again what an earlier "
            f"question in the same topic had already asked.{where} Thin or "
            f"repetitive answers may reflect the questioning rather than the "
            f"candidate, and a subject nobody asked about is not a subject they "
            f"could not discuss."
        )


class FeedbackReport(BaseModel):
    """Structured evidence for a human decision-maker.

    Explicitly not a hiring decision. The recommendation is a signal about the
    strength of the evidence gathered, not a verdict on the candidate.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    blueprint_id: str
    role_title: str

    competency_scores: list[CompetencyScore]
    red_flags_observed: list[ObservedRedFlag] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)

    #: Every dealbreaker in the spec, and which of three the interview could
    #: say. Unlike `red_flags_observed` this is complete: a dealbreaker nobody
    #: put to the candidate appears here as `not_reached` rather than as
    #: silence. Read it beside `questioning_health`, which explains why.
    dealbreakers: list[DealbreakerFinding] = Field(default_factory=list)

    summary: str = Field(description="What the transcript shows, in plain language.")
    recommendation: RecommendationSignal

    #: Competencies the interview never adequately covered. Surfaced prominently
    #: so a reader knows what this report cannot tell them.
    coverage_gaps: list[str] = Field(default_factory=list)

    #: How well the candidate could actually be heard. Read this before reading
    #: a low score: the two are easily confused and only one is about them.
    conversation_health: ConversationHealth | None = None

    #: How much of our own analysis actually ran. Independent of the above and
    #: never a substitute for it: a report can be degraded on both counts, and
    #: one must not hide the other.
    judgment_health: JudgmentHealth | None = None

    #: How the interview was conducted. Read this before reading a low score or
    #: a red flag that turns on an absence: an interviewer that asked the same
    #: thing repeatedly gathered less than its length suggests.
    questioning_health: QuestioningHealth | None = None

    interview_duration_seconds: float = 0.0

    @property
    def is_decision(self) -> bool:
        """Always False. Present so the intent is impossible to misread."""
        return False
