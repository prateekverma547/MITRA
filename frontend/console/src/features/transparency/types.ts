// Shapes read from backend/hiring/transparency.py. Local to this feature.

import type { CandidateStage, Role } from "../../contracts";

export type Outcome = "followed" | "differed" | "not_comparable" | "no_recommendation" | "mitra";

export interface DecisionRow {
  id: number;
  at: string | null;
  candidate: { id: string; name: string };
  job: { id: string; title: string };
  bu_name: string;
  from: CandidateStage;
  to: CandidateStage;
  actor: { kind: "mitra" | "person"; role: string; name: string };
  reason: string | null;
  model_recommendation: string | null;
  outcome: Outcome;
}

export interface DecisionLog {
  total: number;
  page: number;
  page_size: number;
  items: DecisionRow[];
  summary: {
    by_people: number;
    by_mitra: number;
    followed: number;
    differed: number;
    not_comparable: number;
    no_recommendation: number;
    differed_percent: number | null;
  };
  business_units: { id: string; name: string }[];
}

export interface RecordMatch {
  turn: number | null;
  speaker: string;
  at_seconds: number | null;
  text: string;
}

export interface RecordItem {
  kind: "hr_round" | "screening" | "recording";
  id: string;
  at: string | null;
  candidate: { id: string; name: string };
  job: { id: string; title: string };
  bu_name: string;
  open: string;
  matches: RecordMatch[];
  more_matches: number;
  // hr_round
  turns?: number;
  eval_status?: string;
  recommendation?: "proceed_to_technical" | "reject" | "discuss" | null;
  summary?: string | null;
  attribution?: "clear" | "likely" | "uncertain" | null;
  // screening
  score_percent?: number | null;
  assessed_weight_percent?: number | null;
  // recording
  round?: "hr" | "technical";
  play?: string;
}

export interface Records {
  total: number;
  page: number;
  page_size: number;
  items: RecordItem[];
  counts: Record<"hr_round" | "screening" | "recording", number>;
}

export type Purpose = "cv_fit" | "hr_questions" | "hr_evaluation" | "job_understanding";

export interface RegisterGroup {
  purpose: Purpose;
  purpose_label: string;
  model: string;
  prompt_version: string;
  count: number;
  first_used: string | null;
  last_used: string | null;
}

export interface Use {
  purpose: Purpose;
  purpose_label: string;
  model: string;
  prompt_version: string;
  at: string | null;
  subject: string;
  candidate: { id: string; name: string } | null;
  job: { id: string; title: string } | null;
  bu_name: string | null;
}

export interface Register {
  register: RegisterGroup[];
  total: number;
  page: number;
  page_size: number;
  items: Use[];
  gaps: string[];
}

export interface AuditRow {
  id: number;
  at: string | null;
  action: string;
  label: string;
  actor: { name: string; role: Role; email: string } | null;
  subject_type: string | null;
  subject_id: string | null;
  detail: Record<string, unknown> | null;
}

export interface Audit {
  total: number;
  page: number;
  page_size: number;
  items: AuditRow[];
  actions: { key: string; label: string }[];
}

// ---- AI calls and cost (backend/hiring/usage.py) ---------------------------------------------

export interface ModelCallRow {
  id: string;
  at: string | null;
  stage: string;
  stage_label: string;
  purpose: string;
  model: string;
  prompt_version: string | null;
  status: string;
  latency_ms: number | null;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number | null;
  audio_seconds: number | null;
  cost_usd: number | null;
  price_known: boolean;
  candidate: { id: string; name: string | null } | null;
  job: { id: string; title: string | null } | null;
  interview_id: string | null;
}

export interface ModelCallList {
  total: number;
  page: number;
  page_size: number;
  items: ModelCallRow[];
  facets: { stages: { key: string; label: string }[]; models: string[] };
  summary: { calls: number; cost_usd: number; unpriced: number; errors: number };
}

export interface ModelCallDetail extends ModelCallRow {
  error: string | null;
  summary: Record<string, string | number | boolean | null>;
  output_preview: string | null;
  price: Record<string, number> | null;
  price_source: string;
  cost_note: string | null;
}

export interface Costs {
  days: number;
  currency: string;
  price_source: string;
  totals: {
    cost_usd: number;
    calls: number;
    unpriced: number;
    candidates: number;
    hires: number;
    per_candidate_usd: number | null;
    per_hire_usd: number | null;
  };
  by_stage: { key: string; label: string; cost_usd: number; calls: number; unpriced: number; not_measured: string | null }[];
  per_candidate_by_stage: { key: string; label: string; cost_usd: number | null }[];
  by_model: { model: string; cost_usd: number; calls: number; priced: boolean }[];
  by_bu: { name: string; cost_usd: number }[];
  by_job: { id: string; title: string; cost_usd: number }[];
  weeks: string[];
  trend_usd: number[];
  not_measured: { key: string; label: string; why: string }[];
}
