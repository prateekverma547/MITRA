// The AI usage register: which model and which prompt version produced every
// score, question set and recommendation. Read from the outputs themselves,
// so it cannot disagree with them.

import { ListFilter, X } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { Pager, TransparencyHead, day, useFetch, useFilters, when } from "./common";
import type { Register as RegisterData, RegisterGroup } from "./types";
import "./transparency.css";

const NOT_RECORDED = "not recorded";

function Cell({ value }: { value: string }) {
  return value === NOT_RECORDED ? <span className="tx-missing">Not recorded</span> : <code className="tx-code">{value}</code>;
}

export function Register() {
  const { params, set, clear, page } = useFilters();
  const purpose = params.get("purpose");
  const model = params.get("model");
  const version = params.get("prompt_version");
  const query = new URLSearchParams({ page: String(page), page_size: "25" });
  if (purpose) query.set("purpose", purpose);
  if (model) query.set("model", model);
  if (version) query.set("prompt_version", version);
  const { data, error, refreshing } = useFetch<RegisterData>(`/transparency/ai?${query}`);
  const sub = "Every model output behind a score, a question set or a recommendation, with the model and the prompt version that produced it.";

  if (error && !data) {
    return (
      <div className="page page-wide">
        <TransparencyHead sub={sub} />
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <PageSkeleton rows={6} />;

  const picked = (g: RegisterGroup) => g.purpose === purpose && g.model === model && g.prompt_version === version;
  const drilled = Boolean(purpose || model || version);
  const label = data.register.find(picked)?.purpose_label;

  return (
    <div className="page page-wide dash">
      <TransparencyHead sub={sub} />

      {data.gaps.map((g) => (
        <p key={g} className="notice notice-attention">
          {g}
        </p>
      ))}

      <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
        <section className="card-viz">
          <header className="card-viz-head">
            <div>
              <h2 className="card-title">The register</h2>
              <p className="card-caption">One row for each purpose, model and prompt version in use. Select a row to see every output it produced.</p>
            </div>
          </header>
          {data.register.length === 0 ? (
            <p className="empty-note">No model output is on record yet.</p>
          ) : (
            <div className="table-wrap tx-flat">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Purpose</th>
                    <th scope="col">Model</th>
                    <th scope="col">Prompt version</th>
                    <th scope="col" className="num">
                      Outputs
                    </th>
                    <th scope="col">First used</th>
                    <th scope="col">Last used</th>
                    <th scope="col">
                      <span className="visually-hidden">Show</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.register.map((g) => (
                    <tr key={`${g.purpose}|${g.model}|${g.prompt_version}`} className={picked(g) ? "tx-picked" : undefined}>
                      <td className="row-title">{g.purpose_label}</td>
                      <td>
                        <Cell value={g.model} />
                      </td>
                      <td>
                        <Cell value={g.prompt_version} />
                      </td>
                      <td className="num">{g.count}</td>
                      <td>{day(g.first_used)}</td>
                      <td>{day(g.last_used)}</td>
                      <td className="tx-row-act">
                        <Button
                          size="sm"
                          variant={picked(g) ? "primary" : "ghost"}
                          icon={<ListFilter size={14} />}
                          aria-pressed={picked(g)}
                          onClick={() =>
                            picked(g) ? clear() : set({ purpose: g.purpose, model: g.model, prompt_version: g.prompt_version })
                          }
                        >
                          {picked(g) ? "Showing" : "Show outputs"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card-viz">
          <header className="card-viz-head">
            <div>
              <h2 className="card-title">{drilled && label ? `${label} outputs` : "Every output, newest first"}</h2>
              <p className="card-caption">
                {drilled ? `${data.total} produced by this model and prompt version.` : `${data.total} on record.`}
              </p>
            </div>
            {drilled && (
              <div className="card-actions">
                <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={clear}>
                  Show all
                </Button>
              </div>
            )}
          </header>
          {data.items.length === 0 ? (
            <p className="empty-note">Nothing here.</p>
          ) : (
            <div className="table-wrap tx-flat">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Purpose</th>
                    <th scope="col">For</th>
                    <th scope="col">Output</th>
                    <th scope="col">Model and prompt</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((u, i) => (
                    <tr key={i}>
                      <td className="tx-when">{when(u.at)}</td>
                      <td>{u.purpose_label}</td>
                      <td>
                        {u.candidate ? (
                          <Link className="row-title" viewTransition to={`/candidates/${u.candidate.id}`}>
                            {u.candidate.name}
                          </Link>
                        ) : u.job ? (
                          <Link className="row-title" viewTransition to={`/jobs/${u.job.id}`}>
                            {u.job.title}
                          </Link>
                        ) : null}
                        {u.candidate && u.job && <span className="row-sub">{u.job.title}</span>}
                      </td>
                      <td>{u.subject}</td>
                      <td>
                        <span className="tx-pair">
                          <Cell value={u.model} />
                          <Cell value={u.prompt_version} />
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pager total={data.total} page={data.page} pageSize={data.page_size} onPage={(p) => set({ page: String(p) }, false)} />
        </section>
      </div>
    </div>
  );
}
