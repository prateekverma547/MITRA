"""What the hiring flow learns about a candidate before the AI interview.

Three contracts, all written by Track H and read by Track I and the console:

- `FitAnalysis`: the CV against the job, at upload time.
- `HRQuestionSet`: the ten questions for the human HR round.
- `HRRoundEvaluation`: what the HR round showed, after it, including what
  MITRA's technical plan should probe.

The fit percentage is computed in code from the ratings and the BU head's
weights, never typed by a model. Models are poor at producing numbers that sum
or average exactly (the same lesson as the blueprint's time budgets), and a
percentage that can be recomputed line by line can be explained to the person
it describes.

Contracts change by adding, never by renaming or removing.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from shared.contracts import EvidenceQuote

SCHEMA_VERSION = 1

#: The screening cut-off, as a percentage. Decided 2026-11-18 at 50%. It only
#: changes which buttons HR is offered; it never moves a candidate.
DEFAULT_THRESHOLD_PERCENT = 50.0

#: How much of the job's weight the CV must speak to before its score means
#: anything. "No evidence" is left out of the score, which is right (a missing
#: line is not a zero), but it means a CV that says almost nothing relevant can
#: score well on the one thing it does mention. Found live: a product manager's
#: CV scored 50% for an LLM engineering job, judged on 10% of the weight. Below
#: this line the screen is "too little evidence to judge", treated at the gate
#: like a low score. Same 75%-by-weight rule the feedback report applies to
#: interview coverage (feedback/score.py), for the same reason.
MIN_ASSESSED_WEIGHT_PERCENT = 75.0

#: Ratings run 0-4, the same scale the technical report uses.
MAX_RATING = 4

#: How far a stored percentage may drift from the recomputed one before the
#: contract refuses it. Covers rounding to one decimal place, nothing more.
_SCORE_TOLERANCE = 0.1


class CompetencyRating(BaseModel):
    """One competency from the job, rated against the CV."""

    competency_id: str
    competency_name: str
    weight: float = Field(gt=0.0, description="The BU head's weight, as stored on the spec.")
    rating: int | None = Field(
        default=None,
        ge=0,
        le=MAX_RATING,
        description=(
            "0-4. None means the CV says nothing about it: no evidence, which is "
            "a different statement from a low score and is shown differently."
        ),
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Lines quoted from the CV, verified against its text in code.",
    )
    reasoning: str = ""

    @model_validator(mode="after")
    def _a_rating_needs_evidence(self) -> "CompetencyRating":
        if self.rating is not None and self.rating > 0 and not self.evidence:
            raise ValueError(
                f"{self.competency_id}: a rating above 0 must quote the CV. "
                "Without a quote it is 'no evidence' (rating None)."
            )
        return self


def compute_fit(ratings: list[CompetencyRating]) -> tuple[float, float]:
    """(score percent, assessed share of weight percent), both to one decimal.

    score    = sum(weight x rating) / (MAX_RATING x assessed weight)
    assessed = assessed weight / total weight

    Competencies with no evidence are left out of the score and reported in the
    assessed share instead, so a thin CV reads as "we could judge 60% of this"
    rather than as a weak candidate.
    """
    total = sum(r.weight for r in ratings)
    assessed = [r for r in ratings if r.rating is not None]
    assessed_weight = sum(r.weight for r in assessed)
    if not total or not assessed_weight:
        return 0.0, 0.0
    score = sum(r.weight * r.rating for r in assessed) / (MAX_RATING * assessed_weight)
    return round(score * 100, 1), round(assessed_weight / total * 100, 1)


class FitAnalysis(BaseModel):
    """A CV against a job, at upload time. Evidence for HR, never a decision."""

    schema_version: Literal[1] = SCHEMA_VERSION
    candidate_id: str
    job_id: str
    ratings: list[CompetencyRating] = Field(min_length=1)
    score_percent: float = Field(ge=0.0, le=100.0)
    assessed_weight_percent: float = Field(ge=0.0, le=100.0)
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT
    summary: str = Field(description="Plain-language reasoning for HR.")
    experience_note: str | None = Field(
        default=None,
        description="How the experience compares with what the BU head asked for.",
    )
    identity_fields_removed: list[str] = Field(
        default_factory=list,
        description="What was stripped from the CV before scoring, for the record.",
    )
    model: str = ""
    prompt_version: str = ""

    @model_validator(mode="after")
    def _the_score_is_the_arithmetic(self) -> "FitAnalysis":
        score, assessed = compute_fit(self.ratings)
        if abs(score - self.score_percent) > _SCORE_TOLERANCE:
            raise ValueError(
                f"score_percent {self.score_percent} does not match the ratings "
                f"({score}). Compute it with compute_fit(); never accept a model's number."
            )
        if abs(assessed - self.assessed_weight_percent) > _SCORE_TOLERANCE:
            raise ValueError(
                f"assessed_weight_percent {self.assessed_weight_percent} does not "
                f"match the ratings ({assessed})."
            )
        return self

    @property
    def below_threshold(self) -> bool:
        return self.score_percent < self.threshold_percent

    @property
    def enough_evidence(self) -> bool:
        return self.assessed_weight_percent >= MIN_ASSESSED_WEIGHT_PERCENT

    @property
    def passes_screen(self) -> bool:
        """At or above the cut-off, on enough of the job to mean it. Anything
        else needs a written reason to proceed. It never moves anyone."""
        return self.enough_evidence and not self.below_threshold

    @property
    def no_evidence(self) -> list[CompetencyRating]:
        return [r for r in self.ratings if r.rating is None]

    @classmethod
    def from_ratings(cls, **fields) -> "FitAnalysis":
        """Build one with the numbers computed, which is the only way to build one."""
        score, assessed = compute_fit(fields["ratings"])
        return cls(score_percent=score, assessed_weight_percent=assessed, **fields)


class HRQuestion(BaseModel):
    id: str = Field(description="Stable within the set: 'hr1' .. 'hr10'.")
    text: str
    why: str = Field(description="One line for HR: what this question is for.")


class HRQuestionSet(BaseModel):
    """The ten questions for the human HR round, each with its reason."""

    schema_version: Literal[1] = SCHEMA_VERSION
    candidate_id: str
    job_id: str
    questions: list[HRQuestion] = Field(min_length=10, max_length=10)
    model: str = ""
    prompt_version: str = ""

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "HRQuestionSet":
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids must be unique")
        return self


class HRRecommendation(StrEnum):
    PROCEED_TO_TECHNICAL = "proceed_to_technical"
    REJECT = "reject"
    #: The evidence points neither way. Said plainly rather than forced.
    DISCUSS = "discuss"


class SoftSkillRating(BaseModel):
    name: str
    rating: int | None = Field(default=None, ge=0, le=MAX_RATING)
    evidence: list[EvidenceQuote] = Field(
        default_factory=list,
        description="Verbatim from the raw HR round transcript, verified in code.",
    )
    reasoning: str = ""

    @model_validator(mode="after")
    def _a_rating_needs_evidence(self) -> "SoftSkillRating":
        if self.rating is not None and self.rating > 0 and not self.evidence:
            raise ValueError(f"{self.name}: a rating above 0 must quote the transcript.")
        return self


class HRRoundEvaluation(BaseModel):
    """What the HR round showed. HR decides; this recommends.

    `findings_for_technical` is the part Track I reads: gaps the MITRA
    interview round should probe. Each becomes one neutral probe question in
    MITRA's plan (blueprint/refine.py), so each is phrased as a gap in the
    evidence, never as a conclusion about the candidate.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    candidate_id: str
    interview_id: str
    ratings: list[SoftSkillRating]
    questions_asked: list[str] = Field(
        default_factory=list, description="HRQuestion ids HR ticked as asked."
    )
    recommendation: HRRecommendation
    summary: str
    findings_for_technical: list[str] = Field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
