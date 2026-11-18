import { useTip, tipProps } from "./Tooltip";

export interface StripRow {
  id: string;
  title: string;
  threshold: number | null;
  scores: { candidate_id: string; name: string; score: number; enough_evidence: boolean; group: string }[];
}

/**
 * How strong the field is for each job: every screened candidate as a dot on
 * one 0–100% scale, the cut-off as a rule. Closed candidates are hollow, so
 * the live field reads first. Each dot opens the candidate.
 */
export function ScoreStrip({ rows, onPick }: { rows: StripRow[]; onPick?: (candidateId: string) => void }) {
  const tip = useTip();
  const withScores = rows.filter((r) => r.scores.length);
  if (!withScores.length) return <p className="empty-note">No screened candidates yet. Scores appear as HR screens CVs.</p>;
  return (
    <div className="strip">
      <div className="strip-axis" aria-hidden="true">
        <span />
        <span className="strip-ticks">
          {[0, 25, 50, 75, 100].map((t) => (
            <span key={t} style={{ left: `${t}%` }}>
              {t}%
            </span>
          ))}
        </span>
      </div>
      <ol className="strip-rows">
        {withScores.map((r) => (
          <li key={r.id} className="strip-row">
            <span className="strip-title">{r.title}</span>
            <span className="strip-track" role="list" aria-label={`${r.title}: ${r.scores.length} screened`}>
              {r.threshold !== null && (
                <span className="strip-cut" style={{ left: `${r.threshold}%` }} aria-hidden="true" />
              )}
              {r.scores.map((s) => (
                <button
                  key={s.candidate_id}
                  type="button"
                  role="listitem"
                  className={`strip-dot${s.group === "closed" ? " closed" : ""}${s.group === "shortlisted" ? " chosen" : ""}`}
                  style={{ left: `${Math.max(0, Math.min(100, s.score))}%` }}
                  onClick={() => onPick?.(s.candidate_id)}
                  {...tipProps(
                    tip,
                    `${Math.round(s.score)}%`,
                    `${s.name}${s.enough_evidence ? "" : ", too little evidence"}${s.group === "closed" ? ", closed" : ""}`,
                  )}
                />
              ))}
            </span>
          </li>
        ))}
      </ol>
      <ul className="legend legend-static">
        <li>
          <span className="swatch dot-key" aria-hidden="true" /> In progress
        </li>
        <li>
          <span className="swatch dot-key chosen" aria-hidden="true" /> Shortlisted
        </li>
        <li>
          <span className="swatch dot-key closed" aria-hidden="true" /> Closed
        </li>
        <li>
          <span className="swatch cut-key" aria-hidden="true" /> Cut-off
        </li>
      </ul>
    </div>
  );
}
