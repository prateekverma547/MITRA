// Every call MITRA makes to a model, newest first: a trace view. Each row
// opens a drawer with what went in (described, never the prompt itself), what
// came out, how long it took and what it cost. backend/hiring/usage.py.

import { Clock, Coins, FileOutput, FileInput, X } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Button } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { Pager, SearchBox, TransparencyHead, useFetch, useFilters, when } from "./common";
import { duration, tokens, usd } from "./money";
import type { ModelCallDetail, ModelCallList, ModelCallRow } from "./types";
import "./transparency.css";

const STATUS: Record<string, { label: string; tone: string }> = {
  ok: { label: "OK", tone: "good" },
  error: { label: "Failed", tone: "closed" },
  schema_error: { label: "Unusable answer", tone: "attention" },
  completed: { label: "OK", tone: "good" },
};

function Status({ value }: { value: string }) {
  const s = STATUS[value] ?? { label: value.replace(/_/g, " "), tone: "attention" };
  return <span className={`mark tone-${s.tone}`}>{s.label}</span>;
}

function label(key: string): string {
  return key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function Drawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: call, error } = useFetch<ModelCallDetail>(`/transparency/calls/${encodeURIComponent(id)}`);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="tx-drawer-wrap" role="presentation" onClick={onClose}>
      <aside className="tx-drawer" role="dialog" aria-modal="true" aria-label="Model call" onClick={(e) => e.stopPropagation()}>
        <header className="tx-drawer-head">
          <div>
            <p className="tx-drawer-kicker">{call ? call.stage_label : "Model call"}</p>
            <h2 className="card-title">{call ? call.purpose : "Loading"}</h2>
          </div>
          <Button size="sm" variant="ghost" icon={<X size={16} />} onClick={onClose} aria-label="Close">
            Close
          </Button>
        </header>
        {error && <p className="error">{error}</p>}
        {call && (
          <div className="tx-drawer-body">
            <div className="tx-drawer-facts">
              <Status value={call.status} />
              <span className="tx-code">{call.model}</span>
              {call.prompt_version && <span className="tx-code">{call.prompt_version}</span>}
              <span className="muted small">{when(call.at)}</span>
            </div>

            <section className="tx-drawer-section">
              <h3>
                <FileInput size={15} aria-hidden="true" /> What went in
              </h3>
              <dl className="tx-dl">
                {call.candidate && (
                  <>
                    <dt>Candidate</dt>
                    <dd>
                      <Link to={`/candidates/${call.candidate.id}`}>{call.candidate.name ?? call.candidate.id}</Link>
                    </dd>
                  </>
                )}
                {call.job && (
                  <>
                    <dt>Job</dt>
                    <dd>
                      <Link to={`/jobs/${call.job.id}`}>{call.job.title ?? call.job.id}</Link>
                    </dd>
                  </>
                )}
                <dt>Input tokens</dt>
                <dd>
                  {tokens(call.input_tokens)}
                  {call.cached_input_tokens ? `, ${tokens(call.cached_input_tokens)} from cache` : ""}
                </dd>
                {call.audio_seconds !== null && (
                  <>
                    <dt>Audio</dt>
                    <dd>{Math.round(call.audio_seconds)} seconds</dd>
                  </>
                )}
              </dl>
              <p className="tx-privacy">The prompt itself is not stored: it holds CVs and transcripts, which are personal data.</p>
            </section>

            <section className="tx-drawer-section">
              <h3>
                <FileOutput size={15} aria-hidden="true" /> What came out
              </h3>
              {call.error && <p className="notice notice-attention">{call.error}</p>}
              {call.output_preview ? <blockquote className="tx-preview">{call.output_preview}</blockquote> : <p className="muted small">No output preview.</p>}
              {Object.keys(call.summary ?? {}).length > 0 && (
                <dl className="tx-dl">
                  {Object.entries(call.summary).map(([k, v]) =>
                    v === null || v === undefined ? null : (
                      <div key={k} className="tx-dl-row">
                        <dt>{label(k)}</dt>
                        <dd>{typeof v === "number" ? v.toLocaleString() : String(v)}</dd>
                      </div>
                    ),
                  )}
                </dl>
              )}
            </section>

            <section className="tx-drawer-section">
              <h3>
                <Clock size={15} aria-hidden="true" /> Time
              </h3>
              <dl className="tx-dl">
                <dt>{call.stage === "technical_interview" ? "Interview length" : "Latency"}</dt>
                <dd>{duration(call.latency_ms) || "Not recorded"}</dd>
                <dt>Output tokens</dt>
                <dd>
                  {tokens(call.output_tokens)}
                  {call.reasoning_tokens ? `, of which ${tokens(call.reasoning_tokens)} reasoning` : ""}
                </dd>
              </dl>
            </section>

            <section className="tx-drawer-section">
              <h3>
                <Coins size={15} aria-hidden="true" /> Cost
              </h3>
              <p className="tx-cost">{usd(call.cost_usd)}</p>
              {call.cost_note && <p className="muted small">{call.cost_note}</p>}
              {call.price ? (
                <p className="muted small">
                  Price used:{" "}
                  {Object.entries(call.price)
                    .map(([k, v]) => `${label(k)} $${v}${k === "per_minute" ? " a minute" : " per million tokens"}`)
                    .join(", ")}
                  .
                </p>
              ) : (
                !call.price_known && (
                  <p className="muted small">
                    No price is set for {call.model}. Add it to MODEL_PRICES_JSON on the server to include it in costs.
                  </p>
                )
              )}
              <p className="muted small">{call.price_source}</p>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}

export function Calls() {
  const { params, set, clear, page } = useFilters();
  const q = params.get("q") ?? "";
  const stage = params.get("stage");
  const model = params.get("model");
  const status = params.get("status");
  const open = params.get("call");
  const query = new URLSearchParams({ page: String(page), page_size: "25" });
  for (const [k, v] of Object.entries({ q, stage, model, status })) if (v) query.set(k, v);
  const { data, error, refreshing } = useFetch<ModelCallList>(`/transparency/calls?${query}`);
  const sub = "Every call MITRA makes to a model: what it was for, how long it took and what it cost.";
  const filtered = Boolean(q || stage || model || status);

  if (error && !data) {
    return (
      <div className="page page-wide">
        <TransparencyHead sub={sub} />
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <PageSkeleton rows={6} />;

  return (
    <div className="page page-wide dash">
      <TransparencyHead sub={sub} />
      <div className="toolbar">
        <SearchBox value={q} onChange={(v) => set({ q: v })} placeholder="Search by candidate or job" />
        <label className="select">
          <span className="visually-hidden">Stage</span>
          <select value={stage ?? ""} onChange={(e) => set({ stage: e.target.value || null })}>
            <option value="">Every stage</option>
            {data.facets.stages.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="select">
          <span className="visually-hidden">Model</span>
          <select value={model ?? ""} onChange={(e) => set({ model: e.target.value || null })}>
            <option value="">Every model</option>
            {data.facets.models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="select">
          <span className="visually-hidden">Status</span>
          <select value={status ?? ""} onChange={(e) => set({ status: e.target.value || null })}>
            <option value="">Any result</option>
            <option value="ok">OK</option>
            <option value="error">Failed</option>
            <option value="schema_error">Unusable answer</option>
          </select>
        </label>
      </div>

      <p className="tx-tally">
        <strong>{data.summary.calls}</strong> calls, <strong>{usd(data.summary.cost_usd)}</strong>
        {data.summary.errors ? `, ${data.summary.errors} failed` : ""}
        {data.summary.unpriced ? `, ${data.summary.unpriced} with no price set` : ""}.
      </p>

      <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
        {data.items.length === 0 ? (
          <div className="empty">
            <p className="empty-title">{filtered ? "No call matches" : "No model calls yet"}</p>
            <p className="muted">Calls appear here as MITRA reads jobs, screens CVs and evaluates rounds.</p>
            {filtered && (
              <Button size="sm" icon={<X size={15} />} onClick={clear} className="tx-clear">
                Clear everything
              </Button>
            )}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table tx-table tx-calls">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Stage</th>
                  <th scope="col">For</th>
                  <th scope="col">Model</th>
                  <th scope="col">Result</th>
                  <th scope="col" className="num">
                    Tokens in / out
                  </th>
                  <th scope="col" className="num">
                    Time
                  </th>
                  <th scope="col" className="num">
                    Cost
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c: ModelCallRow) => (
                  <tr
                    key={c.id}
                    className={open === c.id ? "tx-picked tx-call" : "tx-call"}
                    tabIndex={0}
                    onClick={() => set({ call: c.id }, false)}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && set({ call: c.id }, false)}
                  >
                    <td className="tx-when">{when(c.at)}</td>
                    <td>
                      <span className="tx-stage">{c.stage_label}</span>
                    </td>
                    <td>
                      <span className="row-title">{c.candidate?.name ?? c.job?.title ?? c.purpose}</span>
                      {(c.candidate || c.job) && <span className="row-sub">{c.candidate ? c.job?.title ?? c.purpose : c.purpose}</span>}
                    </td>
                    <td>
                      <span className="tx-code">{c.model}</span>
                    </td>
                    <td>
                      <Status value={c.status} />
                    </td>
                    <td className="num">
                      {!c.input_tokens && !c.output_tokens && c.audio_seconds
                        ? `${Math.round(c.audio_seconds / 60)} min audio`
                        : `${tokens(c.input_tokens)} / ${tokens(c.output_tokens)}`}
                    </td>
                    <td className="num">{duration(c.latency_ms)}</td>
                    <td className={c.price_known ? "num" : "num muted"}>{c.price_known ? usd(c.cost_usd) : "Not set"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager total={data.total} page={data.page} pageSize={data.page_size} onPage={(p) => set({ page: String(p) }, false)} />
      </div>
      {open && <Drawer id={open} onClose={() => set({ call: null }, false)} />}
    </div>
  );
}
