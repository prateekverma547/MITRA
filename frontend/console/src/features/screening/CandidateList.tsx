import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS, STAGE_TONES, type CandidateSummary, type JobCandidates } from "../../contracts";
import { ScoreMeter } from "./ScoreMeter";

type Filter = "all" | "needs_me" | "below";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "needs_me", label: "Waiting for a person" },
  { id: "below", label: "Below the cut-off or unclear" },
];

function screeningCell(c: CandidateSummary) {
  if (c.screening_status === "waiting") return <span className="muted">CV not confirmed yet</span>;
  if (c.screening_status === "running") return <span className="muted">Screening now</span>;
  if (c.screening_status === "failed") return <span className="error">Screening failed</span>;
  return <ScoreMeter score={c.score_percent ?? 0} threshold={c.threshold_percent} enoughEvidence={c.enough_evidence} compact />;
}

export function CandidateList() {
  const { jobId } = useParams();
  const [data, setData] = useState<JobCandidates | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    api
      .get<JobCandidates>(`/jobs/${jobId}/candidates`)
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [jobId]);

  const shown = useMemo(() => {
    if (!data) return [];
    if (filter === "below") return data.candidates.filter((c) => c.below_threshold);
    if (filter === "needs_me") {
      return data.candidates.filter((c) => STAGE_TONES[c.stage] === "attention" || c.screening_status === "waiting");
    }
    return data.candidates;
  }, [data, filter]);

  if (error) {
    return (
      <div className="page">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <div className="page muted">Loading…</div>;

  return (
    <div className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to={`/jobs/${data.job.id}`}>{data.job.title}</Link>
      </nav>
      <header className="page-head">
        <div>
          <h1 className="page-title">Candidates</h1>
          <p className="page-sub">
            {data.job.title}, {data.job.bu_name}. The screening cut-off is {data.threshold_percent}%.
          </p>
        </div>
        {data.can_add && data.job.accepting && (
          <Link className="btn btn-primary" to={`/jobs/${data.job.id}/candidates/new`}>
            Add candidate
          </Link>
        )}
      </header>

      {data.can_add && !data.job.accepting && (
        <p className="notice notice-attention">Its BU head is revising this job request. You can add candidates once it is approved again.</p>
      )}

      {data.candidates.length === 0 ? (
        <div className="empty">
          <p className="empty-title">No candidates yet</p>
          {data.can_add && <p className="muted">Add a candidate with their CV. MITRA screens it against what the BU head asked for.</p>}
        </div>
      ) : (
        <>
          <div className="segmented" role="group" aria-label="Show">
            {FILTERS.map((f) => (
              <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
          {shown.length === 0 ? (
            <p className="muted">Nobody here matches that filter.</p>
          ) : (
            <div className="table-wrap">
              <table className="table table-fixed">
                <colgroup>
                  <col className="col-job" />
                  <col className="col-stage" />
                  <col />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Candidate</th>
                    <th scope="col">Where they stand</th>
                    <th scope="col">Screening</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <Link className="row-title" to={`/candidates/${c.id}`}>
                          {c.name}
                        </Link>
                        {c.created_at && <span className="row-sub">Added {new Date(c.created_at).toLocaleDateString()}</span>}
                      </td>
                      <td>
                        <span className={`mark tone-${STAGE_TONES[c.stage]}`}>{STAGE_LABELS[c.stage]}</span>
                      </td>
                      <td>{screeningCell(c)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
