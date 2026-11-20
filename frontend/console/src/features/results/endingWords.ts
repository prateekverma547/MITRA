import type { ConnectionRecord, EndReason } from "../../contracts";
import { BOT_NAME } from "../../branding";

/**
 * How an interview ended, in one plain sentence for HR (the report and Check
 * Intelligence). Neutral, and never blaming the candidate: no "abandoned", "failed",
 * "quit" or "gave up". Keyed by the recorded `ended_because` (EndReason, the
 * contract's values, C-146), refined by the coordinator's `ending` where it says more
 * (the time limit). Each sentence says what the code actually does for that reason
 * (B_findings B-106 has the table). An unknown or missing reason never shows its raw
 * code and never crashes: "The interview ended (reason not recorded)."
 *
 * Typed as Record<EndReason, …>, so a reason added to the contract fails the type
 * check here until it has words.
 */
export const END_REASON_WORDS: Record<EndReason, string> = {
  interview_complete: "The interview was completed.",
  candidate_withdrew: "The candidate chose to end the interview early.",
  candidate_ended_button: "The candidate ended the interview using the End button.",
  // Pipeline engine only: its bot ends the session when the last candidate connection leaves.
  candidate_dropped: "The candidate's connection dropped, and the interview ended at that point.",
  // The silence ladder's close (bot/silence.py, close_at_seconds 120 by default).
  abandoned_by_candidate: "No speech was heard from the candidate for about two minutes, so the session was closed.",
  // Pipeline engine only: the room stayed empty through the silence ladder's close.
  candidate_left_the_call: "The candidate was not in the call for about two minutes, so the session was closed.",
  // Pipeline engine only: neither finished nor withdrawn; also what an unrecognised reason becomes.
  session_ended: "The session ended before the interview was complete; no specific reason was recorded.",
  failed_to_start: "The interview could not start because of a problem on our side.",
  candidate_did_not_return: "The connection dropped and the candidate did not return within the time allowed.",
  rejoin_limit_reached: "The connection kept dropping, so the interview ended once the rejoin limit was passed.",
  interrupted_by_restart: "The interview was interrupted by a server restart, a problem on our side.",
};

/** The coordinator's `ending` values that change the sentence (bot/realtime_coordinator.py ENDING_*). */
export const ENDING_WORDS: Record<string, string> = {
  forced_at_hard_stop: "The interview reached its time limit, so it ended there.",
  hard_stop_during_drop: "The interview reached its time limit while the candidate's connection was down.",
  withdrew: `The candidate chose to end the interview early; ${BOT_NAME} confirmed with them before ending.`,
};

export const UNKNOWN_ENDING = "The interview ended (reason not recorded).";

/** Which part of the report the sentence points to, when there is one. */
export type EndingLink = "connection" | "withdrawal" | null;

export interface InterviewEnding {
  sentence: string;
  /** The recorded reason when it is one the contract knows; null otherwise. */
  reason: EndReason | null;
  link: EndingLink;
}

const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const known = (r: unknown): r is EndReason => typeof r === "string" && Object.prototype.hasOwnProperty.call(END_REASON_WORDS, r);
const CONNECTION: ReadonlySet<string> = new Set(["candidate_dropped", "candidate_did_not_return", "rejoin_limit_reached"]);

export function interviewEnding({
  endedBecause,
  ending,
  connection,
}: {
  endedBecause: unknown;
  ending?: unknown;
  connection?: ConnectionRecord | null;
}): InterviewEnding {
  if (!known(endedBecause)) return { sentence: UNKNOWN_ENDING, reason: null, link: null };
  const reason = endedBecause;
  const how = typeof ending === "string" ? ending : null;
  if (reason === "interview_complete" && how === "forced_at_hard_stop") return { sentence: ENDING_WORDS.forced_at_hard_stop, reason, link: null };
  if (reason === "interview_complete" && how === "hard_stop_during_drop")
    return { sentence: ENDING_WORDS.hard_stop_during_drop, reason, link: "connection" };
  if (reason === "candidate_withdrew") return { sentence: how === "withdrew" ? ENDING_WORDS.withdrew : END_REASON_WORDS.candidate_withdrew, reason, link: "withdrawal" };
  // The connection's own numbers, when the record has them.
  const grace = finite(connection?.grace_seconds) ? Math.round(connection!.grace_seconds) : null;
  const limit = finite(connection?.rejoin_limit) ? connection!.rejoin_limit : null;
  if (reason === "candidate_did_not_return" && grace !== null)
    return { sentence: `The connection dropped and the candidate did not return within ${grace} seconds.`, reason, link: "connection" };
  if (reason === "rejoin_limit_reached" && limit !== null)
    return { sentence: `The connection kept dropping, so the interview ended once the rejoin limit (${limit}) was passed.`, reason, link: "connection" };
  return { sentence: END_REASON_WORDS[reason], reason, link: CONNECTION.has(reason) ? "connection" : null };
}
