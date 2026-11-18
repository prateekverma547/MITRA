import { useTip, tipProps } from "./Tooltip";

export interface AgeingRow {
  action: string;
  buckets: number[];
  median_days: number | null;
  count: number;
}

/**
 * How long each kind of waiting work has waited. One row per step, split into
 * age bands on a single blue ramp that darkens with age, so the eye goes to
 * the old work first. Rows share one scale, so lengths compare across steps.
 */
export function AgeingBars({ rows, buckets, onPick }: { rows: AgeingRow[]; buckets: string[]; onPick?: (row: AgeingRow) => void }) {
  const tip = useTip();
  const max = Math.max(1, ...rows.map((r) => r.count));
  if (!rows.length) return <p className="empty-note">Nothing is waiting on you.</p>;
  return (
    <div className="ageing">
      <ul className="legend legend-static" aria-label="Age bands">
        {buckets.map((b, i) => (
          <li key={b}>
            <span className={`swatch age-${i}`} aria-hidden="true" />
            {b}
          </li>
        ))}
      </ul>
      <ol className="ageing-rows">
        {rows.map((r) => (
          <li key={r.action}>
            <button type="button" className="ageing-row" onClick={() => onPick?.(r)}>
              <span className="ageing-label">{r.action}</span>
              <span className="ageing-track">
                <span className="ageing-bar" style={{ width: `${(r.count / max) * 100}%` }}>
                  {r.buckets.map((n, i) =>
                    n ? (
                      <span
                        key={i}
                        className={`ageing-seg age-${i}`}
                        style={{ flexGrow: n }}
                        {...tipProps(tip, `${n} waiting`, `${r.action}, ${buckets[i].toLowerCase()}`)}
                        tabIndex={-1}
                      />
                    ) : null,
                  )}
                </span>
              </span>
              <span className="ageing-count">{r.count}</span>
              <span className="ageing-median">
                {r.median_days === null ? "" : `median ${r.median_days < 1 ? "under a day" : `${Math.round(r.median_days)} d`}`}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
