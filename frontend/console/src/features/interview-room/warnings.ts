// The MITRA room's warnings (product owner, 2026-11-20): every kind of problem the
// room can see gets a warning while it lasts, every time, with how many times it
// has come up. This supersedes "at most three reminders per kind" for what the
// candidate sees; what is recorded for the report is unchanged (Track I's engine,
// useIntegrity.ts).

import { useEffect, useRef, useState } from "react";

export interface Warning {
  on: boolean;
  /** How many times this warning has come up in this call, the current one included. */
  count: number;
}

/**
 * On once `condition` has held for `afterMs` (so a blink of a detector does not
 * flash it), and kept on for `holdMs` after it clears (so a flickering detector
 * does not flash it off). Coming back within the hold is the same time, not a new one.
 */
export function useWarning(condition: boolean, afterMs: number, holdMs = 0): Warning {
  const [on, setOn] = useState(false);
  const [count, setCount] = useState(0);
  const showing = useRef(false);
  useEffect(() => {
    if (condition) {
      if (showing.current) return; // the same time, still going
      const t = window.setTimeout(() => {
        showing.current = true;
        setOn(true);
        setCount((c) => c + 1);
      }, afterMs);
      return () => window.clearTimeout(t);
    }
    if (!showing.current) return;
    const t = window.setTimeout(() => {
      showing.current = false;
      setOn(false);
    }, holdMs);
    return () => window.clearTimeout(t);
  }, [condition, afterMs, holdMs]);
  return { on, count };
}

/** On while `episode` is set; each new value is one more time (leaving the tab, seen on return). */
export function useEpisodeWarning(episode: number | null): Warning {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (episode !== null) setCount((c) => c + 1);
  }, [episode]);
  return { on: episode !== null, count };
}

/** "1st time", "2nd time", "11th time". */
export function timesLabel(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : n % 10 === 1 ? "st" : n % 10 === 2 ? "nd" : n % 10 === 3 ? "rd" : "th";
  return `${n}${suffix} time`;
}
