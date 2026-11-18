// TypeScript mirrors of the backend contracts the console reads.
//
// The source of truth is backend/shared/ (stages.py, screening.py). These are
// copied by hand for now, and backend/tests/hiring/test_console_contracts.py
// fails if the stage or role values here ever drift from the Python ones.

export const ROLES = ["org_admin", "hr", "bu_head"] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Record<Role, string> = {
  org_admin: "Organisation",
  hr: "HR",
  bu_head: "BU head",
};

export const CANDIDATE_STAGES = [
  "new",
  "screened",
  "ready_for_hr",
  "hr_booked",
  "hr_done",
  "ready_for_technical",
  "technical_booked",
  "technical_done",
  "with_bu_head",
  "shortlisted",
  "on_hold",
  "rejected",
  "parked",
  "withdrew",
] as const;
export type CandidateStage = (typeof CANDIDATE_STAGES)[number];

/** What HR and BU heads read on a status chip. */
export const STAGE_LABELS: Record<CandidateStage, string> = {
  new: "New",
  screened: "Screened",
  ready_for_hr: "Ready for HR round",
  hr_booked: "HR round booked",
  hr_done: "HR round done",
  ready_for_technical: "Ready for technical",
  technical_booked: "Technical booked",
  technical_done: "Technical done",
  with_bu_head: "With BU head",
  shortlisted: "Shortlisted",
  on_hold: "On hold",
  rejected: "Rejected",
  parked: "Not now",
  withdrew: "Withdrew",
};

/** Tone of a stage's chip: in progress, needs a person, finished well, closed. */
export type StageTone = "progress" | "attention" | "good" | "closed";

export const STAGE_TONES: Record<CandidateStage, StageTone> = {
  new: "progress",
  screened: "attention",
  ready_for_hr: "attention",
  hr_booked: "progress",
  hr_done: "attention",
  ready_for_technical: "attention",
  technical_booked: "progress",
  technical_done: "attention",
  with_bu_head: "attention",
  shortlisted: "good",
  on_hold: "closed",
  rejected: "closed",
  parked: "closed",
  withdrew: "closed",
};

// ---- API shapes ------------------------------------------------------------------

export interface Me {
  id: string;
  email: string;
  name: string;
  role: Role;
  bu_id: string | null;
  bu_name: string | null;
  org_id: string;
}

export interface HomeJob {
  id: string;
  title: string;
  request_status: "draft" | "open" | "closed" | string;
  candidates_by_stage: Partial<Record<CandidateStage, number>>;
}

export interface HomeBusinessUnit {
  id: string | null;
  name: string;
  jobs: HomeJob[];
}

export interface Home {
  me: Me;
  business_units: HomeBusinessUnit[];
}
