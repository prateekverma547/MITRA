import type { ReactNode } from "react";
import { CountUp } from "../ui/CountUp";
import { Sparkline } from "./Sparkline";

/** label, value, an optional note and an eight-week trend. */
export function StatTile({
  label,
  value,
  note,
  trend,
  trendLabel,
  accent = false,
  children,
}: {
  label: string;
  value: number;
  note?: string;
  trend?: number[];
  trendLabel?: string;
  accent?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={accent ? "stat stat-accent" : "stat"}>
      <span className="stat-label">{label}</span>
      <div className="stat-row">
        <span className="stat-value">
          <CountUp value={value} />
        </span>
        {trend && <Sparkline values={trend} label={trendLabel ?? label} />}
      </div>
      {note && <span className="stat-note">{note}</span>}
      {children}
    </div>
  );
}
