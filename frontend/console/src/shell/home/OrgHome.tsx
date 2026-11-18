// The org admin's home is about how the organisation hires: where candidates
// drop out, how long each step takes, how the business units compare, and
// whether people's overrides of the cut-off worked out. Read-only.

import { ChevronRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { MoreInsight } from "../ui/MoreInsight";
import { ChartCard } from "../viz/ChartCard";
import { Funnel, StepTime } from "../viz/Funnel";
import { PipelineBar, ThinPipeline } from "../viz/Pipeline";
import { StatTile } from "../viz/StatTile";
import { ActivityFeed, Filters, WeeklyCard, sum, type OverviewState } from "./common";

export function OrgHome({ state }: { state: OverviewState }) {
  const navigate = useNavigate();
  const data = state.data!;
  const k = data.kpis;
  const bu = state.bu;
  const multiBu = data.scope.business_units.length > 1;
  const reached = data.conversion;
  const end = reached[reached.length - 1]?.reached ?? 0;
  const start = reached[0]?.reached ?? 0;
  const anyway = data.proceed_anyway;
  const anywayOn = anyway.groups.technical + anyway.groups.bu_head + anyway.groups.shortlisted;

  function openGroup(group: string) {
    const q = new URLSearchParams({ group });
    if (bu) q.set("bu", bu);
    navigate(`/candidates?${q}`, { viewTransition: true });
  }

  return (
    <div className="page page-wide dash">
      <header className="page-head">
        <div>
          <h1 className="page-title">Organisation overview</h1>
          <p className="page-sub">How the organisation is hiring. Read-only.</p>
        </div>
      </header>
      <Filters state={state} />
      <div className={state.refreshing ? "dash-body refreshing" : "dash-body"}>
        <div className="stats">
          <StatTile label="Open job requests" value={k.open_jobs} note={`${data.scope.business_units.length} business units${k.draft_jobs ? `, ${k.draft_jobs} drafts` : ""}`} />
          <StatTile label="Candidates in progress" value={k.active_candidates} trend={k.added_by_week} trendLabel="Candidates added per week" note={`${sum(k.added_by_week)} added in eight weeks`} />
          <StatTile label="Shortlisted" value={end} note={start ? `${Math.round((end / start) * 100)}% of ${start} who started in this period` : "Nobody started in this period"} />
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
          {multiBu && !bu && (
            <ChartCard
              title="Business units"
              caption="Candidates by step. Select a unit to focus the page on it."
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
                    <button type="button" className="bu-row" disabled={!b.bu_id} onClick={() => b.bu_id && state.setFilter("bu", b.bu_id)}>
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
        </div>


        <MoreInsight id="org" hint="Time in each step, weekly flow, cut-off overrides, activity">
        <div className="viz-grid">
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
          <WeeklyCard data={data} title="Weekly flow" caption="Candidates added against decisions people made, over eight weeks." decided={k.decided_by_week} />
          <ChartCard
            title="Sent on below the cut-off"
            caption={
              anyway.total
                ? `HR chose "Proceed anyway" for ${anyway.total}. ${anywayOn} reached the technical round or later.`
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
        </MoreInsight>
      </div>
    </div>
  );
}
