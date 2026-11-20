import type { IntegrityKind, ReminderState } from "../../../contracts";
import { REMINDER_COOLDOWN_S, REMINDERS_PER_KIND } from "./thresholds";

/**
 * The reminder policy (task 16, decision 10). Decides, per event, whether the
 * candidate sees an on-screen reminder:
 *
 * - at most 3 per kind, and at least 2 minutes between two of the same kind;
 *   after that the kind is recorded silently;
 * - leaving full screen, paste and a second monitor are logged only;
 * - share stopped or not the whole screen is a technical prompt: always shown,
 *   never counted against a cap, recorded as "technical_prompt" (see TECHNICAL);
 * - with the accommodation chosen at consent, gaze is never nudged, but is
 *   still recorded.
 */

/** Never shown to the candidate (DEV_PLAN section 7: "Logged only", "logged if it changes"). */
export const LOG_ONLY: ReadonlySet<IntegrityKind> = new Set(["left_full_screen", "paste", "second_monitor"]);

/**
 * Shown every time as a technical prompt ("Share your whole screen to carry
 * on"), not as an integrity reminder. Recorded with reminder
 * "technical_prompt" and no reminder_index (the contract state Session C added,
 * C-65); the report counts these on their own line, never with reminders.
 */
export const TECHNICAL: ReadonlySet<IntegrityKind> = new Set(["share_stopped", "share_not_whole_screen", "face_missing"]);
// Track H, 2026-11-20 (product owner): face_missing joined TECHNICAL. The room shows a
// warning for as long as the face is out of view, every time, not three times
// (Room.tsx); the event is recorded as a technical prompt, as the contract allows
// (shared/interview_contracts.py TECHNICAL_PROMPT_KINDS).

export interface ReminderDecision {
  reminder: ReminderState;
  /** 1 to 3 when nudged, null otherwise. */
  reminder_index: number | null;
  /** True when the technical share prompt is shown (uncapped). */
  technicalPrompt: boolean;
  /** Why it was not nudged, for the dev view and tests. */
  why: "nudged" | "log_only" | "technical" | "accommodation" | "cap" | "cooldown";
}

export class ReminderPolicy {
  private shown = new Map<IntegrityKind, number>();
  private lastAt = new Map<IntegrityKind, number>();

  constructor(private readonly opts: { gazeRemindersOff: boolean }) {}

  /** Decide for one confirmed event at interview time `t` (seconds). Records the reminder if nudged. */
  decide(kind: IntegrityKind, t: number): ReminderDecision {
    if (LOG_ONLY.has(kind)) return { reminder: "recorded_only", reminder_index: null, technicalPrompt: false, why: "log_only" };
    if (TECHNICAL.has(kind)) return { reminder: "technical_prompt", reminder_index: null, technicalPrompt: true, why: "technical" };
    if (kind === "looking_away" && this.opts.gazeRemindersOff)
      return { reminder: "recorded_only", reminder_index: null, technicalPrompt: false, why: "accommodation" };
    const count = this.shown.get(kind) ?? 0;
    if (count >= REMINDERS_PER_KIND) return { reminder: "recorded_only", reminder_index: null, technicalPrompt: false, why: "cap" };
    const last = this.lastAt.get(kind);
    if (last !== undefined && t - last < REMINDER_COOLDOWN_S)
      return { reminder: "recorded_only", reminder_index: null, technicalPrompt: false, why: "cooldown" };
    this.shown.set(kind, count + 1);
    this.lastAt.set(kind, t);
    return { reminder: "nudged", reminder_index: count + 1, technicalPrompt: false, why: "nudged" };
  }

  shownCount(kind: IntegrityKind): number {
    return this.shown.get(kind) ?? 0;
  }
}

/**
 * The words the candidate sees (DEV_PLAN section 7). Neutral: no accusation,
 * no mention of scores or consequences.
 */
export function reminderMessage(kind: IntegrityKind, direction?: "near" | "far"): string | null {
  switch (kind) {
    case "distance_out_of_band":
      return direction === "near" ? "Please sit back a little" : "Please come a little closer";
    case "looking_away":
      return "Please keep your eyes on the screen";
    case "face_missing":
      return "We can't see you clearly";
    case "second_face":
      return "Please make sure only you are in view.";
    case "tab_unfocused":
      return "Please return to the interview tab";
    case "tab_hidden":
      // Shown when the tab is visible again: a toast on a hidden tab is never
      // seen, and "return to" makes no sense once they have. A deliberate
      // deviation from DEV_PLAN's wording, reported as a contradiction.
      return "Please stay on the interview tab";
    default:
      return null;
  }
}
