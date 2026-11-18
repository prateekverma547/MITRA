// Every candidate this person may see, across jobs and business units.
// Built for hundreds: search, filters with counts, sorting and pages, all in
// the URL so back, forward and a shared link keep the same view.

import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS, STAGE_TONES, initials, type Directory as DirectoryData } from "../../contracts";
import { useSession } from "../../session";
import { Button } from "../../shell/ui/Button";
import { RowLink } from "../../shell/ui/RowLink";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { ScoreMeter } from "../screening/ScoreMeter";

const SORTS = [
  { id: "recent", label: "Newest first" },
  { id: "waiting", label: "Waiting longest" },
  { id: "score", label: "Highest score" },
  { id: "name", label: "Name" },
];
const PAGE_SIZE = 25;

export function Directory() {
  const { me } = useSession();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<DirectoryData | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState(params.get("q") ?? "");
  const search = useRef<HTMLInputElement>(null);

  const q = params.get("q") ?? "";
  const bu = params.get("bu");
  const group = params.get("group");
  const flag = params.get("flag");
  const sort = params.get("sort") ?? "recent";
  const page = Number(params.get("page") ?? 1);

  function set(changes: Record<string, string | null>, resetPage = true) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    if (resetPage && !("page" in changes)) next.delete("page");
    setParams(next, { replace: true });
  }

  // Typing searches after a short pause, not on every key.
  useEffect(() => {
    if (text === q) return;
    const timer = window.setTimeout(() => set({ q: text.trim() || null }), 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  useEffect(() => {
    setRefreshing(true);
    const query = new URLSearchParams({ sort, page: String(page), page_size: String(PAGE_SIZE) });
    if (q) query.set("q", q);
    if (bu) query.set("bu", bu);
    if (group) query.set("group", group);
    if (flag) query.set("flag", flag);
    api
      .get<DirectoryData>(`/candidates?${query}`)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [q, bu, group, flag, sort, page]);

  if (error && !data) {
    return (
      <div className="page">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <PageSkeleton rows={6} />;

  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const from = data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const to = Math.min(data.total, data.page * data.page_size);
  const filtered = Boolean(q || bu || group || flag);

  return (
    <div className="page page-wide dash">
      <header className="page-head">
        <div>
          <h1 className="page-title">Candidates</h1>
          <p className="page-sub">
            {me?.role === "bu_head" ? `Everyone in ${me.bu_name ?? "your"} job requests.` : "Everyone across every job request and business unit."}
          </p>
        </div>
      </header>

      <div className="toolbar">
        <label className="search-box">
          <Search size={17} aria-hidden="true" />
          <input
            ref={search}
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Search by candidate or job"
            aria-label="Search candidates"
          />
          {text && (
            <button type="button" className="search-clear" aria-label="Clear search" onClick={() => setText("")}>
              <X size={15} />
            </button>
          )}
        </label>
        <label className="select">
          <span className="visually-hidden">Sort</span>
          <select value={sort} onChange={(e) => set({ sort: e.target.value })}>
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
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
        <div className="chips" role="group" aria-label="Step">
          <button type="button" className="chip-btn" aria-pressed={!group} onClick={() => set({ group: null })}>
            Every step
          </button>
          {data.groups.map((g) => (
            <button key={g.key} type="button" className="chip-btn" aria-pressed={group === g.key} onClick={() => set({ group: group === g.key ? null : g.key })}>
              <span className={`swatch seg-${g.key}`} aria-hidden="true" /> {g.label}
              <span className="chip-n">{data.facets.groups[g.key]}</span>
            </button>
          ))}
        </div>
        <div className="chips" role="group" aria-label="Flags">
          {me?.role !== "org_admin" && (
            <button type="button" className="chip-btn" aria-pressed={flag === "needs_me"} onClick={() => set({ flag: flag === "needs_me" ? null : "needs_me" })}>
              Waiting for you<span className="chip-n">{data.facets.needs_me}</span>
            </button>
          )}
          <button type="button" className="chip-btn" aria-pressed={flag === "below"} onClick={() => set({ flag: flag === "below" ? null : "below" })}>
            Below the cut-off or unclear<span className="chip-n">{data.facets.below}</span>
          </button>
        </div>
      </div>

      <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
        {data.items.length === 0 ? (
          <div className="empty">
            <p className="empty-title">{filtered ? "Nobody matches" : "No candidates yet"}</p>
            <p className="muted">
              {filtered ? "Try a different search, or clear a filter." : "Candidates appear here once HR adds them to an approved job request."}
            </p>
            {filtered && (
              <Button size="sm" icon={<X size={15} />} onClick={() => { setText(""); setParams(new URLSearchParams(), { replace: true }); }}>
                Clear everything
              </Button>
            )}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table table-fixed">
              <colgroup>
                <col className="col-person" />
                <col className="col-stage" />
                <col />
                <col className="col-wait" />
                <col className="col-go" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Candidate</th>
                  <th scope="col">Where they stand</th>
                  <th scope="col">Screening</th>
                  <th scope="col">Waiting</th>
                  <th className="row-go-head" aria-hidden="true" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  <RowLink key={c.id} to={`/candidates/${c.id}`}>
                    <td>
                      <span className="person">
                        <span className="avatar" aria-hidden="true">
                          {initials(c.name)}
                        </span>
                        <span className="person-text">
                          <Link className="row-title" viewTransition to={`/candidates/${c.id}`}>
                            {c.name}
                          </Link>
                          <span className="row-sub">
                            {c.job.title}
                            {c.bu_name ? `, ${c.bu_name}` : ""}
                          </span>
                        </span>
                      </span>
                    </td>
                    <td>
                      <span className={`mark tone-${STAGE_TONES[c.stage]}`}>{STAGE_LABELS[c.stage]}</span>
                      {c.needs && <span className="row-sub needs">{c.needs}</span>}
                    </td>
                    <td>
                      {c.score_percent === null ? (
                        <span className="muted">Not screened</span>
                      ) : (
                        <ScoreMeter score={c.score_percent} threshold={c.threshold_percent ?? 50} enoughEvidence={c.enough_evidence ?? true} compact />
                      )}
                    </td>
                    <td>
                      <span className={c.needs && c.waiting_days >= 3 ? "wait long" : "wait"}>
                        {c.waiting_days === 0 ? "Today" : `${c.waiting_days} d`}
                      </span>
                    </td>
                  </RowLink>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data.total > 0 && (
          <nav className="pager" aria-label="Pages">
            <span className="muted">
              {from}–{to} of {data.total}
            </span>
            <div className="actions">
              <Button size="sm" icon={<ChevronLeft size={15} />} disabled={page <= 1} onClick={() => set({ page: String(page - 1) }, false)}>
                Previous
              </Button>
              <span className="pager-n">
                Page {data.page} of {pages}
              </span>
              <Button size="sm" disabled={page >= pages} onClick={() => set({ page: String(page + 1) }, false)}>
                Next <ChevronRight size={15} aria-hidden="true" />
              </Button>
            </div>
          </nav>
        )}
      </div>
    </div>
  );
}
