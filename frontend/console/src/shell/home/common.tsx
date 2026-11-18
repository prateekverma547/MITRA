// Pieces every role's home shares: the data, the filter row, and the cards
// more than one role shows. Each role's home composes its own page from them
// (HRHome, BUHome, OrgHome): what a person sees first is what they act on.

import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS, ago, initials, type Overview, type OverviewJob } from "../../contracts";
import { ChartCard } from "../viz/ChartCard";
import { FlowChart } from "../viz/FlowChart";
import { ThinPipeline } from "../viz/Pipeline";
import { ScoreHistogram } from "../viz/ScoreHistogram";

export const PERIODS = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 0, label: "All time" },
];

export function sum(values: number[]) {
  return values.reduce((a, b) => a + b, 0);
}

/** The overview for the signed-in person, with the BU and period kept in the URL. */
export function useOverview() {
  const [params, setParams] = useSearchParams();
  const bu = params.get("bu");
  const days = Number(params.get("days") ?? 90);
  const [data, setData] = useState<Overview | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRefreshing(true);
    const query = new URLSearchParams({ days: String(days) });
    if (bu) query.set("bu", bu);
    api
      .get<Overview>(`/overview?${query}`)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [bu, days]);

  function setFilter(key: "bu" | "days", value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  }

  return { data, error, refreshing, bu, days, setFilter };
}

export type OverviewState = ReturnType<typeof useOverview>;

