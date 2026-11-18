import { useTip, tipProps } from "./Tooltip";

/**
 * Screening scores in tens. Columns at or above the cut-off carry the accent,
 * those below are de-emphasised, and the cut-off itself is a labelled rule.
 */
export function ScoreHistogram({ bins, threshold }: { bins: number[]; threshold: number | null }) {
  const tip = useTip();
  const max = Math.max(1, ...bins);
  const W = 520;
  const H = 180;
  const pad = { l: 28, r: 8, t: 18, b: 26 };
  const band = (W - pad.l - pad.r) / bins.length;
  const bw = Math.min(24, band - 6);
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const cut = threshold ?? 50;
  const cutX = pad.l + (cut / 100) * (W - pad.l - pad.r);
  const ticks = max <= 4 ? Array.from({ length: max + 1 }, (_, i) => i) : [0, Math.round(max / 2), max];
  return (
    <svg className="hist" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Screening scores by band">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="grid" />
          <text x={pad.l - 8} y={y(t) + 4} className="axis" textAnchor="end">
            {t}
          </text>
        </g>
      ))}
      {bins.map((n, i) => {
        const x = pad.l + i * band + (band - bw) / 2;
        const top = y(n);
        const h = H - pad.b - top;
        const above = i * 10 >= cut;
        const d = h > 0 ? `M${x},${H - pad.b} V${top + 4} q0,-4 4,-4 h${bw - 8} q4,0 4,4 V${H - pad.b} Z` : "";
        return (
          <g key={i}>
            {h > 0 && <path d={d} className={above ? "col col-on" : "col col-off"} />}
            <rect
              x={pad.l + i * band}
              y={pad.t}
              width={band}
              height={H - pad.t - pad.b}
              className="hit"
              {...tipProps(tip, `${n} ${n === 1 ? "candidate" : "candidates"}`, `${i * 10}–${i * 10 + 9}%`)}
            />
            {(i % 2 === 0 || i === bins.length - 1) && (
              <text x={pad.l + i * band + band / 2} y={H - 8} className="axis" textAnchor="middle">
                {i * 10}
              </text>
            )}
          </g>
        );
      })}
      <line x1={cutX} x2={cutX} y1={pad.t - 6} y2={H - pad.b} className="cutoff" />
      <text x={cutX + 6} y={pad.t} className="cutoff-label">
        Cut-off {cut}%
      </text>
    </svg>
  );
}
