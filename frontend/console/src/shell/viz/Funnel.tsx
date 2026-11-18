import { useTip, tipProps } from "./Tooltip";

/**
 * How far candidates get: everyone who reached each step, on one scale, in the
 * journey's ramp. Between steps, the share who went on. Closed candidates
 * count at the furthest step they reached, so a drop is a real drop.
 */
export function Funnel({ steps }: { steps: { key: string; label: string; reached: number }[] }) {
  const tip = useTip();
  const top = Math.max(1, steps[0]?.reached ?? 0);
  if (!steps[0]?.reached) return <p className="empty-note">No candidates in this period.</p>;
  return (
    <ol className="funnel">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].reached : null;
        const share = prev ? Math.round((s.reached / prev) * 100) : null;
        return (
          <li key={s.key} className="funnel-step">
            {share !== null && <span className="funnel-share">{prev ? `${share}% went on` : ""}</span>}
            <span className="funnel-row">
              <span className="funnel-label">{s.label}</span>
              <span className="funnel-track">
                <span
                  className={`funnel-bar seg-${s.key}`}
                  style={{ width: `${Math.max(s.reached ? 1.5 : 0, (s.reached / top) * 100)}%` }}
                  {...tipProps(tip, `${s.reached}`, `reached ${s.label.toLowerCase()}`)}
                />
              </span>
              <span className="funnel-count">{s.reached}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Median days in each step. One hue: this is a magnitude, not an identity. */
export function StepTime({ steps }: { steps: { key: string; label: string; median_days: number | null; count: number }[] }) {
  const tip = useTip();
  const max = Math.max(1, ...steps.map((s) => s.median_days ?? 0));
  if (!steps.some((s) => s.count)) return <p className="empty-note">No step has been finished yet in this period.</p>;
  return (
    <ol className="steptime">
      {steps.map((s) => (
        <li key={s.key} className="funnel-row">
          <span className="funnel-label">{s.label}</span>
          <span className="funnel-track">
            {s.median_days !== null && (
              <span
                className="steptime-bar"
                style={{ width: `${Math.max(1.5, (s.median_days / max) * 100)}%` }}
                {...tipProps(tip, `${s.median_days.toFixed(1)} days`, `median in ${s.label.toLowerCase()}, over ${s.count}`)}
              />
            )}
          </span>
          <span className="funnel-count">
            {s.median_days === null ? "None yet" : `${s.median_days < 10 ? s.median_days.toFixed(1) : Math.round(s.median_days)} d`}
          </span>
        </li>
      ))}
    </ol>
  );
}
