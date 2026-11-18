"""Where a candidate stands in the hiring flow, and who may move them.

One field on the candidate, one state machine, read by every screen. The moves
are data rather than scattered `if` statements so that the console, the API and
the tests all ask the same table, and so a move nobody planned for is refused
instead of quietly allowed.

A model never moves anyone. Every move below names the people who may make it;
"system" moves are the ones that record that a fact has happened (a CV was
screened, an interview finished), never a judgement about the candidate.

Track H owns this file (docs/TRACK_H.md). Contracts change by adding, never by
renaming or removing: Track I reads these values.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class Role(StrEnum):
    """Who is signed in. The account decides this, never a button."""

    ORG_ADMIN = "org_admin"
    HR = "hr"
    BU_HEAD = "bu_head"


class Actor(StrEnum):
    """Who made a move. `system` records a fact; the rest are people."""

    SYSTEM = "system"
    HR = "hr"
    BU_HEAD = "bu_head"


class CandidateStage(StrEnum):
    NEW = "new"  # CV uploaded, screening not finished
    SCREENED = "screened"  # fit analysis ready, waiting for HR
    READY_FOR_HR = "ready_for_hr"  # HR chose "Begin HR round"
    HR_BOOKED = "hr_booked"
    HR_DONE = "hr_done"  # HR round held and evaluated
    READY_FOR_TECHNICAL = "ready_for_technical"
    TECHNICAL_BOOKED = "technical_booked"
    TECHNICAL_DONE = "technical_done"  # report ready
    WITH_BU_HEAD = "with_bu_head"
    SHORTLISTED = "shortlisted"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    PARKED = "parked"  # HR chose "Not now"
    WITHDREW = "withdrew"


S = CandidateStage
_HR = frozenset({Actor.HR})
_BU = frozenset({Actor.BU_HEAD})
_SYS = frozenset({Actor.SYSTEM})
_SYS_HR = frozenset({Actor.SYSTEM, Actor.HR})

#: (from, to) -> who may make the move. Anything absent is refused.
#:
#: Rejection rights follow the decision of 2026-11-18: HR rejects at screening
#: and after the HR round; after the technical round HR can only send the
#: candidate to the BU head, and the BU head decides.
#:
#: A no-show is not a stage. The booking moves back to its "ready" stage with
#: the reason "no_show", so the candidate can be booked again and the history
#: still says what happened.
ALLOWED_MOVES: dict[tuple[CandidateStage, CandidateStage], frozenset[Actor]] = {
    # Screening
    (S.NEW, S.SCREENED): _SYS,
    (S.SCREENED, S.READY_FOR_HR): _HR,
    (S.SCREENED, S.PARKED): _HR,
    (S.SCREENED, S.REJECTED): _HR,
    (S.PARKED, S.READY_FOR_HR): _HR,
    (S.PARKED, S.REJECTED): _HR,
    # HR round
    (S.READY_FOR_HR, S.HR_BOOKED): _HR,
    (S.READY_FOR_HR, S.PARKED): _HR,
    (S.READY_FOR_HR, S.REJECTED): _HR,
    (S.HR_BOOKED, S.READY_FOR_HR): _SYS_HR,  # cancelled or no-show
    (S.HR_BOOKED, S.HR_DONE): _SYS,
    (S.HR_BOOKED, S.WITHDREW): _SYS_HR,
    (S.HR_DONE, S.READY_FOR_TECHNICAL): _HR,
    (S.HR_DONE, S.PARKED): _HR,
    (S.HR_DONE, S.REJECTED): _HR,
    # Technical round
    (S.READY_FOR_TECHNICAL, S.TECHNICAL_BOOKED): _HR,
    (S.READY_FOR_TECHNICAL, S.REJECTED): _HR,
    (S.TECHNICAL_BOOKED, S.READY_FOR_TECHNICAL): _SYS_HR,  # cancelled or no-show
    (S.TECHNICAL_BOOKED, S.TECHNICAL_DONE): _SYS,
    (S.TECHNICAL_BOOKED, S.WITHDREW): _SYS_HR,
    (S.TECHNICAL_DONE, S.WITH_BU_HEAD): _HR,
    # The BU head decides
    (S.WITH_BU_HEAD, S.SHORTLISTED): _BU,
    (S.WITH_BU_HEAD, S.ON_HOLD): _BU,
    (S.WITH_BU_HEAD, S.REJECTED): _BU,
    (S.ON_HOLD, S.SHORTLISTED): _BU,
    (S.ON_HOLD, S.REJECTED): _BU,
}

#: Moves that must carry a written reason. A rejection is a judgement about a
#: real person, so it is never recorded without one; "on hold" and "not now"
#: are only useful later if someone can read why.
REASON_REQUIRED: frozenset[CandidateStage] = frozenset({S.REJECTED, S.ON_HOLD, S.PARKED})

TERMINAL: frozenset[CandidateStage] = frozenset({S.SHORTLISTED, S.REJECTED, S.WITHDREW})


def allowed_next(stage: CandidateStage, actor: Actor) -> list[CandidateStage]:
    """The stages this actor may move a candidate to from here, in table order."""
    return [to for (frm, to), who in ALLOWED_MOVES.items() if frm == stage and actor in who]


def can_move(frm: CandidateStage, to: CandidateStage, actor: Actor) -> bool:
    return actor in ALLOWED_MOVES.get((frm, to), frozenset())


class Decision(BaseModel):
    """One move of one candidate: who, when, what the model said, and why.

    Stored for every move, including the system's. The model's recommendation
    sits beside the person's decision so the record shows whether they followed
    it, which is what the org's transparency view and any later audit need.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    id: int | None = None
    candidate_id: str
    from_stage: CandidateStage
    to_stage: CandidateStage
    actor: Actor
    actor_user_id: str | None = Field(
        default=None, description="None only for system moves."
    )
    reason: str | None = None
    model_recommendation: str | None = Field(
        default=None,
        description="What the model recommended at the moment of the move, verbatim.",
    )
    created_at: datetime | None = None
