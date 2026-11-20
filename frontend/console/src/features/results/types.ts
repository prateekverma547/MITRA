// Track H. Types for the results page, the comparison and the technical
// booking (backend/hiring/results.py and technical.py). Local to this feature
// so the shared contracts file is not edited while other work is in flight.

import type { CandidateStage, DecisionEntry, HRRoundEvaluation } from "../../contracts";

export type HRRecommendation = HRRoundEvaluation["recommendation"];

/** shared.contracts.RecommendationSignal: the MITRA interview report's signal. */
export type TechnicalSignal = "strong_evidence_for" | "some_evidence_for" | "mixed" | "limited_evidence" | "insufficient_signal";

export interface StrengthOrGap {
  source: "cv" | "hr_round";
  name: string;
  rating: number | null;
  weight_percent: number | null;
  why: string;
  kind: "strong" | "low" | "no_evidence";
}

export interface Results {
  candidate: { id: string; name: string; stage: CandidateStage };
  /** HR skipped the HR round: who, when and the optional reason. */
  hr_skipped?: { by: string | null; at: string | null; reason: string | null } | null;
  job: { id: string; title: string; bu_name: string | null };
  jd_match: {
    score_percent: number;
    assessed_weight_percent: number;
    threshold_percent: number;
    min_assessed_percent: number;
    enough_evidence: boolean;
    passes_screen: boolean;
    summary: string;
    experience_note: string | null;
  } | null;
  rubric: { competency_id: string; name: string; weight_percent: number; rating: number | null; evidence: string[]; why: string }[];
  hr_round: {
    recommendation: HRRecommendation;
    summary: string;
    ratings: HRRoundEvaluation["ratings"];
    findings_for_technical: string[];
    notes: string | null;
    attribution_confidence: "clear" | "likely" | "uncertain" | null;
    evaluated_at: string | null;
  } | null;
  technical: {
    /** Every MITRA round with a report; the report below is the newest, or the one asked for. */
    rounds: TechnicalRound[];
    report: {
      recommendation: TechnicalSignal | null;
      summary: string | null;
      scores: { name: string; score: number | null; insufficient_signal: boolean }[];
      coverage_gaps: string[];
    } | null;
    interview_id: string | null;
    full: FeedbackReport | null;
    transcript: TranscriptLine[];
    ended_at: string | null;
    integrity: IntegrityFacts | null;
  };
  strengths: StrengthOrGap[];
  gaps: StrengthOrGap[];
  sent_to_bu_head: { at: string | null; by: string | null; note: string | null } | null;
  decisions: (DecisionEntry & { actor_name: string | null })[];
  can_decide: boolean;
  next: CandidateStage[];
}

export interface Comparison {
  job: { id: string; title: string; bu_name: string | null };
  competencies: { id: string; name: string; weight_percent: number }[];
  soft_skills: string[];
  candidates: {
    id: string;
    name: string;
    stage: CandidateStage;
    score_percent: number | null;
    assessed_weight_percent: number | null;
    enough_evidence: boolean | null;
    passes_screen: boolean | null;
    ratings: Record<string, number | null>;
    hr_round: { recommendation: HRRecommendation; ratings: Record<string, number | null> } | null;
    /** HR skipped the HR round (the round is optional). */
    hr_skipped?: boolean;
    technical: { recommendation: TechnicalSignal | null } | null;
  }[];
  hidden: { closed: number; not_screened: number };
  include_closed: boolean;
  threshold_percent: number | null;
}

export interface TechnicalRound {
  number: number;
  interview_id: string;
  ended_at: string | null;
  recommendation: TechnicalSignal | null;
}

export interface TechnicalView {
  candidate: { id: string; name: string; stage: CandidateStage };
  job: { id: string; title: string; bu_name: string | null };
  /** MITRA's plan (the interview blueprint) for this candidate. */
  /** `minutes`: the plan's own length. `bookable_minutes`: what HR can book (20 to 60). */
  plan: { status: "pending" | "generating" | "ready" | "failed"; error: string | null; minutes: number; bookable_minutes: number[] };
  /** The booked round, shaped like Track I's TwoLinks plus the password (HR only). */
  booking: {
    interview_id: string;
    status: "scheduled" | "in_progress";
    meeting_id: string;
    password: string | null;
    candidate_join_url: string | null;
    live_view_url: string | null;
    duration_minutes: number;
    expires_at: string | null;
    started_at: string | null;
    booked_by: string | null;
  } | null;
  last_round: { status: string; failure_reason: string | null; feedback_status: string } | null;
  /** While booked or just done: what is happening now. */
  round: RoundState | null;
  has_report: boolean;
  sent_to_bu_head: { at: string | null; by: string | null; note: string | null } | null;
  can_book: boolean;
  /** A finished round: HR may book another, a retake of the same plan. */
  can_book_again: boolean;
  /** Every MITRA round with a report, numbered from the first. */
  rounds: TechnicalRound[];
  can_retry_plan: boolean;
  can_cancel: boolean;
  can_send: boolean;
}

