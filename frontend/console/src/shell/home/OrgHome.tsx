// The org admin's home: what needs attention, how healthy hiring is against the
// previous period, and how the business units compare. Every sentence and
// figure is computed on the server from stages, decisions and screenings
// (GET /api/org/insights); no model writes them. The charts that are useful
// now and then sit under "More insight". Read-only.

import { AlertCircle, ArrowDownRight, ArrowUpRight, ChevronRight, Info, Minus } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { useSession } from "../../session";
import { MoreInsight } from "../ui/MoreInsight";
import { ChartCard } from "../viz/ChartCard";
import { Funnel, StepTime } from "../viz/Funnel";
import { PipelineBar } from "../viz/Pipeline";
import { ActivityFeed, PERIODS, WeeklyCard, type OverviewState } from "./common";
import "./org.css";

interface Insight {
  kind: string;
  tone: "attention" | "info";
  text: string;
  link: { to: string; label: string };
  basis: string;
}

interface Kpi {
  value: number | null;
  n: number;
  unit: "days" | "percent" | "candidates";
  previous: number | null;
  change_percent: number | null;
  better: "lower" | "higher";
  over_week?: number;
  over_week_percent?: number | null;
}

interface Unit {
  bu_id: string;
  name: string;
  open_jobs: number;
  in_progress: number;
  median_days_to_decision: number | null;
  decided: number;
  pass_rate: number | null;
  screened: number;
  waiting_over_week: number;
}

