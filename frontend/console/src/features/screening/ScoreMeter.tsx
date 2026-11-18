/**
 * The screening score against the cut-off, as one line: the score fills the
 * track, the cut-off is a tick. Below the cut-off the fill turns amber, and the
 * words say so too, so colour is never the only signal.
 */
export function ScoreMeter({
  score,
  threshold,
  enoughEvidence = true,
  compact = false,
}: {
  score: number;
  threshold: number;
  enoughEvidence?: boolean;
  compact?: boolean;
}) {
  const below = score < threshold || !enoughEvidence;
  const pct = Math.max(0, Math.min(100, score));
  return (
    <div className={compact ? "meter meter-compact" : "meter"}>
      <span className={below ? "meter-value below" : "meter-value"}>{score % 1 === 0 ? score : score.toFixed(1)}%</span>
      <span className="meter-track" role="img" aria-label={`${score}% against a ${threshold}% cut-off`}>
        <span className={below ? "meter-fill below" : "meter-fill"} style={{ width: `${pct}%` }} />
        <span className="meter-tick" style={{ left: `${threshold}%` }} />
      </span>
      {compact && below && <span className="meter-flag">{enoughEvidence ? "Below cut-off" : "Too little to judge"}</span>}
    </div>
  );
}
