// A BU head's home is about their own roles: the decisions that are theirs to
// make, how strong the field is for each job, and how each job is moving.

import { Plus, Scale } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { ButtonLink } from "../ui/Button";
import { MoreInsight } from "../ui/MoreInsight";
import { ChartCard } from "../viz/ChartCard";
import { ScoreStrip } from "../viz/ScoreStrip";
import { StatTile } from "../viz/StatTile";
import { Filters, JobRows, NeedsQueue, type OverviewState } from "./common";

const OUTCOMES = [
  { stage: "shortlisted", label: "Shortlisted" },
  { stage: "on_hold", label: "On hold" },
  { stage: "rejected", label: "Rejected" },
] as const;

export function BUHome({ state, buName }: { state: OverviewState; buName: string | null }) {
  const navigate = useNavigate();
  const data = state.data!;
  const k = data.kpis;
  const live = data.jobs.filter((j) => j.status !== "draft");
  const decided = OUTCOMES.map((o) => ({ ...o, n: data.mine.outcomes[o.stage] ?? 0 }));
  const decidedTotal = decided.reduce((s, o) => s + o.n, 0);
  const inFinal = data.pipeline.find((p) => p.key === "bu_head")?.count ?? 0;

  return (
    <div className="page page-wide dash">
      <header className="page-head">
        <div>
          <h1 className="page-title">{buName ?? "Your"} hiring</h1>
          <p className="page-sub">The decisions that are yours, and where each of your job requests stands.</p>
        </div>
        <ButtonLink variant="primary" to="/jobs/new" icon={<Plus size={17} />}>
          New job request
        </ButtonLink>
      </header>
      <Filters state={state} showBu={false} />
      <div className={state.refreshing ? "dash-body refreshing" : "dash-body"}>
        <div className="stats">
          <StatTile label="Waiting for your decision" value={k.needs_you} accent={k.needs_you > 0} note={k.needs_you ? "After the MITRA interview round" : "Nothing is waiting on you"} />
          <StatTile label="Open job requests" value={k.open_jobs} note={k.draft_jobs ? `${k.draft_jobs} still being described` : undefined} />
          <StatTile label="Shortlisted by you" value={data.mine.outcomes.shortlisted ?? 0} note={`${inFinal} with you or on hold`} />
        </div>

        <div className="split-2">
          <NeedsQueue data={data} title="Your decisions to make" caption="Candidates HR sent you after the MITRA interview round." empty="Nothing is waiting on you. HR sends candidates here after the MITRA interview round." />
        <JobRows
          jobs={data.jobs}
          title="Your job requests"
          caption="Each request's candidates by step. Open one to see them or compare them."
          showBu={false}
          extra={(j) =>
            j.total > 1 ? (
              <span className="row-hint">
                <Scale size={14} aria-hidden="true" /> Compare
              </span>
            ) : null
          }
        />
        </div>

        <MoreInsight id="bu" hint="How strong each field is, and what you decided">
        <ChartCard
          title="How strong is each field"
          caption="CV fit of everyone screened, job by job. Select a dot to open them."
          table={
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Candidate</th>
                  <th className="num">Fit</th>
                  <th>Step</th>
                </tr>
              </thead>
              <tbody>
                {live.flatMap((j) =>
                  j.scores.map((s) => (
                    <tr key={s.candidate_id}>
                      <td>{j.title}</td>
                      <td>{s.name}</td>
                      <td className="num">{Math.round(s.score)}%</td>
                      <td>{s.group}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          }
        >
          <ScoreStrip rows={live} onPick={(id) => navigate(`/candidates/${id}`, { viewTransition: true })} />
        </ChartCard>
        <ChartCard
          title="What you decided"
          caption={decidedTotal ? "Your decisions in this period." : "You have not decided on anyone in this period."}
          table={
            <table>
              <thead>
                <tr>
                  <th>Decision</th>
                  <th className="num">Candidates</th>
                </tr>
              </thead>
              <tbody>
                {decided.map((o) => (
                  <tr key={o.stage}>
                    <td>{o.label}</td>
                    <td className="num">{o.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
        >
          <ol className="steptime">
            {decided.map((o) => (
              <li key={o.stage} className="funnel-row">
                <span className="funnel-label">{o.label}</span>
                <span className="funnel-track">
                  {o.n > 0 && <span className={`steptime-bar outcome-${o.stage}`} style={{ width: `${(o.n / Math.max(1, ...decided.map((d) => d.n))) * 100}%` }} />}
                </span>
                <span className="funnel-count">{o.n}</span>
              </li>
            ))}
          </ol>
        </ChartCard>
        </MoreInsight>
      </div>
    </div>
  );
}
