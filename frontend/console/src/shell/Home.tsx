// The dashboard. One screen, read three ways: the server has already scoped
// every number to the person signed in, so nothing here filters for safety.
// Filters sit in one row above everything they scope; refetching keeps the
// current view, dimmed, rather than flashing placeholders.

import { ChevronRight, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { STAGE_LABELS, ago, initials, type Overview } from "../contracts";
import { useSession } from "../session";
import { ButtonLink } from "./ui/Button";
import { PageSkeleton } from "./ui/Skeleton";
import { ChartCard } from "./viz/ChartCard";
import { FlowChart } from "./viz/FlowChart";
import { PipelineBar, ThinPipeline } from "./viz/Pipeline";
import { ScoreHistogram } from "./viz/ScoreHistogram";
import { StatTile } from "./viz/StatTile";
import { TooltipLayer } from "./viz/Tooltip";

const PERIODS = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 0, label: "All time" },
];

const TITLES = {
  bu_head: (bu: string | null) => `${bu ?? "Your"} hiring`,
  hr: () => "Hiring across the organisation",
  org_admin: () => "Organisation overview",
};

const SUBTITLES = {
  bu_head: "Your job requests, your candidates, and what is waiting for your decision.",
  hr: "Every approved job request, every candidate, and what needs you next.",
  org_admin: "Read-only. Every business unit, job and candidate, and how hiring is moving.",
};

function sum(values: number[]) {
  return values.reduce((a, b) => a + b, 0);
}

export function Home() {
  const { me } = useSession();
  const navigate = useNavigate();
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

  function openGroup(group: string) {
    const q = new URLSearchParams({ group });
    if (bu) q.set("bu", bu);
    navigate(`/candidates?${q}`, { viewTransition: true });
  }

  const multiBu = (data?.scope.business_units.length ?? 0) > 1;
  const decided = useMemo(() => sum(data?.kpis.decided_by_week ?? []), [data]);

  if (error && !data) {
    return (
      <div className="page">
        <h1 className="page-title">Home</h1>
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data || !me) return <PageSkeleton />;

  const k = data.kpis;
  const passRate = k.screened ? Math.round((k.passed_screen / k.screened) * 100) : 0;

  return (
    <TooltipLayer>
      <div className="page page-wide dash">
        <header className="page-head">
          <div>
            <h1 className="page-title">{TITLES[me.role](me.bu_name)}</h1>
            <p className="page-sub">{SUBTITLES[me.role]}</p>
          </div>
          {me.role === "bu_head" && (
            <ButtonLink variant="primary" to="/jobs/new" icon={<Plus size={17} />}>
              New job request
            </ButtonLink>
          )}
        </header>

        <div className="filters" role="group" aria-label="Filters">
          {multiBu && (
            <div className="chips" role="group" aria-label="Business unit">
              <button type="button" className="chip-btn" aria-pressed={!bu} onClick={() => setFilter("bu", null)}>
                All business units
              </button>
              {data.scope.business_units.map((b) => (
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

        <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
          <div className="stats">
            <StatTile
              label="Open job requests"
              value={k.open_jobs}
              note={k.draft_jobs ? `${k.draft_jobs} still being described` : "All approved requests"}
            />
            <StatTile
              label="Candidates in progress"
              value={k.active_candidates}
              trend={k.added_by_week}
              trendLabel="Candidates added per week, last eight weeks"
              note={`${sum(k.added_by_week)} added in the last eight weeks`}
            />
            {me.role === "org_admin" ? (
              <StatTile
                label="Decisions by people"
                value={decided}
                trend={k.decided_by_week}
                trendLabel="Decisions per week, last eight weeks"
                note="In the last eight weeks"
              />
            ) : (
              <StatTile label="Waiting for you" value={k.needs_you} accent={k.needs_you > 0} note={k.needs_you ? "Oldest first, below" : "Nothing is waiting on you"} />
            )}
            <StatTile label="Passed screening" value={k.passed_screen} note={k.screened ? `${passRate}% of ${k.screened} screened` : "Nobody screened in this period"}>
              <span className="stat-meter" aria-hidden="true">
                <span style={{ width: `${passRate}%` }} />
              </span>
            </StatTile>
          </div>

          <ChartCard
            title="Where everyone stands"
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

          <div className="split-2">
            <section className="card-viz">
              <header className="card-viz-head">
                <div>
                  <h2 className="card-title">{me.role === "org_admin" ? "Waiting longest" : "Needs you"}</h2>
                  <p className="card-caption">Longest wait first. Select someone to act.</p>
                </div>
              </header>
              {data.needs_you.length === 0 ? (
                <p className="empty-note">{me.role === "org_admin" ? "Admins read; HR and BU heads act." : "Nothing is waiting on you."}</p>
              ) : (
                <ul className="queue">
                  {data.needs_you.map((n) => (
                    <li key={n.candidate_id}>
                      <button
                        type="button"
                        className="queue-item"
                        onClick={() => navigate(n.stage === "hr_booked" ? `/candidates/${n.candidate_id}/hr-round` : `/candidates/${n.candidate_id}`, { viewTransition: true })}
                      >
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
                  {data.activity.slice(0, 8).map((a, i) => (
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
          </div>

          {multiBu && !bu && (
            <ChartCard
              title="Business units"
              caption="Candidates in each unit, by step. Select a unit to focus the whole page on it."
              table={
                <table>
                  <thead>
                    <tr>
                      <th>Business unit</th>
                      {data.pipeline.map((g) => (
                        <th key={g.key} className="num">
                          {g.label}
                        </th>
                      ))}
                      <th className="num">Open jobs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_bu.map((b) => (
                      <tr key={b.bu_id ?? "none"}>
                        <td>{b.name}</td>
                        {data.pipeline.map((g) => (
                          <td key={g.key} className="num">
                            {b.groups[g.key]}
                          </td>
                        ))}
                        <td className="num">{b.open_jobs}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            >
              <ul className="bu-rows">
                {data.by_bu.map((b) => (
                  <li key={b.bu_id ?? "none"}>
                    <button type="button" className="bu-row" disabled={!b.bu_id} onClick={() => b.bu_id && setFilter("bu", b.bu_id)}>
                      <span className="bu-name">{b.name}</span>
                      <ThinPipeline groups={b.groups} label={`${b.name}: ${b.total} candidates`} />
                      <span className="bu-total">{b.total}</span>
                      <span className="bu-open">
                        {b.open_jobs} open {b.open_jobs === 1 ? "job" : "jobs"}
                      </span>
                      <ChevronRight size={18} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            </ChartCard>
          )}

          <div className="viz-grid">
            <ChartCard
              title="Screening scores"
              caption={
                data.scores.screened
                  ? `${data.scores.screened} screened in this period. Below the cut-off in grey.`
                  : "Nobody screened in this period."
              }
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
            <ChartCard
              title="Weekly flow"
              caption="Candidates added against decisions people made, over eight weeks."
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
                        <td className="num">{k.added_by_week[i]}</td>
                        <td className="num">{k.decided_by_week[i]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            >
              <FlowChart weeks={data.weeks} added={k.added_by_week} decided={k.decided_by_week} />
            </ChartCard>
          </div>
        </div>
      </div>
    </TooltipLayer>
  );
}
