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

// ---- job requests (backend/hiring/jobs.py) ------------------------------------------

/** shared.contracts.Competency */
export interface Competency {
  id: string;
  name: string;
  description: string;
  weight: number;
}

/** The parts of shared.contracts.EvaluationSpec the console shows. */
export interface Understanding {
  role_title: string;
  seniority: string;
  experience_expectation: string;
  duration_minutes: number;
  tone?: string;
  competencies: Competency[];
  red_flags: string[];
}

export interface ChatTurn {
  from: "mitra" | "you";
  text: string;
}

export interface JobRequest {
  id: string;
  title: string;
  bu_id: string | null;
  bu_name: string | null;
  request_status: "draft" | "open" | "closed";
  spec_status: "awaiting_clarification" | "ready";
  spec_version: number;
  source_filename: string | null;
  has_jd: boolean;
  created_by: string | null;
  created_at: string | null;
  candidates: number;
  understanding: Understanding | null;
  inferred: string[];
  chat: ChatTurn[];
  can_chat: boolean;
  can_edit: boolean;
  can_approve: boolean;
}

/** One phrase for where a request stands, for lists and headers. */
export function requestPhase(job: Pick<JobRequest, "request_status" | "spec_status">): {
  label: string;
  tone: StageTone;
} {
  const ready = job.spec_status === "ready";
  if (job.request_status === "draft") {
    return ready ? { label: "Ready to approve", tone: "attention" } : { label: "Being described", tone: "progress" };
  }
  if (job.request_status === "closed") return { label: "Closed", tone: "closed" };
  return ready ? { label: "With HR", tone: "good" } : { label: "With HR, being revised", tone: "attention" };
}
