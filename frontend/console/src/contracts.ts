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

// ---- candidates and screening (backend/hiring/candidates.py, shared/screening.py) ----

export type ScreeningStatus = "waiting" | "running" | "ready" | "failed";

export interface CandidateSummary {
  id: string;
  name: string;
  stage: CandidateStage;
  source_filename: string | null;
  created_at: string | null;
  screening_status: ScreeningStatus;
  score_percent: number | null;
  assessed_weight_percent: number | null;
  threshold_percent: number;
  /** The share of the job's weight the CV must speak to for its score to count. */
  min_assessed_percent: number;
  enough_evidence: boolean;
  /** Needs a written reason to proceed: a low score, or too little to judge. */
  below_threshold: boolean;
}

export interface JobCandidates {
  job: { id: string; title: string; bu_name: string | null; accepting: boolean };
  threshold_percent: number;
  can_add: boolean;
  candidates: CandidateSummary[];
}

/** shared.screening.CompetencyRating */
export interface CompetencyRating {
  competency_id: string;
  competency_name: string;
  weight: number;
  rating: number | null;
  evidence: string[];
  reasoning: string;
}

/** shared.screening.FitAnalysis */
export interface FitAnalysis {
  ratings: CompetencyRating[];
  score_percent: number;
  assessed_weight_percent: number;
  threshold_percent: number;
  summary: string;
  experience_note: string | null;
  identity_fields_removed: string[];
}

export interface HRQuestion {
  id: string;
  text: string;
  why: string;
}

export interface CandidateDetail extends CandidateSummary {
  job: { id: string; title: string; bu_name: string | null };
  cv_text: string;
  screening_error: string | null;
  fit: FitAnalysis | null;
  hr_questions: { questions: HRQuestion[] } | null;
  next: CandidateStage[];
  can_reupload: boolean;
  can_proceed: boolean;
}

export interface DecisionEntry {
  from: CandidateStage;
  to: CandidateStage;
  actor: "system" | "hr" | "bu_head";
  reason: string | null;
  model_recommendation: string | null;
  at: string | null;
}

// ---- the HR round (backend/hiring/hr_round.py) --------------------------------------------

export interface HRRoundState {
  interview_id: string;
  status: "scheduled" | "in_progress" | "completed" | "expired" | "failed";
  meeting_id: string | null;
  password: string | null;
  join_url: string | null;
  expires_at: string | null;
  asked: string[];
  notes: string;
  started_at: string | null;
  ended_at: string | null;
  can_join: boolean;
  can_end: boolean;
  can_cancel: boolean;
  can_edit_notes: boolean;
}

export interface HRRoundView {
  candidate: { id: string; name: string; stage: CandidateStage };
  job: { id: string; title: string; bu_name: string | null };
  questions: HRQuestion[];
  can_book: boolean;
  round: HRRoundState | null;
}

// ---- dashboards, directory, search (backend/hiring/insights.py) ---------------------------

export type GroupKey = "screening" | "hr" | "technical" | "bu_head" | "shortlisted" | "closed";

export interface Overview {
  scope: { business_units: { id: string; name: string }[]; bu: string | null; days: number };
  kpis: {
    open_jobs: number;
    draft_jobs: number;
    active_candidates: number;
    needs_you: number;
    screened: number;
    passed_screen: number;
    added_by_week: number[];
    decided_by_week: number[];
  };
  pipeline: { key: GroupKey; label: string; count: number }[];
  by_bu: { bu_id: string | null; name: string; open_jobs: number; groups: Record<GroupKey, number>; total: number }[];
  needs_you: {
    candidate_id: string;
    name: string;
    job_id: string;
    job_title: string;
    bu_name: string | null;
    stage: CandidateStage;
    action: string;
    waiting_days: number;
  }[];
  activity: { at: string | null; actor: string; candidate_id: string; candidate: string; job_title: string; to: CandidateStage; reason: string | null }[];
  scores: { bins: number[]; threshold: number | null; screened: number; passed: number };
  weeks: string[];
}

export interface DirectoryItem {
  id: string;
  name: string;
  stage: CandidateStage;
  group: GroupKey;
  job: { id: string; title: string };
  bu_name: string | null;
  score_percent: number | null;
  threshold_percent: number | null;
  enough_evidence: boolean | null;
  below: boolean;
  needs: string | null;
  waiting_days: number;
  added: string | null;
}

export interface Directory {
  total: number;
  page: number;
  page_size: number;
  items: DirectoryItem[];
  facets: { groups: Record<GroupKey, number>; needs_me: number; below: number };
  business_units: { id: string; name: string }[];
  groups: { key: GroupKey; label: string }[];
}

export interface SearchResults {
  candidates: { id: string; name: string; stage: CandidateStage; job_title: string }[];
  jobs: { id: string; title: string; bu_name: string | null; request_status: string }[];
  business_units: { id: string; name: string }[];
}

export function initials(name: string | null | undefined): string {
  const parts = (name ?? "?").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export function ago(iso: string | null): string {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

/** Mirrors backend/hiring/insights.py GROUPS. */
export const GROUPS: { key: GroupKey; label: string; stages: CandidateStage[] }[] = [
  { key: "screening", label: "Screening", stages: ["new", "screened"] },
  { key: "hr", label: "HR round", stages: ["ready_for_hr", "hr_booked", "hr_done"] },
  { key: "technical", label: "Technical", stages: ["ready_for_technical", "technical_booked", "technical_done"] },
  { key: "bu_head", label: "With BU head", stages: ["with_bu_head", "on_hold"] },
  { key: "shortlisted", label: "Shortlisted", stages: ["shortlisted"] },
  { key: "closed", label: "Closed", stages: ["rejected", "parked", "withdrew"] },
];
export function groupOf(stage: CandidateStage): GroupKey {
  return GROUPS.find((g) => g.stages.includes(stage))?.key ?? "screening";
}
