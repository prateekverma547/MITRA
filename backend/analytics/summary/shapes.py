"""Result shapes of the summary page (Session D). Codes, never display words: Session B
holds the wording. Missing is never zero: a value not recorded is None with a status
saying so. To be adopted into the shared contracts by Session C at the next change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

#: The plan's cost, per candidate: generation = calls stored as job "blueprint";
#: refinement = job "blueprint_refine" (C-158, from 2026-11-19). Rows stored before
#: that keep "blueprint" for both, so on them refinement is inside generation (D-88).
#: goals = job "blueprint_goals" (round 18, Session A: goal writing at booking, C-176).
CostPartName = Literal["realtime", "auditor", "cleaner", "scoring", "plan_generation", "plan_refinement",
                       "plan_goals", "daily"]
#: recorded: the full cost is stored. incomplete: stored for some calls, others carry no price.
#: not_recorded: nothing stored (never read as 0). counted_elsewhere: a candidate's plan cost,
#: counted on that candidate's first interview. not_applicable: the part cannot occur here.
CostStatus = Literal["recorded", "incomplete", "not_recorded", "counted_elsewhere", "not_applicable"]
Owner = Literal["mitra", "code", "plan_refinement", "unknown"]


class CostPart(BaseModel):
    part: CostPartName
    status: CostStatus
    usd: float | None = Field(default=None, ge=0, description="None unless recorded or incomplete.")
    calls_without_price: int = Field(default=0, ge=0)


class InterviewCost(BaseModel):
    parts: list[CostPart]
    #: Sum of the parts that are recorded or incomplete. Never includes a guess.
    recorded_usd: float = Field(ge=0)
    #: True only when every part that can occur is recorded in full.
    complete: bool
    missing_parts: list[CostPartName]


class Ending(BaseModel):
    status: str = Field(description="InterviewStatus code as stored.")
    end_reason: str | None = Field(default=None, description="EndReason code as stored (ended_because).")
    ending: str | None = Field(default=None, description="How it ended (the coordinator's code).")
    hang_up_by: str | None = None
    category: str = Field(description="HR category code (D_architecture 9.3).")
    category_from: Literal["end_reason", "status", "failure", "unknown"]


class AreaRow(BaseModel):
    area_id: str
    planned_questions: int | None = None
    plan_questions_asked: int = 0
    follow_ups: int = 0
    clarifications: int = 0
    easier_variants: int = 0
    coverage: str | None = None


class Areas(BaseModel):
    labels_from: Literal["events", "session_metrics", "none"]
    rows: list[AreaRow]
    off_plan_questions: int = 0
    plan_areas: int | None = None
    plan_areas_with_a_plan_question: int = 0


class Connection(BaseModel):
    recorded: bool
    drops: int | None = None
    counted_rejoins: int | None = None
    longest_gap_seconds: float | None = None
    limit_reached: bool | None = None


class AfterCall(BaseModel):
    enabled_now: bool
    checks: dict[str, str] | None = Field(default=None, description="check -> state; None when nothing has run.")
    second_voice_ranges: int | None = None


class Decision(BaseModel):
    phase: Literal["before_interview", "during_interview", "ending"]
    owner: Owner
    action: str
    reason: str | None = None
    at_seconds: float | None = None
    #: Where it came from: "event:<event_id>", "coordinator_events[<i>]",
    #: "session_metrics.ended_because", "blueprint.hr_findings".
    source: str


class InterviewSummary(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    job_id: str
    candidate_id: str
    candidate_name: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    duration_from: Literal["interview_ended_event", "timestamps", "not_recorded"]
    booked_minutes: int | None = None
    ending: Ending
    areas: Areas
    connection: Connection
    after_call: AfterCall
    decisions: list[Decision]
    cost: InterviewCost
    capacity_refusals: int = 0


class Basis(BaseModel):
    """How a per-job figure was made: what was counted and what was left out, and why."""

    counted: int
    excluded: dict[str, int] = Field(default_factory=dict)


class Figure(BaseModel):
    value: float | None
    unit: str
    basis: Basis


class PartTotal(BaseModel):
    part: CostPartName
    usd: float = Field(ge=0)
    interviews_recorded: int
    interviews_missing: int
    interviews_incomplete: int = 0


class JobSummary(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    job_id: str
    title: str | None = None
    generated_at: datetime
    interviews: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    average_length_seconds: Figure
    total_cost_usd: Figure
    cost_per_interview_usd: Figure
    cost_by_part: list[PartTotal]
    #: Plan costs of the job's candidates who have no interview (not in the total).
    plan_cost_without_interview_usd: Figure
    capacity_refusals: Figure
    after_call_signals: Figure
    decisions_by_owner: dict[str, int]
    interview_rows: list[InterviewSummary] | None = None


class OperationsSummary(BaseModel):
    """System-wide figures not tied to one job."""

    schema_version: Literal[1] = SCHEMA_VERSION
    generated_at: datetime
    capacity_refusals_total: int
    capacity_refusals_without_interview: int
    peak_live_bots: int | None
    bot_limit_seen: int | None
    window_start: datetime | None
    window_end: datetime | None
    interviews_by_category: dict[str, int]


class JobListItem(BaseModel):
    """One job on the console home: only jobs with at least one interview are listed."""

    job_id: str
    title: str | None = None
    interviews: int = Field(ge=1, description="All interviews of the job's candidates, any status.")
    #: The newest interview's time: when it started, or when it was booked if it has not
    #: started (scheduled, no-show). The list is ordered by this, newest first.
    latest_interview_at: datetime | None = None


class RecentInterview(BaseModel):
    """One of the most recent interviews across all jobs (the console home's option)."""

    interview_id: str
    job_id: str
    candidate_name: str | None = None
    status: str
    end_reason: str | None = None
    category: str = Field(description="HR category code (D_architecture 9.3).")
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime | None = None


class JobList(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    generated_at: datetime
    jobs: list[JobListItem]
    #: Only when asked for (?recent=N, at most 50); None otherwise.
    recent_interviews: list[RecentInterview] | None = None