// ---- Track I's FeedbackReport (shared/contracts.py), as stored ------------------------------------

export interface ReportQuote {
  text: string;
  turn_index: number;
  at_seconds: number;
  audio_start_seconds?: number | null;
}

export interface ReportScore {
  competency_id: string;
  name: string;
  score: number | null;
  coverage: string;
  rationale: string;
  evidence: ReportQuote[];
  insufficient_signal: boolean;
}

export interface FeedbackReport {
  role_title: string;
  competency_scores: ReportScore[];
  red_flags_observed?: { description: string; evidence: ReportQuote[] }[];
  strengths_observed?: { competency_id: string; description: string; evidence: ReportQuote[] }[];
  contradictions?: { description?: string; summary?: string; evidence?: ReportQuote[] }[];
  summary: string;
  recommendation: TechnicalSignal;
  coverage_gaps?: string[];
  conversation_health?: { degraded?: boolean; summary?: string; notes?: string[] } | null;
  interview_duration_seconds?: number;
}

export interface TranscriptLine {
  index: number;
  speaker: string | null;
  text: string;
  at_seconds: number | null;
}

export const HR_RECOMMENDATION: Record<HRRecommendation, { label: string; tone: "good" | "closed" | "attention" }> = {
  proceed_to_technical: { label: "Proceed to the MITRA interview round", tone: "good" },
  reject: { label: "Reject", tone: "closed" },
  discuss: { label: "Discuss: the evidence points neither way", tone: "attention" },
};

export const TECHNICAL_SIGNAL: Record<TechnicalSignal, string> = {
  strong_evidence_for: "Strong evidence for",
  some_evidence_for: "Some evidence for",
  mixed: "Mixed evidence",
  limited_evidence: "Limited evidence",
  insufficient_signal: "Not enough signal to say",
};

// ---- the live room (backend/hiring/technical.py technical_live) -----------------------------------

/** What the candidate's browser reported during the round. Facts only, never a score. */
/** What the candidate's browser reported in the MITRA round (hiring/technical.py
 * integrity_facts_for): Track I's integrity kinds (face_missing, share_stopped,
 * tab_hidden, ...) and older tab reports, each with a plain label. */
export interface IntegrityFacts {
  events: { at_seconds: number | null; seconds: number; kind: string; label: string }[];
  counts: { kind: string; label: string; count: number; total_seconds: number }[];
  total: number;
}

export type LiveState =
  | "not_booked"
  | "waiting"
  | "consented"
  | "starting"
  | "live"
  | "candidate_left"
  | "closing"
  | "scoring"
  | "done"
  | "did_not_happen";

/** Who is in the room now, from Daily (backend/hiring/presence.py). */
export interface RoomPresence {
  known: boolean;
  candidate: boolean;
  mitra: boolean;
  others: number;
}

/** Where the MITRA interview round actually stands (backend/hiring/technical.py round_state). */
export interface RoundState {
  state: LiveState;
  presence: RoomPresence | null;
  scoring_step: "saving" | "compiling" | null;
  report_failed: boolean;
  ended_at: string | null;
}

export interface TechnicalLive {
  candidate: { id: string; name: string; stage: CandidateStage };
  job: { id: string; title: string; bu_name: string | null };
  state: LiveState;
  presence: RoomPresence | null;
  scoring_step: "saving" | "compiling" | null;
  interview_id: string | null;
  started_at: string | null;
  ended_at: string | null;
  elapsed_seconds: number;
  planned_minutes: number;
  candidate_timezone: string | null;
  recording_status: string | null;
  failure_reason: string | null;
  plan: { sections: { key: string; name: string; minutes: number }[]; current: string | null };
  integrity: IntegrityFacts;
}