export function Filters({ state, showBu = true }: { state: OverviewState; showBu?: boolean }) {
  const { data, bu, days, setFilter } = state;
  const multiBu = showBu && (data?.scope.business_units.length ?? 0) > 1;
  return (
    <div className="filters" role="group" aria-label="Filters">
      {multiBu && (
        <div className="chips" role="group" aria-label="Business unit">
          <button type="button" className="chip-btn" aria-pressed={!bu} onClick={() => setFilter("bu", null)}>
            All business units
          </button>
          {data!.scope.business_units.map((b) => (
            <button key={b.id} type="button" className="chip-btn" aria-pressed={bu === b.id} onClick={() => setFilter("bu", b.id)}>
              {b.name}
            </button>
          ))}
        </div>
      )}
      <div className="segmented" role="group" aria-label="Period">
        {PERIODS.map((p) => (
          <button key={p.days} type="button" aria-pressed={days === p.days} onClick={() => setFilter("days", String(p.days))}>
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function pathFor(n: Overview["needs_you"][number]) {
  if (n.stage === "hr_booked" || n.stage === "hr_done") return `/candidates/${n.candidate_id}/hr-round`;
  if (n.stage === "with_bu_head" || n.stage === "on_hold") return `/candidates/${n.candidate_id}/results`;
  return `/candidates/${n.candidate_id}`;
}

export function NeedsQueue({ data, title, caption, empty, limit = 5 }: { data: Overview; title: string; caption: string; empty: string; limit?: number }) {
  const navigate = useNavigate();
  const multiBu = data.scope.business_units.length > 1;
  return (
    <section className="card-viz">
      <header className="card-viz-head">
        <div>
          <h2 className="card-title">{title}</h2>
          <p className="card-caption">{caption}</p>
        </div>
        {data.kpis.needs_you > Math.min(limit, data.needs_you.length) && (
          <button type="button" className="quiet-link" onClick={() => navigate("/candidates?flag=needs_me&sort=waiting", { viewTransition: true })}>
            All {data.kpis.needs_you}
          </button>
        )}
      </header>
      {data.needs_you.length === 0 ? (
        <p className="empty-note">{empty}</p>
      ) : (
        <ul className="queue">
          {data.needs_you.slice(0, limit).map((n) => (
            <li key={n.candidate_id}>
              <button type="button" className="queue-item" onClick={() => navigate(pathFor(n), { viewTransition: true })}>
                <span className="avatar" aria-hidden="true">
                  {initials(n.name)}
                </span>
                <span>
                  <span className="queue-name">{n.name}</span>
                  <span className="queue-sub">
                    {n.action}. {n.job_title}
                    {n.bu_name && multiBu ? `, ${n.bu_name}` : ""}
                  </span>
                </span>
                <span className={n.waiting_days >= 3 ? "wait long" : "wait"}>
                  {n.waiting_days === 0 ? "Today" : `${n.waiting_days} ${n.waiting_days === 1 ? "day" : "days"}`}
                </span>
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ActivityFeed({ data, limit = 8 }: { data: Overview; limit?: number }) {
  const navigate = useNavigate();
  return (
    <section className="card-viz">
      <header className="card-viz-head">
        <div>
          <h2 className="card-title">Recent activity</h2>
          <p className="card-caption">Every move, by MITRA and by people.</p>
        </div>
      </header>
      {data.activity.length === 0 ? (
        <p className="empty-note">Nothing has moved yet.</p>
      ) : (
        <ul className="feed">
          {data.activity.slice(0, limit).map((a, i) => (
            <li key={i}>
              <span className={a.actor === "MITRA" ? "feed-dot" : "feed-dot person"} aria-hidden="true" />
              <span>
                <span className="feed-text">
                  <strong>{a.actor}</strong> moved{" "}
                  <button type="button" className="quiet-link" onClick={() => navigate(`/candidates/${a.candidate_id}`, { viewTransition: true })}>
                    {a.candidate}
                  </button>{" "}
                  to {STAGE_LABELS[a.to]}
                </span>
                <span className="feed-when"> {ago(a.at)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** One row per job: where its candidates stand, and what waits on you there. */
export function JobRows({
  jobs,
  title,
  caption,
  showBu,
  extra,
  limit = 5,
}: {
  jobs: OverviewJob[];
  title: string;
  caption: string;
  showBu: boolean;
  extra?: (job: OverviewJob) => React.ReactNode;
  limit?: number;
}) {
  const navigate = useNavigate();
  const [all, setAll] = useState(false);
  const shown = all ? jobs : jobs.slice(0, limit);
  const groups = ["screening", "hr", "technical", "bu_head", "shortlisted", "closed"] as const;
  return (
    <ChartCard
      title={title}
      caption={caption}
      table={
        <table>
          <thead>
            <tr>
              <th>Job</th>
              {showBu && <th>Business unit</th>}
              {groups.map((g) => (
                <th key={g} className="num">
                  {g === "bu_head" ? "With BU head" : g[0].toUpperCase() + g.slice(1).replace("hr", "HR round")}
                </th>
              ))}
              <th className="num">Waiting on you</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.title}</td>
                {showBu && <td>{j.bu_name}</td>}
                {groups.map((g) => (
                  <td key={g} className="num">
                    {j.groups[g]}
                  </td>
                ))}
                <td className="num">{j.waiting_on_you}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      {jobs.length === 0 ? (
        <p className="empty-note">No job requests yet.</p>
      ) : (
        <ul className="bu-rows job-rows">
          {shown.map((j) => (
            <li key={j.id}>
              <button type="button" className="bu-row" onClick={() => navigate(`/jobs/${j.id}/candidates`, { viewTransition: true })}>
                <span className="bu-name">
                  {j.title}
                  <span className="job-row-sub">
                    {showBu && j.bu_name ? `${j.bu_name}. ` : ""}
                    {j.status === "draft" ? "Being described" : j.status === "open" ? (j.open_days ? `Open ${j.open_days} ${j.open_days === 1 ? "day" : "days"}` : "Opened today") : "Closed"}
                  </span>
                </span>
                <ThinPipeline groups={j.groups} label={`${j.title}: ${j.total} candidates`} />
                <span className="bu-total">{j.total}</span>
                <span className={j.waiting_on_you ? "bu-open waits" : "bu-open"}>{j.waiting_on_you ? `${j.waiting_on_you} for you` : extra?.(j) ?? ""}</span>
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {jobs.length > limit && (
        <button type="button" className="btn btn-ghost btn-sm show-all" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${jobs.length}`}
        </button>
      )}
    </ChartCard>
  );
}

export function ScoresCard({ data }: { data: Overview }) {
  return (
    <ChartCard
      title="Screening scores"
      caption={data.scores.screened ? `${data.scores.screened} screened in this period. Below the cut-off in grey.` : "Nobody screened in this period."}
      table={
        <table>
          <thead>
            <tr>
              <th>Score</th>
              <th className="num">Candidates</th>
            </tr>
          </thead>
          <tbody>
            {data.scores.bins.map((n, i) => (
              <tr key={i}>
                <td>
                  {i * 10}–{i * 10 + 9}%
                </td>
                <td className="num">{n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ScoreHistogram bins={data.scores.bins} threshold={data.scores.threshold} />
    </ChartCard>
  );
}

export function WeeklyCard({ data, title, caption, decided, decidedLabel }: { data: Overview; title: string; caption: string; decided: number[]; decidedLabel?: string }) {
  return (
    <ChartCard
      title={title}
      caption={caption}
      table={
        <table>
          <thead>
            <tr>
              <th>Week of</th>
              <th className="num">Added</th>
              <th className="num">Decided</th>
            </tr>
          </thead>
          <tbody>
            {data.weeks.map((w, i) => (
              <tr key={w}>
                <td>{new Date(w).toLocaleDateString()}</td>
                <td className="num">{data.kpis.added_by_week[i]}</td>
                <td className="num">{decided[i]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <FlowChart weeks={data.weeks} added={data.kpis.added_by_week} decided={decided} decidedLabel={decidedLabel} />
    </ChartCard>
  );
}

export function HomeError({ error }: { error: string }) {
  return (
    <div className="page">
      <h1 className="page-title">Home</h1>
      <p className="error">{error}</p>
    </div>
  );
}
