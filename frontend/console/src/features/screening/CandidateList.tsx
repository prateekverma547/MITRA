import { Columns3, GitCompareArrows, Rows3, UserPlus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import {
  GROUPS,
  STAGE_LABELS,
  STAGE_TONES,
  groupOf,
  initials,
  type CandidateSummary,
  type JobCandidates,
} from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { ButtonLink } from "../../shell/ui/Button";
import { RowLink } from "../../shell/ui/RowLink";
import { PageSkeleton } from "../../shell/ui/Skeleton";
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
  const [view, setView] = useState<"table" | "board">(() => {
    try {
      return (localStorage.getItem("mitra.candidates.view") as "table" | "board") || "table";
    } catch {
      return "table";
    }
  });
  function choose(v: "table" | "board") {
    setView(v);
    try {
      localStorage.setItem("mitra.candidates.view", v);
    } catch {
      // Private windows refuse storage; the choice just will not stick.
    }
  }

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
  if (!data) return <PageSkeleton />;

  return (
    <div className="page">
      <BackButton fallback="/jobs" label="Job requests" />
      <header className="page-head">
        <div>
          <h1 className="page-title">Candidates</h1>
          <p className="page-sub">
            {data.job.title}, {data.job.bu_name}. The screening cut-off is {data.threshold_percent}%.
          </p>
        </div>
        <div className="actions">
          {data.candidates.some((c) => c.stage !== "new") && (
            <ButtonLink to={`/jobs/${data.job.id}/compare`} icon={<GitCompareArrows size={17} />}>
              Compare candidates
            </ButtonLink>
          )}
          {data.can_add && data.job.accepting && (
            <ButtonLink variant="primary" to={`/jobs/${data.job.id}/candidates/new`} icon={<UserPlus size={17} />}>
              Add candidate
            </ButtonLink>
          )}
        </div>
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
          <div className="filters">
            <div className="segmented" role="group" aria-label="Show">
              {FILTERS.map((f) => (
                <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
                  {f.label}
                </button>
              ))}
            </div>
            <div className="segmented view-toggle" role="group" aria-label="Layout">
              <button type="button" aria-pressed={view === "table"} onClick={() => choose("table")}>
                <Rows3 size={14} aria-hidden="true" /> Table
              </button>
              <button type="button" aria-pressed={view === "board"} onClick={() => choose("board")}>
                <Columns3 size={14} aria-hidden="true" /> Board
              </button>
            </div>
          </div>
          {shown.length === 0 ? (
            <p className="muted">Nobody here matches that filter.</p>
          ) : view === "board" ? (
            <Board candidates={shown} />
          ) : (
            <div className="table-wrap">
              <table className="table table-fixed">
                <colgroup>
                  <col className="col-job" />
                  <col className="col-stage" />
                  <col />
                  <col className="col-go" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Candidate</th>
                    <th scope="col">Where they stand</th>
                    <th scope="col">Screening</th>
                    <th className="row-go-head" aria-hidden="true" />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((c) => (
                    <RowLink key={c.id} to={`/candidates/${c.id}`}>
                      <td>
                        <Link className="row-title" viewTransition to={`/candidates/${c.id}`}>
                          {c.name}
                        </Link>
                        {c.created_at && <span className="row-sub">Added {new Date(c.created_at).toLocaleDateString()}</span>}
                      </td>
                      <td>
                        <span className={`mark tone-${STAGE_TONES[c.stage]}`}>{STAGE_LABELS[c.stage]}</span>
                      </td>
                      <td>{screeningCell(c)}</td>
                    </RowLink>
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


/** Columns by step, for seeing a job's whole pipeline at once. Read-only:
 * moving someone happens on their page, where reasons and rules apply. */
function Board({ candidates }: { candidates: CandidateSummary[] }) {
  return (
    <div className="board">
      {GROUPS.map((g) => {
        const cards = candidates.filter((c) => groupOf(c.stage) === g.key);
        return (
          <section key={g.key} className="board-col" aria-label={g.label}>
            <header className="board-head">
              <span className={`swatch seg-${g.key}`} aria-hidden="true" />
              <span>{g.label}</span>
              <span className="board-n">{cards.length}</span>
            </header>
            <div className="board-cards">
              {cards.length === 0 && <span className="board-empty">Nobody</span>}
              {cards.map((c) => (
                <Link key={c.id} to={`/candidates/${c.id}`} viewTransition className="board-card">
                  <span className="board-card-head">
                    <span className="avatar avatar-sm" aria-hidden="true">
                      {initials(c.name)}
                    </span>
                    <span className="board-name">{c.name}</span>
                  </span>
                  <span className={`mark tone-${STAGE_TONES[c.stage]}`}>{STAGE_LABELS[c.stage]}</span>
                  {c.score_percent !== null && (
                    <ScoreMeter score={c.score_percent} threshold={c.threshold_percent} enoughEvidence={c.enough_evidence} compact />
                  )}
                </Link>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
