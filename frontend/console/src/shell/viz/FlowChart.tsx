import { useRef, useState } from "react";

/**
 * Two lines on one axis: candidates added and decisions people made, by week.
 * A crosshair finds the week; the readout lists both.
 */
export function FlowChart({ weeks, added, decided }: { weeks: string[]; added: number[]; decided: number[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const W = 520;
  const H = 180;
  const pad = { l: 28, r: 64, t: 14, b: 26 };
  const max = Math.max(1, ...added, ...decided);
  const x = (i: number) => pad.l + (i * (W - pad.l - pad.r)) / Math.max(1, weeks.length - 1);
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const label = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const last = weeks.length - 1;

  function move(e: React.PointerEvent<SVGSVGElement>) {
    const box = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * last);
    setHover(Math.max(0, Math.min(last, i)));
  }

  return (
    <div className="flow">
      <svg
        ref={svg}
        className="flow-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Candidates added and decisions made, by week"
        onPointerMove={move}
        onPointerLeave={() => setHover(null)}
      >
        {[0, max].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 8} y={y(t) + 4} className="axis" textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        {weeks.map((w, i) =>
          i % 2 === last % 2 ? (
            <text key={w} x={x(i)} y={H - 8} className="axis" textAnchor="middle">
              {label(w)}
            </text>
          ) : null,
        )}
        <path d={path(added)} className="line line-1" />
        <path d={path(decided)} className="line line-2" />
        <circle cx={x(last)} cy={y(added[last])} r={4} className="end end-1" />
        <circle cx={x(last)} cy={y(decided[last])} r={4} className="end end-2" />
        <text x={x(last) + 9} y={y(added[last]) + 4} className="end-label">
          Added
        </text>
        <text x={x(last) + 9} y={y(decided[last]) + (Math.abs(y(added[last]) - y(decided[last])) < 12 ? 16 : 4)} className="end-label">
          Decided
        </text>
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={H - pad.b} className="crosshair" />
            <circle cx={x(hover)} cy={y(added[hover])} r={4} className="end end-1" />
            <circle cx={x(hover)} cy={y(decided[hover])} r={4} className="end end-2" />
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="flow-readout" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <span className="flow-week">Week of {label(weeks[hover])}</span>
          <span>
            <i className="key key-1" />
            <strong>{added[hover]}</strong> added
          </span>
          <span>
            <i className="key key-2" />
            <strong>{decided[hover]}</strong> decided
          </span>
        </div>
      )}
      <ul className="legend legend-inline">
        <li>
          <i className="key key-1" /> Candidates added
        </li>
        <li>
          <i className="key key-2" /> Decisions by people
        </li>
      </ul>
    </div>
  );
}
