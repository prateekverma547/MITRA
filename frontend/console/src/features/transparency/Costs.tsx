// What MITRA's models cost: by stage of the journey, per candidate and per
// hire, by model and by business unit, and week by week. One hue throughout:
// these are amounts, not categories. Every chart has a table view.

import { useSearchParams } from "react-router-dom";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { ChartCard } from "../../shell/viz/ChartCard";
import { TooltipLayer, tipProps, useTip } from "../../shell/viz/Tooltip";
import { TransparencyHead, useFetch } from "./common";
import { usd } from "./money";
import type { Costs as CostsData } from "./types";
import "./transparency.css";

const PERIODS = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 0, label: "All time" },
];

interface Bar {
  key: string;
  label: string;
  value: number;
  note?: string;
}

/** Horizontal bars on one scale, the amount printed at the end of each. */
function BarList({ rows, empty }: { rows: Bar[]; empty: string }) {
  const tip = useTip();
  const max = Math.max(0, ...rows.map((r) => r.value));
  if (!rows.length) return <p className="empty-note">{empty}</p>;
  return (
    <ul className="cost-bars">
      {rows.map((r) => (
        <li key={r.key} className="cost-bar-row">
          <span className="cost-bar-label">{r.label}</span>
          <span className="cost-bar-track">
            {r.value > 0 && (
              <span
                className="cost-bar"
                style={{ width: `${max ? Math.max(1.5, (r.value / max) * 100) : 0}%` }}
                {...tipProps(tip, usd(r.value), r.label)}
              />
            )}
          </span>
          <span className="cost-bar-value">
            {usd(r.value)}
            {r.note && <span className="cost-bar-note">{r.note}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Trend({ weeks, values }: { weeks: string[]; values: number[] }) {
  const tip = useTip();
  const max = Math.max(0, ...values);
  if (!max) return <p className="empty-note">No spend in the last eight weeks.</p>;
  const label = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <div className="cost-trend" role="img" aria-label="Spend per week, last eight weeks">
      {values.map((v, i) => (
        <div key={weeks[i]} className="cost-trend-col">
          <span className="cost-trend-track">
            <span className="cost-trend-bar" style={{ height: `${v ? Math.max(3, (v / max) * 100) : 0}%` }} {...tipProps(tip, usd(v), `Week of ${label(weeks[i])}`)} />
          </span>
          <span className="cost-trend-week">{i % 2 === 0 || i === values.length - 1 ? label(weeks[i]) : ""}</span>
        </div>
      ))}
    </div>
  );
}

function Table({ head, rows }: { head: [string, string]; rows: [string, string][] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>{head[0]}</th>
          <th className="num">{head[1]}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([a, b]) => (
          <tr key={a}>
            <td>{a}</td>
            <td className="num">{b}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Costs() {
  const [params, setParams] = useSearchParams();
  const days = Number(params.get("days") ?? 30);
  const { data, error, refreshing } = useFetch<CostsData>(`/transparency/costs?days=${days}`);
  const sub = "What MITRA's models cost, stage by stage, per candidate and per hire.";

  if (error && !data) {
    return (
      <div className="page page-wide">
        <TransparencyHead sub={sub} />
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <PageSkeleton rows={6} />;
  const t = data.totals;
  const stages: Bar[] = data.by_stage.map((s) => ({
    key: s.key,
    label: s.label,
    value: s.cost_usd,
    note: s.not_measured ? "Not measured yet" : s.unpriced ? `${s.unpriced} unpriced` : s.calls ? `${s.calls} ${s.calls === 1 ? "call" : "calls"}` : undefined,
  }));

  return (
    <TooltipLayer>
      <div className="page page-wide dash">
        <TransparencyHead sub={sub} />
        <div className="filters" role="group" aria-label="Period">
          <div className="segmented" role="group" aria-label="Period">
            {PERIODS.map((p) => (
              <button key={p.days} type="button" aria-pressed={days === p.days} onClick={() => setParams({ days: String(p.days) }, { replace: true })}>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
          <div className="cost-tiles">
            <div className="cost-tile">
              <span className="cost-tile-label">Spend in this period</span>
              <span className="cost-tile-value">{usd(t.cost_usd)}</span>
              <span className="cost-tile-note">{t.calls} model {t.calls === 1 ? "call" : "calls"}</span>
            </div>
            <div className="cost-tile">
              <span className="cost-tile-label">Per candidate</span>
              <span className="cost-tile-value">{t.per_candidate_usd !== null ? usd(t.per_candidate_usd) : "None yet"}</span>
              <span className="cost-tile-note">Average over {t.candidates} candidates</span>
            </div>
            <div className="cost-tile">
              <span className="cost-tile-label">Per hire</span>
              <span className="cost-tile-value">{t.per_hire_usd !== null ? usd(t.per_hire_usd) : "None yet"}</span>
              <span className="cost-tile-note">{t.hires ? `All spend over ${t.hires} shortlisted` : "Nobody shortlisted in this period"}</span>
            </div>
          </div>
          {t.unpriced > 0 && (
            <p className="notice notice-attention">
              {t.unpriced} {t.unpriced === 1 ? "call uses" : "calls use"} a model with no price set, so the totals leave them out. Set its price in MODEL_PRICES_JSON on the server.
            </p>
          )}

          <div className="viz-grid">
            <ChartCard
              title="Cost by stage"
              caption="Each stage of the journey, in order. Stages that do not report their usage yet are marked."
              table={<Table head={["Stage", "Cost"]} rows={data.by_stage.map((s) => [s.label, s.not_measured ? "Not measured" : usd(s.cost_usd)])} />}
            >
              <BarList rows={stages} empty="No spend in this period." />
            </ChartCard>
            <ChartCard
              title="Per candidate, by stage"
              caption="What one candidate costs on average at each stage they reached."
              table={<Table head={["Stage", "Per candidate"]} rows={data.per_candidate_by_stage.map((s) => [s.label, usd(s.cost_usd)])} />}
            >
              <BarList
                rows={data.per_candidate_by_stage.map((s) => ({ key: s.key, label: s.label, value: s.cost_usd ?? 0 }))}
                empty="No candidate costs yet."
              />
            </ChartCard>
          </div>

          <div className="viz-grid">
            <ChartCard
              title="By model"
              caption="Spend per model. A model with no price set shows as unpriced."
              table={<Table head={["Model", "Cost"]} rows={data.by_model.map((m) => [m.model, m.priced ? usd(m.cost_usd) : "Price not set"])} />}
            >
              <BarList
                rows={data.by_model.map((m) => ({ key: m.model, label: m.model, value: m.cost_usd, note: m.priced ? `${m.calls} ${m.calls === 1 ? "call" : "calls"}` : "Price not set" }))}
                empty="No calls in this period."
              />
            </ChartCard>
            <ChartCard
              title="By business unit"
              caption="Spend on each unit's job requests and candidates."
              table={<Table head={["Business unit", "Cost"]} rows={data.by_bu.map((b) => [b.name, usd(b.cost_usd)])} />}
            >
              <BarList rows={data.by_bu.map((b) => ({ key: b.name, label: b.name, value: b.cost_usd }))} empty="No spend in this period." />
            </ChartCard>
          </div>

          <ChartCard
            title="Spend per week"
            caption="The last eight weeks, whatever the period above."
            table={<Table head={["Week of", "Cost"]} rows={data.weeks.map((w, i) => [new Date(w).toLocaleDateString(), usd(data.trend_usd[i])])} />}
          >
            <Trend weeks={data.weeks} values={data.trend_usd} />
          </ChartCard>

          <p className="muted small">
            Prices: {data.price_source}.{" "}
            {data.not_measured.length > 0 && `Not measured yet: ${data.not_measured.map((n) => n.label).join(" and ")}.`}
          </p>
        </div>
      </div>
    </TooltipLayer>
  );
}
