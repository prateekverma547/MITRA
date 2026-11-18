// HR's home is a workbench. Up front: what waits on them and what is going
// stale, then the jobs with work in them. Everything else is in More insight.

import { useNavigate } from "react-router-dom";
import type { Overview } from "../../contracts";
import { MoreInsight } from "../ui/MoreInsight";
import { AgeingBars } from "../viz/Ageing";
import { ChartCard } from "../viz/ChartCard";
import { StatTile } from "../viz/StatTile";
import { Filters, JobRows, NeedsQueue, ScoresCard, WeeklyCard, sum, type OverviewState } from "./common";

function waiting(data: Overview, actions: string[]) {
  return data.ageing.rows.filter((r) => actions.includes(r.action)).reduce((s, r) => s + r.count, 0);
}

export function HRHome({ state }: { state: OverviewState }) {
  const navigate = useNavigate();
  const data = state.data!;
  const k = data.kpis;
  const stale = data.ageing.rows.reduce((s, r) => s + r.buckets[2], 0);
  const rounds = waiting(data, ["Book the HR round", "Run the HR round", "Decide after the HR round"]);
  const multiBu = data.scope.business_units.length > 1 && !state.bu;

  return (
    <div className="page page-wide dash">
      <header className="page-head">
        <div>
          <h1 className="page-title">Your hiring desk</h1>
          <p className="page-sub">What waits on you, oldest first.</p>
        </div>
      </header>
      <Filters state={state} />
      <div className={state.refreshing ? "dash-body refreshing" : "dash-body"}>
        <div className="stats">
          <StatTile label="Waiting for you" value={k.needs_you} accent={k.needs_you > 0} note={stale ? `${stale} for a week or more` : "Nothing older than a week"} />
          <StatTile label="HR rounds to book, hold or decide" value={rounds} />
          <StatTile label="Your decisions, eight weeks" value={sum(data.mine.by_week)} trend={data.mine.by_week} trendLabel="Your decisions per week" />
        </div>

        <div className="split-2 split-wide-left">
          <NeedsQueue data={data} title="Needs you" caption="Longest wait first." empty="Nothing is waiting on you." />
          <ChartCard
            title="How long it has waited"
            caption="By step and by age. Select a step to see who."
            table={
              <table>
                <thead>
                  <tr>
                    <th>Step</th>
                    {data.ageing.buckets.map((b) => (
                      <th key={b} className="num">
                        {b}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.ageing.rows.map((r) => (
                    <tr key={r.action}>
                      <td>{r.action}</td>
                      {r.buckets.map((n, i) => (
                        <td key={i} className="num">
                          {n}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            }
          >
            <AgeingBars rows={data.ageing.rows} buckets={data.ageing.buckets} onPick={() => navigate("/candidates?flag=needs_me&sort=waiting", { viewTransition: true })} />
          </ChartCard>
        </div>

        <JobRows
          jobs={data.jobs.filter((j) => j.status !== "draft")}
          title="Jobs with work for you"
          caption="Candidates by step. Most waiting first."
          showBu={multiBu}
        />

        <MoreInsight id="hr" hint="Screening scores and your week">
          <div className="viz-grid">
            <ScoresCard data={data} />
            <WeeklyCard data={data} title="Your week" caption="Candidates added against your decisions." decided={data.mine.by_week} decidedLabel="Your decisions" />
          </div>
        </MoreInsight>
      </div>
    </div>
  );
}
