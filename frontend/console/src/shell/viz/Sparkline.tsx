/** Eight weeks in one line: de-emphasised history, the current week in the accent. */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  const w = 120;
  const h = 32;
  const max = Math.max(1, ...values);
  const step = values.length > 1 ? w / (values.length - 1) : w;
  const pts = values.map((v, i) => [i * step, h - 4 - (v / max) * (h - 8)] as const);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = pts[pts.length - 1] ?? [0, h];
  return (
    <svg className="spark" viewBox={`-4 0 ${w + 8} ${h}`} width={w} height={h} role="img" aria-label={label}>
      <path d={`${d} L${w},${h} L0,${h} Z`} className="spark-area" />
      <path d={d} className="spark-line" />
      <circle cx={lx} cy={ly} r={4} className="spark-dot" />
    </svg>
  );
}
