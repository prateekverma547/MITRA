// The first screen after signing in. One screen, three readings of the same
// scoped data from /api/home: the server has already decided what this person
// may see, so nothing here filters by role for safety, only for layout.

import { useEffect, useState } from "react";
import { api } from "../api";
import {
  CANDIDATE_STAGES,
  STAGE_LABELS,
  STAGE_TONES,
  type CandidateStage,
  type Home as HomeData,
  type HomeBusinessUnit,
  type HomeJob,
} from "../contracts";

function StageChips({ counts }: { counts: HomeJob["candidates_by_stage"] }) {
  const present = CANDIDATE_STAGES.filter((stage) => (counts[stage] ?? 0) > 0);
  if (present.length === 0) return <span className="muted small">No candidates yet</span>;
  return (
    <ul className="chips" aria-label="Candidates by stage">
      {present.map((stage: CandidateStage) => (
        <li key={stage} className={`chip tone-${STAGE_TONES[stage]}`}>
          <span className="chip-count">{counts[stage]}</span> {STAGE_LABELS[stage]}
        </li>
      ))}
    </ul>
  );
}

function RequestStatus({ status }: { status: string }) {
  const label = status === "draft" ? "Draft" : status === "closed" ? "Closed" : "Open";
  return <span className={`status status-${status}`}>{label}</span>;
}

function JobTable({ jobs }: { jobs: HomeJob[] }) {
  if (jobs.length === 0) return <p className="muted empty">No job requests yet.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Job</th>
            <th scope="col">Request</th>
            <th scope="col">Candidates</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td className="job-title">{job.title}</td>
              <td>
                <RequestStatus status={job.request_status} />
              </td>
              <td>
                <StageChips counts={job.candidates_by_stage} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BuSection({ bu }: { bu: HomeBusinessUnit }) {
  return (
    <section className="bu" aria-labelledby={`bu-${bu.id ?? "none"}`}>
      <h2 id={`bu-${bu.id ?? "none"}`}>
        {bu.name} <span className="muted small">{bu.jobs.length} {bu.jobs.length === 1 ? "job" : "jobs"}</span>
      </h2>
      <JobTable jobs={bu.jobs} />
    </section>
  );
}

function count(data: HomeData, pick: (job: HomeJob) => number): number {
  return data.business_units.reduce((sum, bu) => sum + bu.jobs.reduce((s, job) => s + pick(job), 0), 0);
}

const ATTENTION = CANDIDATE_STAGES.filter((stage) => STAGE_TONES[stage] === "attention");

function Summary({ data }: { data: HomeData }) {
  const openJobs = count(data, (job) => (job.request_status === "open" ? 1 : 0));
  const candidates = count(data, (job) =>
    Object.values(job.candidates_by_stage).reduce((s: number, n) => s + (n ?? 0), 0),
  );
  const waiting = count(data, (job) => ATTENTION.reduce((s, stage) => s + (job.candidates_by_stage[stage] ?? 0), 0));
  return (
    <dl className="tiles">
      <div className="tile">
        <dt>Open job requests</dt>
        <dd>{openJobs}</dd>
      </div>
      <div className="tile">
        <dt>Candidates</dt>
        <dd>{candidates}</dd>
      </div>
      <div className="tile">
        <dt>Waiting for a person</dt>
        <dd>{waiting}</dd>
      </div>
    </dl>
  );
}

const TITLES = {
  bu_head: (bu: string | null) => `${bu ?? "Your"} hiring`,
  hr: () => "Hiring across business units",
  org_admin: () => "Organisation overview",
};

export function Home() {
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<HomeData>("/home")
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return (
      <section className="page">
        <h1>Home</h1>
        <p className="error">{error}</p>
      </section>
    );
  }
  if (!data) return <section className="page muted">Loading…</section>;

  const { me } = data;
  return (
    <section className="page">
      <header className="page-header">
        <h1>{TITLES[me.role](me.bu_name)}</h1>
        {me.role === "org_admin" && <p className="muted">Read-only. Every BU, every job, every candidate.</p>}
      </header>
      <Summary data={data} />
      {data.business_units.length === 0 ? (
        <p className="muted empty">No business units yet.</p>
      ) : (
        data.business_units.map((bu) => <BuSection key={bu.id ?? "unassigned"} bu={bu} />)
      )}
    </section>
  );
}
