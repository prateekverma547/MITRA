import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { requestPhase, type JobRequest } from "../../contracts";
import { useSession } from "../../session";

export function JobList() {
  const { me } = useSession();
  const [jobs, setJobs] = useState<JobRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<JobRequest[]>("/jobs")
      .then(setJobs)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <section className="page">
      <header className="page-header row">
        <div>
          <h1>Job requests</h1>
          <p className="muted">{me?.bu_name ? `${me.bu_name} business unit` : "All business units"}</p>
        </div>
        {me?.role === "bu_head" && (
          <Link className="button primary" to="/jobs/new">
            New job request
          </Link>
        )}
      </header>

      {error && <p className="error">{error}</p>}
      {!jobs && !error && <p className="muted">Loading…</p>}
      {jobs && jobs.length === 0 && (
        <div className="empty-card">
          <p>No job requests yet.</p>
          {me?.role === "bu_head" && (
            <p className="muted">
              Start one with a title. Upload a job description if you have one, or describe the role to MITRA in the
              chat.
            </p>
          )}
        </div>
      )}
      {jobs && jobs.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Where it stands</th>
                <th scope="col">Candidates</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => {
                const phase = requestPhase(job);
                return (
                  <tr key={job.id}>
                    <td className="job-title">
                      <Link to={`/jobs/${job.id}`}>{job.title}</Link>
                      {me?.role !== "bu_head" && job.bu_name && <div className="muted small">{job.bu_name}</div>}
                    </td>
                    <td>
                      <span className={`chip tone-${phase.tone}`}>{phase.label}</span>
                    </td>
                    <td className="num">{job.candidates}</td>
                    <td className="muted small">{job.created_at ? new Date(job.created_at).toLocaleDateString() : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
