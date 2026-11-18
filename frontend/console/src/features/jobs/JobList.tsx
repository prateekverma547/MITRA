import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { requestPhase, type JobRequest } from "../../contracts";
import { useSession } from "../../session";

export function JobList() {
  const { me } = useSession();
  const [jobs, setJobs] = useState<JobRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isBuHead = me?.role === "bu_head";

  useEffect(() => {
    api
      .get<JobRequest[]>("/jobs")
      .then(setJobs)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Job requests</h1>
          <p className="page-sub">{me?.bu_name ? `Everything ${me.bu_name} has asked to hire for.` : "Every business unit."}</p>
        </div>
        {isBuHead && (
          <Link className="btn btn-primary" to="/jobs/new">
            New job request
          </Link>
        )}
      </header>

      {error && <p className="error">{error}</p>}
      {!jobs && !error && <p className="muted">Loading…</p>}
      {jobs && jobs.length === 0 && (
        <div className="empty">
          <p className="empty-title">No job requests yet</p>
          {isBuHead && (
            <p className="muted">
              Start with a title. Upload a job description if you have one, or describe the role to MITRA in the chat.
            </p>
          )}
        </div>
      )}
      {jobs && jobs.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Where it stands</th>
                <th scope="col" className="num">
                  Candidates
                </th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => {
                const phase = requestPhase(job);
                return (
                  <tr key={job.id}>
                    <td>
                      <Link className="row-title" to={`/jobs/${job.id}`}>
                        {job.title}
                      </Link>
                      {!isBuHead && job.bu_name && <span className="row-sub">{job.bu_name}</span>}
                    </td>
                    <td>
                      <span className={`mark tone-${phase.tone}`}>{phase.label}</span>
                    </td>
                    <td className="num">{job.candidates}</td>
                    <td className="muted">{job.created_at ? new Date(job.created_at).toLocaleDateString() : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
