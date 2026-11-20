// Every candidate on one job, side by side, on the job's own yardstick. The
// table is the chart: each rating cell carries its number and a single-hue
// shade (darker is stronger), and "no evidence" is hatched and says so, so
// nothing depends on telling shades apart. Pick two to four candidates to
// compare them closely.

import { Columns3, Eye, EyeOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { CANDIDATE_STAGES, STAGE_LABELS, STAGE_TONES } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button } from "../../shell/ui/Button";
import { RATING_WORDS } from "../../shell/ui/Pips";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { HR_RECOMMENDATION, TECHNICAL_SIGNAL, type Comparison } from "./types";
import "./results.css";

const SORTS = [
  { key: "fit", label: "Fit" },
  { key: "stage", label: "Stage" },
  { key: "name", label: "Name" },
] as const;
type SortKey = (typeof SORTS)[number]["key"];

const MAX_FOCUS = 4;

function RatingCell({ rating, what }: { rating: number | null | undefined; what: string }) {
  if (rating === undefined) {
    return (
      <td className="rc rc-missing">
        <span className="visually-hidden">{what}: </span>Not rated
      </td>
    );
  }
  if (rating === null) {
    return (
      <td className="rc rc-none" aria-label={`${what}: no evidence`}>
        No evidence
      </td>
    );
  }
  return (
    <td className={`rc rc-${rating}`} title={`${rating} of 4: ${RATING_WORDS[rating]}`} aria-label={`${what}: ${rating} of 4, ${RATING_WORDS[rating]}`}>
      {rating}
    </td>
  );
}

