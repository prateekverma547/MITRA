import { useTip, tipProps } from "./Tooltip";

export interface PipelineGroup {
  key: string;
  label: string;
  count: number;
}

/**
 * Where everyone stands, as one tall segmented bar: the journey's six steps in
 * order, an ordinal blue ramp darkening towards a decision, closed in grey.
 * Each block is a button: it opens the directory filtered to that step.
 */
export function PipelineBar({ groups, onPick }: { groups: PipelineGroup[]; onPick?: (key: string) => void }) {
  const tip = useTip();
  const total = groups.reduce((s, g) => s + g.count, 0);
  if (total === 0) return <p className="muted">No candidates yet. They appear here as HR adds them.</p>;
  return (
    <div className="pipeline">
      <div className="seg-bar" role="list" aria-label="Candidates by step">
        {groups
          .filter((g) => g.count > 0)
          .map((g) => {
            const share = (g.count / total) * 100;
            return (
              <button
                key={g.key}
                type="button"
                role="listitem"
                className={`seg seg-${g.key}`}
                style={{ flexGrow: g.count }}
                onClick={() => onPick?.(g.key)}
                {...tipProps(tip, `${g.count} ${g.count === 1 ? "candidate" : "candidates"}`, `${g.label}, ${Math.round(share)}%`)}
              >
                {share >= 7 && <span className="seg-value">{g.count}</span>}
              </button>
            );
          })}
      </div>
      <ul className="legend">
        {groups.map((g) => (
          <li key={g.key}>
            <button type="button" className="legend-item" onClick={() => onPick?.(g.key)}>
              <span className={`swatch seg-${g.key}`} aria-hidden="true" />
              {g.label}
              <span className="legend-count">{g.count}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A thin version of the same bar, for rows of a list. No text: the row carries the figures. */
export function ThinPipeline({ groups, label }: { groups: Record<string, number>; label: string }) {
  const tip = useTip();
  const order = ["screening", "hr", "technical", "bu_head", "shortlisted", "closed"];
  const total = order.reduce((s, k) => s + (groups[k] ?? 0), 0);
  if (!total) return <span className="thin-empty" aria-label={`${label}: no candidates`} />;
  return (
    <span className="thin-bar" role="img" aria-label={label}>
      {order
        .filter((k) => groups[k])
        .map((k) => (
          <span key={k} className={`thin-seg seg-${k}`} style={{ flexGrow: groups[k] }} {...tipProps(tip, `${groups[k]}`, k)} tabIndex={-1} />
        ))}
    </span>
  );
}
