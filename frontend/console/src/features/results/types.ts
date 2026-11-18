// Track H. Types for the results page, the comparison and the technical
// booking (backend/hiring/results.py and technical.py). Local to this feature
// so the shared contracts file is not edited while other work is in flight.

import type { CandidateStage, DecisionEntry, HRRoundEvaluation } from "../../contracts";

export type HRRecommendation = HRRoundEvaluation["recommendation"];

/** shared.contracts.RecommendationSignal: the technical report's signal. */
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
    report: {
      recommendation: TechnicalSignal | null;
      summary: string | null;
      scores: { name: string; score: number | null; insufficient_signal: boolean }[];
      coverage_gaps: string[];
    } | null;
    interview_id: string | null;
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
    technical: { recommendation: TechnicalSignal | null } | null;
  }[];
  hidden: { closed: number; not_screened: number };
  include_closed: boolean;
  threshold_percent: number | null;
}

export interface TechnicalView {
  candidate: { id: string; name: string; stage: CandidateStage };
  job: { id: string; title: string; bu_name: string | null };
  duration: { default: number; min: number; max: number };
  booking: {
    candidate_link: string;
    live_view_link: string;
    stub: boolean;
    duration_minutes: number;
    booked_at: string | null;
    booked_by: string | null;
  } | null;
  has_report: boolean;
  sent_to_bu_head: { at: string | null; by: string | null; note: string | null } | null;
  can_book: boolean;
  can_cancel: boolean;
  can_send: boolean;
}

export const HR_RECOMMENDATION: Record<HRRecommendation, { label: string; tone: "good" | "closed" | "attention" }> = {
  proceed_to_technical: { label: "Proceed to the technical round", tone: "good" },
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
