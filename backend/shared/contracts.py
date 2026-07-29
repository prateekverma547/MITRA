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

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 1

#: Employer-configurable interview length. Nothing may treat 40 as more than a
#: default — it is set per role in the admin panel.
MIN_DURATION_MINUTES = 20
MAX_DURATION_MINUTES = 90
DEFAULT_DURATION_MINUTES = 40


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


class RecommendationSignal(StrEnum):
    """Framing is deliberate: a signal for a human, never a decision.

    Nothing in this system auto-rejects anyone (CLAUDE.md).
    """

    STRONG_EVIDENCE_FOR = "strong_evidence_for"
    SOME_EVIDENCE_FOR = "some_evidence_for"
    MIXED = "mixed"
    LIMITED_EVIDENCE = "limited_evidence"
    INSUFFICIENT_SIGNAL = "insufficient_signal"


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

    summary: str = Field(description="What the transcript shows, in plain language.")
    recommendation: RecommendationSignal

    #: Competencies the interview never adequately covered. Surfaced prominently
    #: so a reader knows what this report cannot tell them.
    coverage_gaps: list[str] = Field(default_factory=list)

    interview_duration_seconds: float = 0.0

    @property
    def is_decision(self) -> bool:
        """Always False. Present so the intent is impossible to misread."""
        return False
