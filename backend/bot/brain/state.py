"""Section plan and runtime state for the sectioned interview brain.

Kept separate from `brain.py` so the state machine's *data* can be inspected and
asserted on directly in tests without driving the machine.

Nothing in `bot/brain/` may import Pipecat.
"""

from dataclasses import dataclass, field

from shared.contracts import (
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
    #: Maximum candidate turns in a competency section. Above this we advance
    #: regardless of judgement — a section that will not converge is eating
    #: another competency's time.
    ceiling_turns: int = 6

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
    coverage_shortfall: bool = False
    shortfall_reason: str | None = None

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
