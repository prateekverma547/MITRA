import { useEffect, useState } from "react";
import { onBusyChange } from "../../api";

/** A thin live-gradient bar along the top while anything is loading. */
export function TopProgress() {
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let timer = 0;
    // A short delay, so quick requests do not flash the bar.
    const off = onBusyChange((b) => {
      window.clearTimeout(timer);
      if (b) timer = window.setTimeout(() => setBusy(true), 120);
      else setBusy(false);
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, []);
  return <div className={busy ? "top-progress on" : "top-progress"} aria-hidden="true" />;
}
