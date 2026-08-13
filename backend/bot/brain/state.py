"""Section plan and runtime state for the sectioned interview brain.

Kept separate from `brain.py` so the state machine's *data* can be inspected and
asserted on directly in tests without driving the machine.

Nothing in `bot/brain/` may import Pipecat.
"""

from dataclasses import dataclass, field

from shared.contracts import (
    UnexplainedTerm,
    Contradiction,
    CoverageLevel,
    InterviewBlueprint,
    KeyClaim,
    SectionKind,
    SectionOutcome,
)

OPENING_SECTION_ID = "opening"
CLOSING_SECTION_ID = "closing"


@dataclass
class BrainConfig:
    """Tunables for the state machine.

    Deliberately data, not constants scattered through the logic: the depth
    heuristics and the squeeze floor are exactly the things we expect to revise
    once real interviews are observed.
    """

    #: Minimum candidate turns before a competency section may end. Stops the
    #: interview skating over a competency because the first answer sounded
    #: confident.
    floor_turns: int = 2
    #: Maximum candidate turns in a competency section, or None to derive it
    #: from what that section was actually allocated.
    #:
    #: **None is the production path.** It was a flat 6 for every competency
    #: regardless of budget, so an eleven minute section and a four minute one
    #: got the same six questions; see `SECONDS_PER_COMPETENCY_TURN`. An explicit
    #: integer still pins it, which is how the deterministic tests drive a
    #: section to its end in two turns rather than fourteen.
    ceiling_turns: int | None = None

    #: The opening is a warm-up, not a first competency.
    #:
    #: This was briefly set to a single turn on the reasoning that a second
    #: generic exchange duplicates what competency sections do better. That was
    #: right about information and wrong about people: a live session opened
    #: with a deep multi-part question about a specific employer, and the
    #: candidate described it as being banged straight into the deep end.
    #: Two or three light exchanges give someone a runway.
    opening_floor_turns: int = 2
    opening_ceiling_turns: int = 3
    #: The close gets two, so the candidate can actually ask the question the
    #: closing prompt invites them to ask.
    closing_ceiling_turns: int = 2

    #: Floor a section's budget may be squeezed to, as a fraction of its
    #: original allocation, before we call it a coverage shortfall.
    squeeze_floor_fraction: float = 0.5

    #: How many of the section's own turns the model sees verbatim. Older turns
    #: in the same section are dropped; cross-section memory is carried claims.
    verbatim_turns: int = 8

    #: Claims carried into later sections. Newest first.
    max_carried_claims: int = 12

    #: Consecutive refusals before we stop asking about this topic and move to
    #: another. Re-asking a question someone has twice declined is neither
    #: productive nor decent.
    refusals_before_topic_change: int = 2

    #: Consecutive refusals across the interview before we close the session.
    #: A candidate declining everything is disengaged, upset, or done; grinding
    #: through the remaining sections serves nobody.
    refusals_before_closing: int = 4


@dataclass
class Section:
    """One planned unit of the interview."""

    id: str
    kind: SectionKind
    name: str
    budget_seconds: float
    competency_id: str | None = None
    target_depth: str | None = None
    seed_questions: list[str] = field(default_factory=list)
    weight: float = 0.0

    #: Runtime.
    started_at: float | None = None
    ended_at: float | None = None
    turns_spent: int = 0
    coverage: CoverageLevel = CoverageLevel.NOT_STARTED
    depth_rationale: str | None = None
    key_claims: list[KeyClaim] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    #: Still unexplained as of the last judgement.
    unexplained_terms: list[UnexplainedTerm] = field(default_factory=list)
    #: Asked about up to the cap and still not explained. Kept separately
    #: because this is the reportable fact: asked twice, never answered.
    unexplained_after_asking: list[UnexplainedTerm] = field(default_factory=list)
    coverage_shortfall: bool = False
    shortfall_reason: str | None = None

    #: Questions spent unpacking a term the candidate did not explain. Bounded
    #: per section rather than per term: three ways of saying one thing is one
    #: idea, and a section that spends itself on vocabulary never reaches its
    #: own material.
    term_probes_spent: int = 0

    #: How many times each term has been asked about IN THIS SECTION, and which
    #: have had their allowance here.
    #:
    #: **Both were brain-level and both were wrong in the same way, seen from two
    #: sides.** A term that spent its two asks in the first competency was closed
    #: for the rest of the interview, so a later section could not ask about it
    #: even where that was the right question. And a term first raised in the
    #: first competency stayed live to the last, which is what produced the
    #: drift: in one measured run the sixth topic asked six questions and every
    #: one was about material from the first.
    #:
    #: A term is a fact about a section. "fine tuning" carried its raise count
    #: out of `applied_llm_depth` and hit its cap inside `evaluation_reasoning`,
    #: which is the same bug written down two weeks before it was fixed.
    #:
    #: `term_probes_spent` was already scoped this way and is the precedent.
    term_raises: dict[str, int] = field(default_factory=dict)
    closed_terms: set[str] = field(default_factory=set)

    #: Turns where the candidate declined to answer. Reported honestly: "the
    #: candidate declined" is real signal for a human reader, and quite
    #: different from "the answer lacked depth".
    declined_turns: int = 0
    #: Turns that actually contained an answer.
    substantive_turns: int = 0

    #: Set when the squeeze cuts this section's budget.
    original_budget_seconds: float | None = None

    def elapsed(self, now: float) -> float:
        if self.started_at is None:
            return 0.0
        return (self.ended_at or now) - self.started_at

    def to_outcome(self, now: float) -> SectionOutcome:
        return SectionOutcome(
            section_id=self.id,
            kind=self.kind,
            competency_id=self.competency_id,
            coverage=self.coverage,
            depth_rationale=self.depth_rationale,
            key_claims=list(self.key_claims),
            contradictions=list(self.contradictions),
            unexplained_terms=list(self.unexplained_after_asking),
            turns_spent=self.turns_spent,
            seconds_spent=round(self.elapsed(now), 2),
            budget_seconds=round(self.budget_seconds, 2),
            coverage_shortfall=self.coverage_shortfall,
            shortfall_reason=self.shortfall_reason,
            declined_turns=self.declined_turns,
        )


def build_sections(blueprint: InterviewBlueprint) -> list[Section]:
    """Turn a blueprint into the ordered section plan.

    Opening and closing are real sections with real budgets. They were implicit
    before, which is how the PM fixture ended up allocating every minute of the
    interview to competencies and leaving no room to say hello.
    """
    sections = [
        Section(
            id=OPENING_SECTION_ID,
            kind=SectionKind.OPENING,
            name="Opening",
            budget_seconds=blueprint.opening_minutes * 60,
        )
    ]

    for plan in blueprint.competency_plans:
        sections.append(
            Section(
                id=plan.competency_id,
                kind=SectionKind.COMPETENCY,
                name=plan.name,
                budget_seconds=plan.time_budget_minutes * 60,
                competency_id=plan.competency_id,
                target_depth=plan.target_depth,
                seed_questions=list(plan.seed_questions),
                weight=blueprint.weight_for(plan.competency_id),
            )
        )

    sections.append(
        Section(
            id=CLOSING_SECTION_ID,
            kind=SectionKind.CLOSING,
            name="Closing",
            budget_seconds=blueprint.closing_minutes * 60,
        )
    )
    return sections
