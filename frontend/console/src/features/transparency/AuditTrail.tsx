// Everything people did that someone may later need to account for: sign-ins,
// failed sign-ins, job requests, candidates, rounds, recordings opened.

import { X } from "lucide-react";
import { ROLE_LABELS } from "../../contracts";
import { Button } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { Pager, SearchBox, TransparencyHead, useFetch, useFilters, when } from "./common";
import type { Audit } from "./types";
import "./transparency.css";

function detail(d: Record<string, unknown> | null): string {
  if (!d) return "";
  return Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("; ");
}

export function AuditTrail() {
  const { params, set, clear, page } = useFilters();
  const q = params.get("q") ?? "";
  const action = params.get("action");
  const query = new URLSearchParams({ page: String(page), page_size: "30" });
  if (q) query.set("q", q);
  if (action) query.set("action", action);
  const { data, error, refreshing } = useFetch<Audit>(`/transparency/audit?${query}`);
  const sub = "What people did that someone may later need to account for, newest first.";

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
        <SearchBox value={q} onChange={(v) => set({ q: v })} placeholder="Search by person, email or record" />
        <label className="select">
          <span className="visually-hidden">What happened</span>
          <select value={action ?? ""} onChange={(e) => set({ action: e.target.value || null })}>
            <option value="">Everything</option>
            {data.actions.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
        {data.items.length === 0 ? (
          <div className="empty">
            <p className="empty-title">{q || action ? "Nothing matches" : "Nothing recorded yet"}</p>
            {(q || action) && (
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
                <col className="tx-col-what" />
                <col />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Who</th>
                  <th scope="col">What</th>
                  <th scope="col">On</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id}>
                    <td className="tx-when">{when(e.at)}</td>
                    <td>
                      {e.actor ? (
                        <>
                          <span className="row-title">{e.actor.name}</span>
                          <span className="row-sub">
                            {ROLE_LABELS[e.actor.role] ?? e.actor.role}, {e.actor.email}
                          </span>
                        </>
                      ) : (
                        <span className="muted">Nobody signed in</span>
                      )}
                    </td>
                    <td className={e.action === "sign_in_failed" ? "tx-what tone-attention" : "tx-what"}>{e.label}</td>
                    <td>
                      {e.subject_type && (
                        <span className="row-sub">
                          {e.subject_type.replace(/_/g, " ")}: <code className="tx-code">{e.subject_id}</code>
                        </span>
                      )}
                      {e.detail && <span className="row-sub">{detail(e.detail)}</span>}
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
  );
}
