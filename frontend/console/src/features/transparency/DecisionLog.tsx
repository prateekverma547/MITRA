// Every move of every candidate, beside what MITRA recommended at that moment.
// "Followed" and "differed" are the server's reading, made only where the two
// can be compared without interpretation; everything else says so.

import { ArrowRight, Filter, X } from "lucide-react";
import { Link } from "react-router-dom";
import { ROLE_LABELS, STAGE_LABELS, type Role } from "../../contracts";
import { Button } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { ChartCard } from "../../shell/viz/ChartCard";
import { TooltipLayer, tipProps, useTip } from "../../shell/viz/Tooltip";
import { Pager, SearchBox, TransparencyHead, useFetch, useFilters, when } from "./common";
import type { DecisionLog as Log, Outcome } from "./types";
import "./transparency.css";

export const OUTCOME_LABELS: Record<Outcome, string> = {
  followed: "Followed MITRA",
  differed: "Differed from MITRA",
  not_comparable: "Not comparable",
  no_recommendation: "No recommendation",
  mitra: "Recorded by MITRA",
};

const OUTCOME_FILTERS: Exclude<Outcome, "mitra">[] = ["followed", "differed", "not_comparable", "no_recommendation"];

function Agreement({ followed, differed }: { followed: number; differed: number }) {
  const tip = useTip();
  const total = followed + differed;
  if (!total) return <p className="muted">No decision in this view can be compared with a recommendation yet.</p>;
  const parts = [
    { key: "followed", label: "Followed MITRA", n: followed },
    { key: "differed", label: "Differed from MITRA", n: differed },
  ];
  return (
    <div className="pipeline">
      <div className="seg-bar tx-bar" role="list" aria-label="Decisions compared with MITRA's recommendation">
        {parts
          .filter((p) => p.n > 0)
          .map((p) => {
            const share = Math.round((p.n / total) * 100);
            return (
              <span key={p.key} role="listitem" className={`seg tx-seg-${p.key}`} style={{ flexGrow: p.n }} {...tipProps(tip, `${p.n} (${share}%)`, p.label)}>
                {share >= 8 && <span className="seg-value">{share}%</span>}
              </span>
            );
          })}
      </div>
      <ul className="legend legend-inline">
        {parts.map((p) => (
          <li key={p.key}>
            <span className={`swatch tx-seg-${p.key}`} aria-hidden="true" />
            {p.label} <span className="legend-count">{p.n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DecisionLog() {
  const { params, set, clear, page } = useFilters();
  const q = params.get("q") ?? "";
  const bu = params.get("bu");
  // People's decisions first: MITRA's recorded facts are one click away.
  const actorParam = params.get("actor") ?? "people";
  const actor = actorParam === "all" ? null : actorParam;
  const role = params.get("role");
  const result = params.get("result");
  const since = params.get("since") ?? "";
  const until = params.get("until") ?? "";

  const query = new URLSearchParams({ page: String(page), page_size: "15" });
  for (const [k, v] of Object.entries({ q, bu, actor, role, result, since, until })) if (v) query.set(k, v);
  const { data, error, refreshing } = useFetch<Log>(`/transparency/decisions?${query}`);

  if (error && !data) {
    return (
      <div className="page page-wide">
        <TransparencyHead sub="Who decided what, and what MITRA recommended at the time." />
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <PageSkeleton rows={6} />;

  const s = data.summary;
  const filtered = Boolean(q || bu || actor !== "people" || role || result || since || until);

  return (
    <TooltipLayer>
      <div className="page page-wide dash">
        <TransparencyHead sub="Who decided what, and what MITRA recommended at the time. MITRA records facts; people make every decision." />

        <div className="toolbar">
          <SearchBox value={q} onChange={(v) => set({ q: v })} placeholder="Search by candidate or job" />
          <label className="select">
            <span className="visually-hidden">Role</span>
            <select value={role ?? ""} onChange={(e) => set({ role: e.target.value || null, actor: e.target.value ? "people" : actor })}>
              <option value="">Any role</option>
              {(["hr", "bu_head"] as Role[]).map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABELS[r]}
                </option>
              ))}
            </select>
          </label>
          <label className="tx-date">
            <span>From</span>
            <input type="date" value={since} max={until || undefined} onChange={(e) => set({ since: e.target.value || null })} />
          </label>
          <label className="tx-date">
            <span>To</span>
            <input type="date" value={until} min={since || undefined} onChange={(e) => set({ until: e.target.value || null })} />
          </label>
        </div>

        <div className="filters" role="group" aria-label="Filters">
          {data.business_units.length > 1 && (
            <div className="chips" role="group" aria-label="Business unit">
              <button type="button" className="chip-btn" aria-pressed={!bu} onClick={() => set({ bu: null })}>
                All units
              </button>
              {data.business_units.map((b) => (
                <button key={b.id} type="button" className="chip-btn" aria-pressed={bu === b.id} onClick={() => set({ bu: b.id })}>
                  {b.name}
                </button>
              ))}
            </div>
          )}
          <div className="segmented" role="group" aria-label="Who moved them">
            {[
              { v: "all", label: "Everyone" },
              { v: "people", label: "People" },
              { v: "mitra", label: "MITRA" },
            ].map((o) => (
              <button key={o.label} type="button" aria-pressed={actorParam === o.v} onClick={() => set({ actor: o.v === "people" ? null : o.v, role: o.v === "people" ? role : null })}>
                {o.label}
              </button>
            ))}
          </div>
          <div className="chips" role="group" aria-label="Compared with MITRA">
            {OUTCOME_FILTERS.map((o) => (
              <button key={o} type="button" className="chip-btn" aria-pressed={result === o} onClick={() => set({ result: result === o ? null : o })}>
                <span className={`swatch tx-dot-${o}`} aria-hidden="true" /> {OUTCOME_LABELS[o]}
                <span className="chip-n">{s[o]}</span>
              </button>
            ))}
          </div>
        </div>

        <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
          <ChartCard
            title="Did people follow MITRA?"
            caption={
              s.differed_percent === null
                ? "Nothing comparable yet."
                : `${s.followed + s.differed} decisions answered a clear go or stop. People differed from MITRA on ${s.differed_percent}%.`
            }
            action={
              s.differed > 0 && result !== "differed" ? (
                <Button size="sm" variant="ghost" icon={<Filter size={14} />} onClick={() => set({ result: "differed", actor: null })}>
                  Show the ones that differed
                </Button>
              ) : undefined
            }
            table={
              <table>
                <thead>
                  <tr>
                    <th>Compared with MITRA</th>
                    <th className="num">Decisions</th>
                  </tr>
                </thead>
                <tbody>
                  {OUTCOME_FILTERS.map((o) => (
                    <tr key={o}>
                      <td>{OUTCOME_LABELS[o]}</td>
                      <td className="num">{s[o]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            }
          >
            <Agreement followed={s.followed} differed={s.differed} />
          </ChartCard>

          {data.items.length === 0 ? (
            <div className="empty">
              <p className="empty-title">{filtered ? "No decision matches" : "No decisions yet"}</p>
              <p className="muted">{filtered ? "Try a different search, or clear a filter." : "Every move of every candidate will be listed here."}</p>
              {filtered && (
                <Button size="sm" icon={<X size={15} />} onClick={clear} className="tx-clear">
                  Clear everything
                </Button>
              )}
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table table-fixed tx-table">
                <colgroup>
                  <col className="tx-col-when" />
                  <col className="tx-col-person" />
                  <col className="tx-col-move" />
                  <col className="tx-col-who" />
                  <col />
                  <col className="tx-col-outcome" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Candidate</th>
                    <th scope="col">Move</th>
                    <th scope="col">Who</th>
                    <th scope="col">MITRA's recommendation then</th>
                    <th scope="col">Compared</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((d) => (
                    <tr key={d.id}>
                      <td className="tx-when">{when(d.at)}</td>
                      <td>
                        <Link className="row-title" viewTransition to={`/candidates/${d.candidate.id}`}>
                          {d.candidate.name}
                        </Link>
                        <span className="row-sub">
                          {d.job.title}, {d.bu_name}
                        </span>
                      </td>
                      <td>
                        <span className="tx-move">
                          {STAGE_LABELS[d.from] ?? d.from} <ArrowRight size={13} aria-label="to" /> <strong>{STAGE_LABELS[d.to] ?? d.to}</strong>
                        </span>
                        {d.reason && <span className="row-sub tx-reason">"{d.reason}"</span>}
                      </td>
                      <td>
                        <span className={d.actor.kind === "mitra" ? "tx-actor tx-actor-mitra" : "tx-actor"}>{d.actor.name}</span>
                        {d.actor.kind === "person" && <span className="row-sub">{ROLE_LABELS[d.actor.role as Role] ?? d.actor.role}</span>}
                      </td>
                      <td className="tx-rec">{d.model_recommendation ?? <span className="muted">None recorded</span>}</td>
                      <td>
                        <span className={`tx-outcome tx-outcome-${d.outcome}`}>
                          <span className={`swatch tx-dot-${d.outcome}`} aria-hidden="true" />
                          {OUTCOME_LABELS[d.outcome]}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pager total={data.total} page={data.page} pageSize={data.page_size} onPage={(p) => set({ page: String(p) }, false)} />
        </div>
      </div>
    </TooltipLayer>
  );
}
