// Track H. Privacy under the DPDP Act 2023: candidates' deletion requests,
// how long each kind of personal data is kept, and the consent records.
// Nothing is deleted on a timer: a person runs each deletion, after a
// confirmation, and every run is in the audit log.

import { ChevronLeft, ChevronRight, ClipboardList, Eraser, FileX2, Plus, Save, Search, Trash2, Undo2, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS } from "../../contracts";
import { Button } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { useToast } from "../../shell/ui/Toast";
import { day, daysSince, type CandidateHit, type ConsentList, type DeletionRequest, type Retention, type RetentionKey } from "./types";
import "./admin.css";

const STATUS_TONE = { open: "attention", completed: "good", refused: "closed" } as const;
const STATUS_LABEL = { open: "Open", completed: "Erased", refused: "Refused" } as const;

// ---- deletion requests --------------------------------------------------------------------------------

function LogRequest({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<CandidateHit[]>([]);
  const [picked, setPicked] = useState<CandidateHit | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (picked || q.trim().length < 2) {
      setHits([]);
      return;
    }
    const timer = window.setTimeout(() => {
      api
        .get<{ candidates: CandidateHit[] }>(`/admin/candidates?q=${encodeURIComponent(q.trim())}`)
        .then((d) => setHits(d.candidates))
        .catch(() => setHits([]));
    }, 220);
    return () => window.clearTimeout(timer);
  }, [q, picked]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/admin/deletion-requests", {
        candidate_id: picked?.id ?? null,
        candidate_name: picked ? picked.name : q.trim(),
        contact_note: note,
      });
      toast("Deletion request logged");
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <div className="field picker">
        <label htmlFor="dr-who">Who asked</label>
        {picked ? (
          <div className="picked">
            <span>
              <strong>{picked.name}</strong>
              <span className="row-sub">
                {picked.job_title}, {STAGE_LABELS[picked.stage]}
              </span>
            </span>
            <Button size="sm" variant="ghost" icon={<X size={15} />} onClick={() => setPicked(null)}>
              Change
            </Button>
          </div>
        ) : (
          <>
            <input id="dr-who" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search candidates by name" autoFocus autoComplete="off" />
            {hits.length > 0 && (
              <ul className="picker-results">
                {hits.map((h) => (
                  <li key={h.id}>
                    <button type="button" onClick={() => setPicked(h)}>
                      <strong>{h.name}</strong>
                      <span className="row-sub">
                        {h.job_title}, {STAGE_LABELS[h.stage]}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {q.trim().length >= 2 && hits.length === 0 && (
              <span className="muted small">No candidate by that name. You can still log the request under the name you typed.</span>
            )}
          </>
        )}
      </div>
      <div className="field">
        <label htmlFor="dr-note">How it arrived and how to reply (optional)</label>
        <textarea id="dr-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: email to HR on 18 November; reply to the same address." />
      </div>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <Button type="submit" variant="primary" icon={<ClipboardList size={16} />} loading={busy} disabled={!picked && q.trim().length < 2}>
          Log the request
        </Button>
        <Button variant="ghost" icon={<X size={16} />} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function RequestItem({ request, onChanged }: { request: DeletionRequest; onChanged: () => void }) {
  const toast = useToast();
  const [acting, setActing] = useState<"complete" | "refuse" | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = request.status === "open";

  async function resolve(e: FormEvent) {
    e.preventDefault();
    if (!acting) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/admin/deletion-requests/${request.id}/${acting}`, { resolution: text });
      toast(acting === "complete" ? `${request.candidate_name}: data erased` : "Request refused");
      setActing(null);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const waited = daysSince(request.received_at);
  return (
    <li className={open ? "request open" : "request"}>
      <div className="request-head">
        <span className="request-name">{request.candidate_name}</span>
        <span className={`mark tone-${STATUS_TONE[request.status]}`}>{STATUS_LABEL[request.status]}</span>
      </div>
      <span className="request-meta">
        Logged {day(request.received_at)}
        {request.received_by ? ` by ${request.received_by}` : ""}
        {open ? `. Open for ${waited === 0 ? "less than a day" : `${waited} ${waited === 1 ? "day" : "days"}`}.` : ""}
        {!request.candidate_id && open ? " Not linked to a candidate record, so there is nothing to erase here; check other systems." : ""}
      </span>
      {request.contact_note && <p className="request-note">{request.contact_note}</p>}
      {!open && request.resolution && (
        <p className="request-resolution">
          {request.resolution}
          <span className="row-sub">
            {request.resolved_by ? `${request.resolved_by}, ` : ""}
            {day(request.resolved_at)}
          </span>
        </p>
      )}
      {open &&
        (acting ? (
          <form className="reason" onSubmit={resolve}>
            {acting === "complete" && request.candidate_id ? (
              <p className="notice notice-attention">
                This erases {request.candidate_name}'s CV, screening, recordings, transcripts, HR notes and evaluations, and replaces their name.
                The decisions made about them stay on record. It cannot be undone.
              </p>
            ) : null}
            <label htmlFor={`res-${request.id}`}>
              {acting === "refuse" ? "Why it is refused, for example a legal hold" : request.candidate_id ? "What was done, kept with the request" : "What was done, for example erased from the shared drive"}
            </label>
            <textarea id={`res-${request.id}`} rows={2} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
            {error && <p className="error">{error}</p>}
            <div className="actions">
              <Button
                type="submit"
                variant={acting === "complete" && request.candidate_id ? "danger" : "primary"}
                icon={acting === "complete" ? <Eraser size={16} /> : <FileX2 size={16} />}
                loading={busy}
                disabled={!text.trim()}
              >
                {acting === "refuse" ? "Refuse the request" : request.candidate_id ? "Erase their data" : "Mark as done"}
              </Button>
              <Button variant="ghost" icon={<Undo2 size={16} />} onClick={() => setActing(null)}>
                Back
              </Button>
            </div>
          </form>
        ) : (
          <div className="actions">
            <Button variant={request.candidate_id ? "danger" : "primary"} size="sm" icon={<Eraser size={15} />} onClick={() => { setText(""); setActing("complete"); }}>
              {request.candidate_id ? "Erase their data" : "Mark as done"}
            </Button>
            <Button size="sm" icon={<FileX2 size={15} />} onClick={() => { setText(""); setActing("refuse"); }}>
              Refuse
            </Button>
          </div>
        ))}
    </li>
  );
}

function RequestsTab() {
  const [data, setData] = useState<{ requests: DeletionRequest[]; open: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [logging, setLogging] = useState(false);

  const load = useCallback(() => {
    api
      .get<{ requests: DeletionRequest[]; open: number }>("/admin/deletion-requests")
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <PageSkeleton rows={3} />;

  return (
    <div className="section">
      <div className="notice-row">
        <p className="muted">
          A candidate may ask for their personal data to be erased. Log the request when it arrives, then erase their data or refuse with a reason.
          Open requests come first, oldest first.
        </p>
        {!logging && (
          <Button variant="primary" icon={<Plus size={17} />} onClick={() => setLogging(true)}>
            Log a request
          </Button>
        )}
      </div>
      {logging && (
        <LogRequest
          onCancel={() => setLogging(false)}
          onDone={() => {
            setLogging(false);
            load();
          }}
        />
      )}
      {data.requests.length === 0 ? (
        <div className="empty">
          <p className="empty-title">No deletion requests</p>
          <p className="muted">When a candidate asks for their data to be erased, log it here so the clock and the outcome are on record.</p>
        </div>
      ) : (
        <ul className="requests">
          {data.requests.map((r) => (
            <RequestItem key={r.id} request={r} onChanged={load} />
          ))}
        </ul>
      )}
    </div>
  );
}

// ---- retention ---------------------------------------------------------------------------------------------

function RetentionTab() {
  const toast = useToast();
  const [data, setData] = useState<Retention | null>(null);
  const [days, setDays] = useState<Record<RetentionKey, string>>({ cv: "", recording: "", transcript: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<RetentionKey | null>(null);

  const take = useCallback((d: Retention) => {
    setData(d);
    setDays(Object.fromEntries(d.categories.map((c) => [c.key, String(c.days)])) as Record<RetentionKey, string>);
  }, []);

  useEffect(() => {
    api.get<Retention>("/admin/retention").then(take).catch((e: Error) => setError(e.message));
  }, [take]);

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <PageSkeleton rows={3} />;

  const changed = data.categories.filter((c) => days[c.key] !== String(c.days));
  const valid = changed.every((c) => /^\d+$/.test(days[c.key]) && Number(days[c.key]) >= 1 && Number(days[c.key]) <= 3650);

  async function save() {
    setBusy("save");
    setError(null);
    try {
      take(await api.post<Retention>("/admin/retention", Object.fromEntries(changed.map((c) => [c.key, Number(days[c.key])]))));
      toast("Retention periods saved");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function run(key: RetentionKey) {
    setBusy(key);
    setError(null);
    try {
      const r = await api.post<{ erased: number; retention: Retention }>(`/admin/retention/${key}/run`);
      take(r.retention);
      setConfirm(null);
      toast(`Deleted for ${r.erased} ${r.erased === 1 ? "candidate" : "candidates"}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="section">
      <p className="muted">
        Each period counts from the day a candidate's process ended: shortlisted, rejected or withdrew. Candidates still in progress are never counted.
        Nothing is deleted until you run it. {data.closed_candidates} {data.closed_candidates === 1 ? "candidate has" : "candidates have"} finished so far.
      </p>
      <ul className="retention-list">
        {data.categories.map((c) => (
          <li key={c.key} className="retention-item">
            <div>
              <h3>{c.label}</h3>
              <p className="muted small">{c.covers}</p>
            </div>
            <label className="retention-days">
              <span className="visually-hidden">{c.label}: days to keep</span>
              <input inputMode="numeric" value={days[c.key]} onChange={(e) => setDays({ ...days, [c.key]: e.target.value.replace(/[^\d]/g, "") })} />
              <span className="muted">days</span>
            </label>
            <div className="retention-due">
              <span className={c.due ? "due-figure has" : "due-figure"}>{c.due}</span>
              <span className="due-label">{c.due === 1 ? "candidate is" : "candidates are"} past this period</span>
              {c.due > 0 && confirm !== c.key && (
                <Button size="sm" variant="danger" icon={<Trash2 size={15} />} onClick={() => setConfirm(c.key)}>
                  Delete now
                </Button>
              )}
            </div>
            {confirm === c.key && (
              <div className="retention-confirm actions">
                <span>
                  Delete {c.label.toLowerCase()} for {c.due} {c.due === 1 ? "candidate" : "candidates"}? This cannot be undone. Decisions stay on record.
                </span>
                <Button variant="danger" size="sm" icon={<Trash2 size={15} />} loading={busy === c.key} onClick={() => run(c.key)}>
                  Delete
                </Button>
                <Button variant="ghost" size="sm" icon={<Undo2 size={15} />} onClick={() => setConfirm(null)}>
                  Keep it
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="error">{error}</p>}
      {changed.length > 0 && (
        <div className="actions">
          <Button variant="primary" icon={<Save size={16} />} loading={busy === "save"} disabled={!valid} onClick={save}>
            Save retention periods
          </Button>
          <Button variant="ghost" icon={<Undo2 size={16} />} onClick={() => take(data)}>
            Undo changes
          </Button>
          {!valid && <span className="error">Each period is between 1 and 3650 days.</span>}
        </div>
      )}
    </div>
  );
}

// ---- consent records ----------------------------------------------------------------------------------------

const PAGE_SIZE = 25;

function ConsentTab() {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<ConsentList | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState(params.get("q") ?? "");
  const q = params.get("q") ?? "";
  const show = params.get("show") ?? "all";
  const page = Number(params.get("page") ?? 1);

  const set = useCallback(
    (changes: Record<string, string | null>, resetPage = true) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(changes)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      if (resetPage && !("page" in changes)) next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  useEffect(() => {
    if (text === q) return;
    const timer = window.setTimeout(() => set({ q: text.trim() || null }), 250);
    return () => window.clearTimeout(timer);
  }, [text, q, set]);

  useEffect(() => {
    setRefreshing(true);
    const query = new URLSearchParams({ show, page: String(page), page_size: String(PAGE_SIZE) });
    if (q) query.set("q", q);
    api
      .get<ConsentList>(`/admin/consent?${query}`)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [q, show, page]);

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <PageSkeleton rows={5} />;

  const all = data.summary.consented + data.summary.missing;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  return (
    <div className="section">
      <p className="consent-summary">
        <strong>{data.summary.consented}</strong> of <strong>{all}</strong> interviews have the candidate's consent recorded before they joined.
        {data.summary.missing > 0 && " The rest were never joined, or were held before consent was captured."}
      </p>
      <div className="toolbar">
        <label className="search-box">
          <Search size={17} aria-hidden="true" />
          <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search by candidate or job" aria-label="Search consent records" />
          {text && (
            <button type="button" className="search-clear" aria-label="Clear search" onClick={() => setText("")}>
              <X size={15} />
            </button>
          )}
        </label>
        <div className="segmented" role="group" aria-label="Consent">
          {(["all", "consented", "missing"] as const).map((s) => (
            <button key={s} type="button" aria-pressed={show === s} onClick={() => set({ show: s === "all" ? null : s })}>
              {s === "all" ? "Every interview" : s === "consented" ? "Consented" : "Not recorded"}
            </button>
          ))}
        </div>
      </div>
      <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
        {data.items.length === 0 ? (
          <div className="empty">
            <p className="empty-title">No interviews match</p>
            <p className="muted">Consent is recorded when a candidate accepts the notice before joining a round.</p>
          </div>
        ) : (
          <div className="table-wrap admin-table">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Candidate</th>
                  <th scope="col">Round</th>
                  <th scope="col">Booked</th>
                  <th scope="col">Consent</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.interview_id}>
                    <td>
                      <Link className="row-title" viewTransition to={`/candidates/${r.candidate_id}`}>
                        {r.candidate}
                      </Link>
                      <span className="row-sub">{r.job_title}</span>
                    </td>
                    <td>{r.round === "hr" ? "HR round" : "Technical round"}</td>
                    <td>{day(r.booked_at)}</td>
                    <td>
                      {r.consent_accepted_at ? (
                        <span className="mark tone-good" title={new Date(r.consent_accepted_at).toLocaleString()}>
                          Consented {day(r.consent_accepted_at)}
                        </span>
                      ) : (
                        <span className="mark tone-attention">Not recorded</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data.total > data.page_size && (
          <nav className="pager" aria-label="Pages">
            <span className="muted">{data.total} interviews</span>
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

// ---- the page ------------------------------------------------------------------------------------------------

const TABS = [
  { id: "requests", label: "Deletion requests" },
  { id: "retention", label: "Retention" },
  { id: "consent", label: "Consent records" },
] as const;

export function Privacy() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.id === params.get("tab"))?.id ?? "requests";

  return (
    <div className="page page-wide dash">
      <header className="page-head">
        <div>
          <h1 className="page-title">Privacy</h1>
          <p className="page-sub">
            Candidates' personal data under the DPDP Act 2023: requests to erase it, how long it is kept, and the consent given before each interview.
          </p>
        </div>
      </header>
      <div className="segmented admin-tabs" role="tablist" aria-label="Privacy sections">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} aria-pressed={tab === t.id} onClick={() => setParams(t.id === "requests" ? {} : { tab: t.id }, { replace: true })}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "requests" ? <RequestsTab /> : tab === "retention" ? <RetentionTab /> : <ConsentTab />}
    </div>
  );
}
