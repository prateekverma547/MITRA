// Job requests. A BU head sees their own unit's; HR and admins see every unit,
// grouped by business unit. Units with something new or something to do come
// first and are marked; units with no requests are listed in one line.

import { Info, Plus, Sparkles, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { requestPhase, type JobRequest } from "../../contracts";
import { useSession } from "../../session";
import { ButtonLink } from "../../shell/ui/Button";
import { RowLink } from "../../shell/ui/RowLink";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import "./jobs.css";

/** Raised in the last few days: still news to HR. */
const NEW_DAYS = 3;

function isNew(job: JobRequest): boolean {
  if (!job.created_at) return false;
  return Date.now() - new Date(job.created_at).getTime() < NEW_DAYS * 86_400_000;
}

/** With HR but nobody added yet: HR's next move. */
function needsCandidates(job: JobRequest): boolean {
  return job.request_status === "open" && job.candidates === 0;
}

interface Unit {
  id: string;
  name: string;
  jobs: JobRequest[];
  fresh: number;
  waiting: number;
  candidates: number;
  latest: number;
}

function JobsTable({ jobs, role, showUnit }: { jobs: JobRequest[]; role: string | undefined; showUnit: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Job</th>
            <th scope="col">Where it stands</th>
            <th scope="col" className="num">
              Candidates
            </th>
            <th scope="col">Raised</th>
            <th className="row-go-head" aria-hidden="true" />
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const phase = requestPhase(job);
            const to = role === "hr" ? `/jobs/${job.id}/candidates` : `/jobs/${job.id}`;
            return (
              <RowLink key={job.id} to={to}>
                <td>
                  <span className="job-cell">
                    <Link className="row-title" viewTransition to={to}>
                      {job.title}
                    </Link>
                    {isNew(job) && <span className="tag-new">New</span>}
                  </span>
                  {showUnit && job.bu_name && <span className="row-sub">{job.bu_name}</span>}
                </td>
                <td>
                  <span className={`mark tone-${phase.tone}`}>{phase.label}</span>
                  {role === "hr" && needsCandidates(job) && <span className="row-sub needs">Add the first candidates</span>}
                </td>
                <td className="num">{job.candidates}</td>
                <td className="muted">{job.created_at ? new Date(job.created_at).toLocaleDateString() : ""}</td>
              </RowLink>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const GUIDE: Record<string, string> = {
  hr: "Requests arrive here once a BU head sends them. Open one to add candidates and run its rounds. Units with something new or waiting on you are at the top.",
  org_admin: "Every request in the organisation, by business unit. Open one to read what MITRA understood and where its candidates are.",
  bu_head: "Raise a request, describe the role to MITRA, then send it to HR. A draft stays with you until you send it, and can be deleted until then.",
};

export function JobList() {
  const { me } = useSession();
  const [jobs, setJobs] = useState<JobRequest[] | null>(null);
  const [units, setUnits] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const isBuHead = me?.role === "bu_head";

  useEffect(() => {
    api
      .get<JobRequest[]>("/jobs")
      .then(setJobs)
      .catch((e: Error) => setError(e.message));
    api
      .get<{ id: string; name: string }[]>("/business-units")
      .then(setUnits)
      .catch(() => setUnits([]));
  }, []);

  // One group per business unit; the ones with something new or to do first.
  const groups: Unit[] = [];
  if (jobs && !isBuHead) {
    const byId = new Map<string, Unit>();
    for (const job of jobs) {
      const key = job.bu_id ?? "none";
      let unit = byId.get(key);
      if (!unit) {
        unit = { id: key, name: job.bu_name ?? "No business unit", jobs: [], fresh: 0, waiting: 0, candidates: 0, latest: 0 };
        byId.set(key, unit);
        groups.push(unit);
      }
      unit.jobs.push(job);
      unit.candidates += job.candidates;
      if (isNew(job)) unit.fresh += 1;
      if (me?.role === "hr" && needsCandidates(job)) unit.waiting += 1;
      unit.latest = Math.max(unit.latest, job.created_at ? new Date(job.created_at).getTime() : 0);
    }
    groups.sort((a, b) => Number(b.fresh + b.waiting > 0) - Number(a.fresh + a.waiting > 0) || b.latest - a.latest);
  }
  const quiet = units.filter((u) => !groups.some((g) => g.id === u.id));

  return (
    <div className="page page-wide">
      <header className="page-head">
        <div>
          <h1 className="page-title">Job requests</h1>
          <p className="page-sub">{me?.bu_name ? `Everything ${me.bu_name} has asked to hire for.` : "Every business unit, grouped."}</p>
        </div>
        {isBuHead && (
          <ButtonLink variant="primary" to="/jobs/new" icon={<Plus size={17} />}>
            New job request
          </ButtonLink>
        )}
      </header>

      {me && GUIDE[me.role] && (
        <p className="page-guide">
          <Info size={16} aria-hidden="true" />
          {GUIDE[me.role]}
        </p>
      )}

      {error && <p className="error">{error}</p>}
      {!jobs && !error && <PageSkeleton rows={3} />}
      {jobs && jobs.length === 0 && (
        <div className="empty">
          <p className="empty-title">No job requests yet</p>
          <p className="muted">
            {isBuHead
              ? "Start with a title. Upload a job description if you have one, or describe the role to MITRA in the chat."
              : "Requests appear here when a BU head sends one to HR."}
          </p>
        </div>
      )}

      {jobs && jobs.length > 0 && isBuHead && <JobsTable jobs={jobs} role={me?.role} showUnit={false} />}

      {jobs && jobs.length > 0 && !isBuHead && (
        <div className="bu-groups">
          {groups.map((g) => {
            const hot = g.fresh + g.waiting > 0;
            return (
              <section key={g.id} className={hot ? "bu-group hot" : "bu-group"} aria-labelledby={`bu-${g.id}`}>
                <header className="bu-group-head">
                  <h2 id={`bu-${g.id}`}>{g.name}</h2>
                  <span className="bu-group-count">
                    {g.jobs.length} {g.jobs.length === 1 ? "request" : "requests"}, {g.candidates} {g.candidates === 1 ? "candidate" : "candidates"}
                  </span>
                  {g.fresh > 0 && (
                    <span className="bu-flag new">
                      <Sparkles size={13} aria-hidden="true" /> {g.fresh} new
                    </span>
                  )}
                  {g.waiting > 0 && (
                    <span className="bu-flag todo">
                      <UserPlus size={13} aria-hidden="true" /> {g.waiting} {g.waiting === 1 ? "needs" : "need"} candidates
                    </span>
                  )}
                </header>
                <JobsTable jobs={g.jobs} role={me?.role} showUnit={false} />
              </section>
            );
          })}
          {quiet.length > 0 && (
            <p className="bu-quiet">
              <strong>No requests yet from:</strong> {quiet.map((u) => u.name).join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