interface OrgInsights {
  computed_at: string;
  days: number;
  min_n: number;
  insights: Insight[];
  kpis: { time_to_decision: Kpi; screen_to_shortlist: Kpi; waiting_on_people: Kpi; agreement: Kpi };
  units: Unit[];
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function periodWords(days: number) {
  return days ? `the last ${days} days` : "all time";
}

function Change({ k, days }: { k: Kpi; days: number }) {
  if (!days) return <span className="kpi-change muted">All time</span>;
  if (k.change_percent === null) return <span className="kpi-change muted">Too few cases to compare</span>;
  // A rate changes by percentage points, not by a percentage of itself:
  // 2.6% from 3.2% is "-0.6 pts", which "-19%" would hide.
  const points = k.unit === "percent" && k.value !== null && k.previous !== null ? k.value - k.previous : null;
  const delta = points ?? k.change_percent;
  const up = delta > 0;
  const flat = points !== null ? Math.abs(points) < 0.5 : Math.abs(k.change_percent) < 1;
  const good = flat ? null : (k.better === "lower") !== up;
  const Icon = flat ? Minus : up ? ArrowUpRight : ArrowDownRight;
  const amount = points !== null ? `${Math.abs(points).toFixed(1)} pts` : `${Math.abs(k.change_percent).toFixed(0)}%`;
  return (
    <span className={`kpi-change ${good === null ? "flat" : good ? "good" : "bad"}`}>
      <Icon size={13} aria-hidden="true" />
      {flat ? "Steady" : `${up ? "+" : "−"}${amount}`} vs previous {days} days
    </span>
  );
}

function KpiTile({ title, value, sub, k, days }: { title: string; value: string; sub: string; k: Kpi; days: number }) {
  return (
    <div className="kpi">
      <span className="kpi-title">{title}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-sub">{sub}</span>
      {k.unit !== "candidates" && <Change k={k} days={days} />}
    </div>
  );
}

/** The worst value in a column, only when there is something to compare. */
function worstOf(units: Unit[], pick: (u: Unit) => number | null, worst: "max" | "min") {
  const values = units.map(pick).filter((v): v is number => v !== null);
  if (values.length < 2 || Math.min(...values) === Math.max(...values)) return null;
  return worst === "max" ? Math.max(...values) : Math.min(...values);
}

export function OrgHome({ state }: { state: OverviewState }) {
  const navigate = useNavigate();
  const { me } = useSession();
  const data = state.data!;
  const days = state.days;
  const bu = state.bu;
  const [org, setOrg] = useState<OrgInsights | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<OrgInsights>(`/org/insights?days=${days}`)
      .then((o) => {
        setOrg(o);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [days]);

  function openGroup(group: string) {
    const q = new URLSearchParams({ group });
    if (bu) q.set("bu", bu);
    navigate(`/candidates?${q}`, { viewTransition: true });
  }

  const first = (me?.name ?? "").split(" ")[0] || "there";
  const focused = bu ? data.scope.business_units.find((b) => b.id === bu)?.name : null;
  const k = org?.kpis;
  const units = org?.units ?? [];
  const worstDays = worstOf(units, (u) => u.median_days_to_decision, "max");
  const worstPass = worstOf(units, (u) => u.pass_rate, "min");
  const worstWait = worstOf(units, (u) => u.waiting_over_week, "max");
  const reached = data.conversion;
  const anyway = data.proceed_anyway;
  const anywayOn = anyway.groups.technical + anyway.groups.bu_head + anyway.groups.shortlisted;

  return (
    <div className="page page-wide dash org-home">
      <header className="page-head">
        <div>
          <h1 className="page-title">
            {greeting()}, {first}.
          </h1>
          <p className="page-sub">
            Here is how hiring went in {periodWords(days)}.
            {org && ` Worked out at ${new Date(org.computed_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })} from every decision on record.`}
          </p>
        </div>
        <div className="segmented" role="group" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p.days} type="button" aria-pressed={days === p.days} onClick={() => state.setFilter("days", String(p.days))}>
              {p.label}
            </button>
          ))}
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      <section className="card-viz attention" aria-labelledby="attention-title">
        <header className="card-viz-head">
          <div>
            <h2 id="attention-title" className="card-title">
              What needs your attention
            </h2>
            <p className="card-caption">
              The few things that stand out, most important first. Each rests on at least {org?.min_n ?? 5} cases.
            </p>
          </div>
        </header>
        {!org ? (
          <p className="empty-note">Working it out.</p>
        ) : org.insights.length === 0 ? (
          <p className="empty-note">Nothing stands out in {periodWords(days)}. Hiring is moving evenly across the units.</p>
        ) : (
          <ol className="insights">
            {org.insights.map((i) => (
              <li key={i.kind + i.text}>
                <button type="button" className={`insight insight-${i.tone}`} onClick={() => navigate(i.link.to, { viewTransition: true })}>
                  {i.tone === "attention" ? <AlertCircle size={17} aria-hidden="true" /> : <Info size={17} aria-hidden="true" />}
                  <span className="insight-body">
                    <span className="insight-text">{i.text}</span>
                    <span className="insight-basis">{i.basis}</span>
                  </span>
                  <span className="insight-go">
                    {i.link.label}
                    <ChevronRight size={16} aria-hidden="true" />
                  </span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>

      {k && (
        <section aria-labelledby="health-title">
          <div className="section-head">
            <h2 id="health-title" className="card-title">
              Health of hiring
            </h2>
            <p className="card-caption">Four numbers that say whether hiring is getting faster, better and fairer.</p>
          </div>
          <div className="kpis">
            <KpiTile
              title="Time to a decision"
              value={k.time_to_decision.value === null ? "–" : `${k.time_to_decision.value} days`}
              sub={
                k.time_to_decision.value === null
                  ? `Needs ${org!.min_n} BU head decisions; ${k.time_to_decision.n} so far`
                  : `Median from CV added to the BU head's decision, over ${k.time_to_decision.n}`
              }
              k={k.time_to_decision}
              days={days}
            />
            <KpiTile
              title="Screened to shortlisted"
              value={k.screen_to_shortlist.value === null ? "–" : `${k.screen_to_shortlist.value}%`}
              sub={
                k.screen_to_shortlist.value === null
                  ? `Needs ${org!.min_n} screened candidates; ${k.screen_to_shortlist.n} so far`
                  : `Of ${k.screen_to_shortlist.n} screened in this period`
              }
              k={k.screen_to_shortlist}
              days={days}
            />
            <KpiTile
              title="Waiting on people"
              value={`${k.waiting_on_people.value}`}
              sub={
                k.waiting_on_people.value
                  ? `${k.waiting_on_people.over_week} of them (${k.waiting_on_people.over_week_percent ?? 0}%) for over a week. Right now.`
                  : "Nobody is waiting on a person right now."
              }
              k={k.waiting_on_people}
              days={days}
            />
            <KpiTile
              title="People agreed with MITRA"
              value={k.agreement.value === null ? "–" : `${k.agreement.value}%`}
              sub={
                k.agreement.value === null
                  ? `Needs ${org!.min_n} comparable decisions; ${k.agreement.n} so far`
                  : `Of ${k.agreement.n} decisions MITRA could be compared on`
              }
              k={k.agreement}
              days={days}
            />
          </div>
        </section>
      )}

      {units.length > 1 && (
        <section className="card-viz" aria-labelledby="units-title">
          <header className="card-viz-head">
            <div>
              <h2 id="units-title" className="card-title">
                Business units
              </h2>
              <p className="card-caption">
                How each unit is hiring in {periodWords(days)}. The slowest, the lowest pass rate and the longest waits are marked. Select a unit
                to focus on it.
              </p>
            </div>
            {focused && (
              <button type="button" className="chip-btn" aria-pressed="true" onClick={() => state.setFilter("bu", null)}>
                Showing {focused}. Show all
              </button>
            )}
          </header>
          <div className="table-wrap">
            <table className="units">
              <thead>
                <tr>
                  <th>Unit</th>
                  <th className="num">Open jobs</th>
                  <th className="num">In progress</th>
                  <th className="num">Days to a decision</th>
                  <th className="num">Pass screening</th>
                  <th className="num">Waiting over a week</th>
                </tr>
              </thead>
              <tbody>
                {units.map((u) => (
                  <tr
                    key={u.bu_id}
                    className={bu === u.bu_id ? "on" : undefined}
                    tabIndex={0}
                    onClick={() => state.setFilter("bu", bu === u.bu_id ? null : u.bu_id)}
                    onKeyDown={(e) => e.key === "Enter" && state.setFilter("bu", bu === u.bu_id ? null : u.bu_id)}
                  >
                    <td className="unit-name">{u.name}</td>
                    <td className="num">{u.open_jobs}</td>
                    <td className="num">{u.in_progress}</td>
                    <td className={`num${u.median_days_to_decision !== null && u.median_days_to_decision === worstDays ? " worst" : ""}`}>
                      {u.median_days_to_decision === null ? <span className="muted">Too few</span> : `${u.median_days_to_decision} d`}
                    </td>
                    <td className={`num${u.pass_rate !== null && u.pass_rate === worstPass ? " worst" : ""}`}>
                      {u.pass_rate === null ? <span className="muted">Too few</span> : `${u.pass_rate.toFixed(0)}%`}
                    </td>
                    <td className={`num${u.waiting_over_week > 0 && u.waiting_over_week === worstWait ? " worst" : ""}`}>{u.waiting_over_week}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <MoreInsight id="org" hint={`Where everyone stands${focused ? ` in ${focused}` : ""}, how far they get, time in each step, weekly flow, overrides, activity`}>
        <div className={state.refreshing ? "dash-body refreshing" : "dash-body"}>
          <ChartCard
            title={`Where everyone stands${focused ? `, ${focused}` : ""}`}
            caption="Each step of the journey, in order. Select a step to see who is in it."
            table={
              <table>
                <thead>
                  <tr>
                    <th>Step</th>
                    <th className="num">Candidates</th>
                  </tr>
                </thead>
                <tbody>
                  {data.pipeline.map((g) => (
                    <tr key={g.key}>
                      <td>{g.label}</td>
                      <td className="num">{g.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            }
          >
            <PipelineBar groups={data.pipeline} onPick={openGroup} />
          </ChartCard>
          <div className="viz-grid">
            <ChartCard
              title="How far candidates get"
              caption="Everyone who started in this period, by the furthest step reached."
              table={
                <table>
                  <thead>
                    <tr>
                      <th>Step</th>
                      <th className="num">Reached</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reached.map((s) => (
                      <tr key={s.key}>
                        <td>{s.label}</td>
                        <td className="num">{s.reached}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            >
              <Funnel steps={reached} />
            </ChartCard>
            <ChartCard
              title="Time in each step"
              caption="Median days from entering a step to leaving it, for those who have left it."
              table={
                <table>
                  <thead>
                    <tr>
                      <th>Step</th>
                      <th className="num">Median days</th>
                      <th className="num">Finished</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.stage_time.map((s) => (
                      <tr key={s.key}>
                        <td>{s.label}</td>
                        <td className="num">{s.median_days === null ? "" : s.median_days.toFixed(1)}</td>
                        <td className="num">{s.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            >
              <StepTime steps={data.stage_time} />
            </ChartCard>
          </div>
          <div className="viz-grid">
            <WeeklyCard data={data} title="Weekly flow" caption="Candidates added against decisions people made, over eight weeks." decided={data.kpis.decided_by_week} />
            <ChartCard
              title="Sent on below the cut-off"
              caption={
                anyway.total
                  ? `HR chose "Proceed anyway" for ${anyway.total}. ${anywayOn} reached the MITRA interview round or later.`
                  : 'Nobody has been sent on below the cut-off with "Proceed anyway".'
              }
              table={
                <table>
                  <thead>
                    <tr>
                      <th>Where they are now</th>
                      <th className="num">Candidates</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.pipeline.map((g) => (
                      <tr key={g.key}>
                        <td>{g.label}</td>
                        <td className="num">{anyway.groups[g.key]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            >
              {anyway.total ? (
                <PipelineBar groups={data.pipeline.map((g) => ({ ...g, count: anyway.groups[g.key] }))} onPick={openGroup} />
              ) : (
                <p className="empty-note">When HR overrides the cut-off, where those candidates end up shows here.</p>
              )}
            </ChartCard>
          </div>
          <ActivityFeed data={data} limit={6} />
        </div>
      </MoreInsight>
    </div>
  );
}