export function Compare() {
  const { jobId } = useParams();
  const [params, setParams] = useSearchParams();
  const includeClosed = params.get("closed") === "1";
  const sort = (params.get("sort") as SortKey) || "fit";
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [focus, setFocus] = useState(false);

  useEffect(() => {
    setRefreshing(true);
    api
      .get<Comparison>(`/jobs/${jobId}/compare?include_closed=${includeClosed}`)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [jobId, includeClosed]);

  function set(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  }

  function toggle(id: string) {
    setPicked((all) => (all.includes(id) ? all.filter((x) => x !== id) : all.length < MAX_FOCUS ? [...all, id] : all));
  }

  const columns = useMemo(() => {
    if (!data) return [];
    const rows = [...data.candidates];
    const order = (s: string) => CANDIDATE_STAGES.indexOf(s as (typeof CANDIDATE_STAGES)[number]);
    rows.sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "stage") return order(b.stage) - order(a.stage) || (b.score_percent ?? -1) - (a.score_percent ?? -1);
      return (b.score_percent ?? -1) - (a.score_percent ?? -1);
    });
    return focus && picked.length >= 2 ? rows.filter((c) => picked.includes(c.id)) : rows;
  }, [data, sort, focus, picked]);

  if (error && !data) {
    return (
      <div className="page">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <PageSkeleton />;

  const span = columns.length + 1;
  const anyHR = data.candidates.some((c) => c.hr_round);

  return (
    <div className="page page-wide compare">
      <BackButton fallback={`/jobs/${data.job.id}/candidates`} label="Candidates" />
      <header className="page-head">
        <div>
          <h1 className="page-title">Compare candidates</h1>
          <p className="page-sub">
            {data.job.title}
            {data.job.bu_name ? `, ${data.job.bu_name}` : ""}. Everyone screened, on the same yardstick: the job's competencies, heaviest first.
          </p>
        </div>
      </header>

      <div className="filters" role="group" aria-label="Comparison controls">
        <div className="segmented" role="group" aria-label="Sort by">
          {SORTS.map((s) => (
            <button key={s.key} type="button" aria-pressed={sort === s.key} onClick={() => set("sort", s.key === "fit" ? null : s.key)}>
              {s.label}
            </button>
          ))}
        </div>
        <button type="button" className="chip-btn" aria-pressed={includeClosed} onClick={() => set("closed", includeClosed ? null : "1")}>
          {includeClosed ? "Including closed" : `Include closed${data.hidden.closed ? ` (${data.hidden.closed})` : ""}`}
        </button>
        <span className="compare-pick">
          {picked.length === 0
            ? `Tick two to ${MAX_FOCUS} candidates to compare them closely.`
            : `${picked.length} of ${MAX_FOCUS} picked.`}
        </span>
        {picked.length >= 2 && (
          <Button size="sm" variant={focus ? "secondary" : "primary"} icon={focus ? <EyeOff size={15} /> : <Eye size={15} />} onClick={() => setFocus((f) => !f)}>
            {focus ? "Show everyone" : "Show only those picked"}
          </Button>
        )}
        {picked.length > 0 && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setPicked([]);
              setFocus(false);
            }}
          >
            Clear
          </Button>
        )}
      </div>

      {data.candidates.length === 0 ? (
        <section className="card-viz">
          <p className="empty-note">
            Nobody to compare yet.{" "}
            {data.hidden.not_screened > 0 && `${data.hidden.not_screened} still waiting for screening. `}
            {data.hidden.closed > 0 && !includeClosed && `${data.hidden.closed} closed; include them above.`}
          </p>
        </section>
      ) : (
        <section className={refreshing ? "card-viz compare-card refreshing" : "card-viz compare-card"}>
          <div className="compare-scroll" tabIndex={0} role="region" aria-label="Comparison table, scrolls sideways">
            <table className="compare-table">
              <caption className="visually-hidden">
                Candidates for {data.job.title}: screening fit, each competency rated 0 to 4 from the CV, the HR round and the MITRA interview round.
              </caption>
              <thead>
                <tr>
                  <th scope="col" className="compare-corner">
                    <Columns3 size={16} aria-hidden="true" /> {columns.length} {columns.length === 1 ? "candidate" : "candidates"}
                  </th>
                  {columns.map((c) => (
                    <th key={c.id} scope="col" className={picked.includes(c.id) ? "compare-col picked" : "compare-col"}>
                      <label className="compare-check">
                        <input
                          type="checkbox"
                          checked={picked.includes(c.id)}
                          disabled={!picked.includes(c.id) && picked.length >= MAX_FOCUS}
                          onChange={() => toggle(c.id)}
                          aria-label={`Pick ${c.name} to compare`}
                        />
                      </label>
                      <Link to={`/candidates/${c.id}/results`} viewTransition className="compare-name">
                        {c.name}
                      </Link>
                      <span className={`mark tone-${STAGE_TONES[c.stage]}`}>{STAGE_LABELS[c.stage]}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr className="compare-group">
                  <th scope="rowgroup" colSpan={span}>
                    Screening{data.threshold_percent !== null ? `, cut-off ${data.threshold_percent}%` : ""}
                  </th>
                </tr>
                <tr>
                  <th scope="row">Fit to the job</th>
                  {columns.map((c) => (
                    <td key={c.id} className="fit-cell">
                      {c.score_percent === null ? (
                        <span className="muted">Not screened</span>
                      ) : (
                        <>
                          <span className={c.passes_screen ? "fit-num" : "fit-num below"}>{Math.round(c.score_percent)}%</span>
                          <span className="fit-bar" aria-hidden="true">
                            <span style={{ width: `${c.score_percent}%` }} className={c.passes_screen ? "" : "below"} />
                            {data.threshold_percent !== null && <i style={{ left: `${data.threshold_percent}%` }} />}
                          </span>
                          {!c.enough_evidence && <span className="fit-flag">Too little to judge</span>}
                        </>
                      )}
                    </td>
                  ))}
                </tr>
                {data.competencies.map((comp) => (
                  <tr key={comp.id}>
                    <th scope="row">
                      {comp.name}
                      <span className="row-weight">{Math.round(comp.weight_percent)}% of the job</span>
                    </th>
                    {columns.map((c) => (
                      <RatingCell key={c.id} rating={c.score_percent === null ? undefined : (c.ratings[comp.id] ?? null)} what={`${c.name}, ${comp.name}`} />
                    ))}
                  </tr>
                ))}

                {anyHR && (
                  <>
                    <tr className="compare-group">
                      <th scope="rowgroup" colSpan={span}>
                        HR round
                      </th>
                    </tr>
                    <tr>
                      <th scope="row">MITRA recommended</th>
                      {columns.map((c) => (
                        <td key={c.id} className="text-cell">
                          {c.hr_round ? (
                            <span className={`tone-${HR_RECOMMENDATION[c.hr_round.recommendation].tone}`}>
                              {c.hr_round.recommendation === "proceed_to_technical" ? "Proceed" : c.hr_round.recommendation === "reject" ? "Reject" : "Discuss"}
                            </span>
                          ) : (
                            <span className="muted">{c.hr_skipped ? "Skipped" : "Not evaluated"}</span>
                          )}
                        </td>
                      ))}
                    </tr>
                    {data.soft_skills.map((skill) => (
                      <tr key={skill}>
                        <th scope="row">{skill}</th>
                        {columns.map((c) => (
                          <RatingCell key={c.id} rating={c.hr_round ? (c.hr_round.ratings[skill] ?? null) : undefined} what={`${c.name}, ${skill}`} />
                        ))}
                      </tr>
                    ))}
                  </>
                )}

                <tr className="compare-group">
                  <th scope="rowgroup" colSpan={span}>
                    MITRA interview round
                  </th>
                </tr>
                <tr>
                  <th scope="row">Report</th>
                  {columns.map((c) => (
                    <td key={c.id} className="text-cell">
                      {c.technical?.recommendation ? TECHNICAL_SIGNAL[c.technical.recommendation] : <span className="muted">No report yet</span>}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
          <p className="compare-legend">
            <span className="legend-ramp" aria-hidden="true">
              {[0, 1, 2, 3, 4].map((n) => (
                <span key={n} className={`rc-swatch rc-${n}`}>
                  {n}
                </span>
              ))}
            </span>
            Ratings 0–4; the shade deepens with the number.
            <span className="rc-swatch rc-none legend-none" aria-hidden="true" /> No evidence: the CV or the round said nothing about it, which is not a low score.
          </p>
        </section>
      )}
    </div>
  );
}
