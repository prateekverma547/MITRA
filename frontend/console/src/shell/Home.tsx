// The first screen after signing in. One screen, three readings of the same
// scoped data from /api/home: the server has already decided what this person
// may see, so nothing here filters by role for safety, only for layout.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import {
  CANDIDATE_STAGES,
  STAGE_LABELS,
  STAGE_TONES,
  type Home as HomeData,
  type HomeBusinessUnit,
  type HomeJob,
} from "../contracts";

export function StageMarks({ counts }: { counts: HomeJob["candidates_by_stage"] }) {
  const present = CANDIDATE_STAGES.filter((stage) => (counts[stage] ?? 0) > 0);
  if (present.length === 0) return <span className="muted">No candidates yet</span>;
  return (
    <ul className="marks" aria-label="Candidates by stage">
      {present.map((stage) => (
        <li key={stage} className={`mark tone-${STAGE_TONES[stage]}`}>
          <span className="mark-count">{counts[stage]}</span> {STAGE_LABELS[stage]}
        </li>
      ))}
    </ul>
  );
}

function requestLabel(status: string) {
  return status === "draft" ? "Draft" : status === "closed" ? "Closed" : "Open";
}

function JobTable({ jobs, hr }: { jobs: HomeJob[]; hr: boolean }) {
  if (jobs.length === 0) return <p className="muted">No job requests in this unit yet.</p>;
  return (
    <div className="table-wrap">
      <table className="table table-fixed">
        <colgroup>
          <col className="col-job" />
          <col className="col-request" />
          <col />
        </colgroup>
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
              <td>
                <Link className="row-title" to={hr ? `/jobs/${job.id}/candidates` : `/jobs/${job.id}`}>
                  {job.title}
                </Link>
              </td>
              <td>
                <span className={`mark tone-${job.request_status === "open" ? "good" : job.request_status === "draft" ? "attention" : "closed"}`}>
                  {requestLabel(job.request_status)}
                </span>
              </td>
              <td>
                <StageMarks counts={job.candidates_by_stage} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BuSection({ bu, hr }: { bu: HomeBusinessUnit; hr: boolean }) {
  const id = `bu-${bu.id ?? "none"}`;
  return (
    <section className="section" aria-labelledby={id}>
      <h2 id={id} className="section-title">
        {bu.name}
        <span className="section-count">
          {bu.jobs.length} {bu.jobs.length === 1 ? "job" : "jobs"}
        </span>
      </h2>
      <JobTable jobs={bu.jobs} hr={hr} />
    </section>
  );
}

/** Units with nothing in them yet, named in one line rather than as empty sections. */
function QuietUnits({ units }: { units: HomeBusinessUnit[] }) {
  if (units.length === 0) return null;
  const names = units.map((bu) => bu.name);
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return <p className="quiet-units">No job requests yet from {list}.</p>;
}

const WAITING = CANDIDATE_STAGES.filter((stage) => STAGE_TONES[stage] === "attention");

function total(data: HomeData, pick: (job: HomeJob) => number): number {
  return data.business_units.reduce((sum, bu) => sum + bu.jobs.reduce((s, job) => s + pick(job), 0), 0);
}

function Ledger({ data }: { data: HomeData }) {
  const open = total(data, (job) => (job.request_status === "open" ? 1 : 0));
  const people = total(data, (job) => Object.values(job.candidates_by_stage).reduce((s: number, n) => s + (n ?? 0), 0));
  const waiting = total(data, (job) => WAITING.reduce((s, stage) => s + (job.candidates_by_stage[stage] ?? 0), 0));
  return (
    <dl className="ledger">
      <div>
        <dt>{open === 1 ? "Open job request" : "Open job requests"}</dt>
        <dd>{open}</dd>
      </div>
      <div>
        <dt>{people === 1 ? "Candidate" : "Candidates"}</dt>
        <dd>{people}</dd>
      </div>
      <div>
        <dt>Waiting for a person</dt>
        <dd>{waiting}</dd>
      </div>
    </dl>
  );
}

const TITLES = {
  bu_head: (bu: string | null) => `${bu ?? "Your"} hiring`,
  hr: () => "Hiring across the organisation",
  org_admin: () => "Organisation overview",
};

const SUBTITLES = {
  bu_head: "Your job requests and the candidates in them.",
  hr: "Every approved job request, by business unit.",
  org_admin: "Read-only. Every business unit, job and candidate.",
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
      <div className="page">
        <h1 className="page-title">Home</h1>
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <div className="page muted">Loading…</div>;

  const { me } = data;
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">{TITLES[me.role](me.bu_name)}</h1>
          <p className="page-sub">{SUBTITLES[me.role]}</p>
        </div>
        {me.role === "bu_head" && (
          <Link className="btn btn-primary" to="/jobs/new">
            New job request
          </Link>
        )}
      </header>
      <Ledger data={data} />
      {data.business_units.length === 0 && <p className="muted">No business units yet.</p>}
      {data.business_units
        .filter((bu) => bu.jobs.length > 0 || me.role === "bu_head")
        .map((bu) => (
          <BuSection key={bu.id ?? "unassigned"} bu={bu} hr={me.role === "hr"} />
        ))}
      <QuietUnits units={me.role === "bu_head" ? [] : data.business_units.filter((bu) => bu.jobs.length === 0)} />
    </div>
  );
}
