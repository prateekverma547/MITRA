// Track H. What the org admin's screens read from /api/admin. Kept beside the
// feature rather than in contracts.ts: nothing else in the console uses them.

import type { CandidateStage, Role } from "../../contracts";

export interface AdminUser {
  id: string;
  name: string;
  email: string;
  role: Role;
  bu_id: string | null;
  bu_name: string | null;
  active: boolean;
  last_sign_in_at: string | null;
  created_at: string | null;
  is_you: boolean;
}

export interface UserList {
  users: AdminUser[];
  counts: { total: number; active: number; by_role: Record<Role, number> };
}

export interface AdminBU {
  id: string;
  name: string;
  heads: number;
  head_names: string[];
  open_jobs: number;
  candidates: number;
  created_at: string | null;
}

export interface ConsentRecord {
  interview_id: string;
  candidate_id: string;
  candidate: string | null;
  job_title: string | null;
  round: "hr" | "technical";
  status: string;
  booked_at: string | null;
  consent_accepted_at: string | null;
}

export interface ConsentList {
  total: number;
  page: number;
  page_size: number;
  summary: { consented: number; missing: number };
  items: ConsentRecord[];
}

export type RetentionKey = "cv" | "recording" | "transcript";

export interface Retention {
  closed_candidates: number;
  categories: { key: RetentionKey; label: string; covers: string; days: number; due: number }[];
}

export interface DeletionRequest {
  id: string;
  candidate_id: string | null;
  candidate_name: string;
  contact_note: string | null;
  received_at: string | null;
  received_by: string | null;
  status: "open" | "completed" | "refused";
  resolution: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface CandidateHit {
  id: string;
  name: string;
  job_title: string | null;
  stage: CandidateStage;
}

export function day(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function daysSince(iso: string | null): number {
  if (!iso) return 0;
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000));
}
