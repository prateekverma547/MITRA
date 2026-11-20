// Where the MITRA interview round actually stands, in words and in steps. Used on the
// candidate page and in the live room, so both say the same thing. The states
// come from backend/hiring/technical.py live_state, which reads Daily's
// presence: an interview the candidate has left no longer says "in progress".

import { CheckCircle2 } from "lucide-react";
import { Spinner } from "../../shell/ui/Spinner";
import type { LiveState } from "./types";

export const ROUND_STATE: Record<LiveState, { label: string; line: string; tone: "live" | "wait" | "work" | "good" | "bad" }> = {
  not_booked: { label: "Not booked", line: "No MITRA interview round is booked for this candidate.", tone: "wait" },
  waiting: { label: "Waiting for the candidate", line: "The room is open. MITRA joins the moment the candidate does.", tone: "wait" },
  consented: { label: "Consent given", line: "The candidate accepted the recording and proctoring notice and is getting ready.", tone: "wait" },
  starting: { label: "Starting", line: "The candidate has joined and MITRA is entering the room.", tone: "live" },
  live: { label: "Live with MITRA", line: "The candidate is in the interview with MITRA now.", tone: "live" },
  candidate_left: {
    label: "Candidate left the call",
    line: "MITRA is waiting about two minutes in case they rejoin, then closes the interview and compiles the report.",
    tone: "work",
  },
  closing: { label: "Interview ended", line: "Everyone has left. MITRA is closing the session and saving the transcript.", tone: "work" },
  scoring: { label: "Compiling results", line: "MITRA is scoring the interview against the plan and checking every quote.", tone: "work" },
  done: { label: "Report ready", line: "The interview is over and the report is ready to read.", tone: "good" },
  did_not_happen: { label: "Did not take place", line: "The round did not happen. HR can book it again from the candidate page.", tone: "bad" },
};

const STEPS = [
  { key: "interview", label: "The interview" },
  { key: "saving", label: "Closing and saving the interview" },
  { key: "compiling", label: "Compiling the report" },
  { key: "ready", label: "Report ready" },
] as const;

/** Which step is under way: 0 interview, 1 saving, 2 compiling, 3 ready. */
function stepOf(state: LiveState, scoringStep: "saving" | "compiling" | null): number {
  if (state === "done") return 3;
  if (state === "scoring") return scoringStep === "compiling" ? 2 : 1;
  if (state === "closing") return 1;
  return 0;
}

const NOTES: Partial<Record<LiveState, string>> = {
  starting: "The candidate is in; MITRA opens with a short hello.",
  live: "The candidate is talking with MITRA.",
  candidate_left: "The candidate left. MITRA waits about two minutes for them to rejoin.",
  closing: "Writing the transcript to the record.",
};

export function RoundSteps({
  state,
  scoringStep,
  reportFailed,
  horizontal = false,
}: {
  state: LiveState;
  scoringStep: "saving" | "compiling" | null;
  reportFailed?: boolean;
  /** Across the page, for the candidate page's "What happens now". */
  horizontal?: boolean;
}) {
  if (!["starting", "live", "candidate_left", "closing", "scoring", "done"].includes(state)) return null;
  const at = stepOf(state, scoringStep);
  return (
    <ol className={`progress-steps round-steps${horizontal ? " horizontal" : ""}`} aria-live="polite">
      {STEPS.map((step, i) => {
        const done = i < at || (i === 3 && at === 3);
        const active = i === at && at !== 3;
        const note =
          i === 0 && active
            ? NOTES[state]
            : i === 1 && active
              ? NOTES.closing
              : i === 2 && active
                ? "Scoring every answer against the plan and checking each quote against the transcript."
                : i === 3 && done && reportFailed
                  ? "MITRA could not score this interview. The transcript is kept; open the results to read it."
                  : undefined;
        return (
          <li key={step.key} className={done ? "done" : active ? "active" : ""}>
            <span className="progress-mark">
              {done ? <CheckCircle2 size={18} /> : active ? <Spinner size={18} /> : <span className="progress-dot" />}
            </span>
            <span>
              <span className="progress-label">{step.label}</span>
              {note && <span className="progress-note">{note}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
