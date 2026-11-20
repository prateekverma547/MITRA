// How the interview ended, in neutral words. Ported from Track I's
// frontend/ui/tests/endings.test.tsx (the wording checks, verbatim); their report
// rendering checks are left with their report. The reasons are read from the
// backend's own contract (shared/interview_contracts.py, EndReason), not from the
// map under test, so a reason added there fails here until it has words.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { END_REASON_WORDS, ENDING_WORDS, interviewEnding, UNKNOWN_ENDING } from "../src/features/results/endingWords";

const contract = readFileSync(`${process.cwd()}/../../backend/shared/interview_contracts.py`, "utf8");
const endReasonClass = contract.match(/class EndReason\(StrEnum\):([\s\S]*?)\n\n\n/)?.[1] ?? "";
const REASONS = [...endReasonClass.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);

// The connection records Track I's fixtures use (frontend/ui/fixtures, connection).
const connections = {
  noReturn: { schema_version: 1 as const, drops: [], rejoin_count: 0, rejoin_limit: 3, grace_seconds: 90, limit_reached: false },
  limit: { schema_version: 1 as const, drops: [], rejoin_count: 3, rejoin_limit: 3, grace_seconds: 90, limit_reached: true },
};

// The brief's words, plus the backend's own banned list (decision 12).
const source = readFileSync(`${process.cwd()}/../../backend/shared/interview_contracts.py`, "utf8");
const BANNED_WORDS = new Set([
  ...[...(source.match(/_NOT_NEUTRAL = \{([^}]*)\}/s)?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]),
  "abandoned", "abandon", "abandons", "failed", "fail", "fails", "failure", "quit", "quits", "blame", "fault",
]);
const BANNED_PHRASES = ["gave up", "give up", "walked out", "refused to"];
const blaming = (text: string) => [
  ...text
    .split(/\s+/)
    .map((w) => w.replace(/^[.,;:!?'"()“”]+|[.,;:!?'"()“”]+$/g, "").toLowerCase())
    .filter((w) => BANNED_WORDS.has(w)),
  ...BANNED_PHRASES.filter((p) => text.toLowerCase().includes(p)),
];
/** A raw code on the page: any end reason or ending value, or any word with an underscore. */
const rawCode = (text: string) => [...REASONS, ...Object.keys(ENDING_WORDS)].filter((c) => text.includes(c)).concat(/\w_\w/.test(text) ? ["_"] : []);

describe("the sentence for every end reason", () => {
  it("every reason in the contract has words here (and the map has no extra ones)", () => {
    expect(Object.keys(END_REASON_WORDS).sort()).toEqual([...REASONS].sort());
  });

  it.each([
    ["interview_complete", "The interview was completed."],
    ["candidate_withdrew", "The candidate chose to end the interview early."],
    ["candidate_ended_button", "The candidate ended the interview using the End button."],
    ["rejoin_limit_reached", "The connection kept dropping, so the interview ended once the rejoin limit was passed."],
    ["candidate_did_not_return", "The connection dropped and the candidate did not return within the time allowed."],
    ["abandoned_by_candidate", "No speech was heard from the candidate for about two minutes, so the session was closed."],
    ["interrupted_by_restart", "The interview was interrupted by a server restart, a problem on our side."],
    ["failed_to_start", "The interview could not start because of a problem on our side."],
    ["candidate_dropped", "The candidate's connection dropped, and the interview ended at that point."],
    ["candidate_left_the_call", "The candidate was not in the call for about two minutes, so the session was closed."],
    ["session_ended", "The session ended before the interview was complete; no specific reason was recorded."],
  ])("%s", (reason, sentence) => {
    expect(interviewEnding({ endedBecause: reason }).sentence).toBe(sentence);
  });

  it("the time limit (forced_at_hard_stop) and the time limit during a drop", () => {
    expect(interviewEnding({ endedBecause: "interview_complete", ending: "forced_at_hard_stop" }).sentence).toBe(
      "The interview reached its time limit, so it ended there.",
    );
    const drop = interviewEnding({ endedBecause: "interview_complete", ending: "hard_stop_during_drop" });
    expect(drop.sentence).toBe("The interview reached its time limit while the candidate's connection was down.");
    expect(drop.link).toBe("connection");
    // An ending that changes nothing (how the goodbye went) leaves the reason's sentence.
    expect(interviewEnding({ endedBecause: "interview_complete", ending: "completed" }).sentence).toBe("The interview was completed.");
  });

  it("a withdrawal Mitra confirmed through the tool says so; without the tool's record it does not claim it", () => {
    expect(interviewEnding({ endedBecause: "candidate_withdrew", ending: "withdrew" }).sentence).toBe(
      "The candidate chose to end the interview early; Mitra confirmed with them before ending.",
    );
    expect(interviewEnding({ endedBecause: "candidate_withdrew", ending: null }).sentence).not.toContain("confirmed");
  });

  it("connection endings use the record's own numbers (Session A's real-cut records)", () => {
    expect(interviewEnding({ endedBecause: "candidate_did_not_return", connection: connections.noReturn }).sentence).toBe(
      "The connection dropped and the candidate did not return within 90 seconds.",
    );
    expect(interviewEnding({ endedBecause: "rejoin_limit_reached", connection: connections.limit }).sentence).toBe(
      "The connection kept dropping, so the interview ended once the rejoin limit (3) was passed.",
    );
  });

  it.each([
    ["unknown", "something_new"],
    ["missing (undefined)", undefined],
    ["missing (null)", null],
    ["empty", ""],
    ["a number", 42],
    ["an object", { reason: "x" }],
    ["a prototype key", "toString"],
  ])("%s reason: 'reason not recorded', never a crash, never a raw code", (_, reason) => {
    const e = interviewEnding({ endedBecause: reason, ending: "another_way" });
    expect(e).toEqual({ sentence: UNKNOWN_ENDING, reason: null, link: null });
  });

  it("no sentence blames the candidate or shows a raw code (and the scans find a planted one)", () => {
    expect(blaming("The candidate abandoned the interview")).toEqual(["abandoned"]);
    expect(blaming("They gave up")).toEqual(["gave up"]);
    expect(rawCode("ended: abandoned_by_candidate")).not.toEqual([]);
    const all = [...Object.values(END_REASON_WORDS), ...Object.values(ENDING_WORDS), UNKNOWN_ENDING];
    for (const s of all) {
      expect(blaming(s)).toEqual([]);
      expect(rawCode(s)).toEqual([]);
    }
  });
});

