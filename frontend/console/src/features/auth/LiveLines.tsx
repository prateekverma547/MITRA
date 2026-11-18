// The welcome page's live element: what happens inside every MITRA interview,
// one line at a time. Each line fades in, holds, and gives way to the next.
// Text only, so it reads as a statement, not as a fake video call.

import { useEffect, useState } from "react";

const LINES: { lead: string; rest: string }[] = [
  { lead: "Asks", rest: "every candidate questions built from their own CV." },
  { lead: "Listens", rest: "without interrupting, then follows up on what was said." },
  { lead: "Watches", rest: "the room for integrity, and records it for a person." },
  { lead: "Checks", rest: "every quote against the transcript before anyone reads it." },
  { lead: "Leaves", rest: "every decision to a person." },
];

const HOLD_MS = 4200;

export function LiveLines() {
  const [at, setAt] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const t = window.setTimeout(() => setAt((i) => (i + 1) % LINES.length), HOLD_MS);
    return () => window.clearTimeout(t);
  }, [at, paused]);

  return (
    <section
      className="ll"
      aria-label="What MITRA does in every interview"
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <p className="ll-kicker">
        <span className="ll-dot" aria-hidden="true" /> Inside every MITRA interview
      </p>
      <div className="ll-stack" aria-live="polite">
        {LINES.map((line, i) => (
          <p key={line.lead} className={i === at ? "ll-line on" : "ll-line"} aria-hidden={i !== at}>
            <span className="ll-lead">{line.lead}</span> {line.rest}
          </p>
        ))}
      </div>
      <div className="ll-steps" role="group" aria-label="Choose a line">
        {LINES.map((line, i) => (
          <button
            key={line.lead}
            type="button"
            className={i === at ? "ll-step on" : "ll-step"}
            aria-label={line.lead}
            aria-pressed={i === at}
            onClick={() => setAt(i)}
          >
            <span />
          </button>
        ))}
      </div>
    </section>
  );
}
