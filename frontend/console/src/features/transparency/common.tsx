// Pieces every transparency tab shares: the tab bar, URL-held filters, a
// debounced search box, the pager and dates.

import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { Button } from "../../shell/ui/Button";

const TABS = [
  { to: "/transparency", label: "Decisions" },
  { to: "/transparency/records", label: "Records" },
  { to: "/transparency/ai", label: "AI usage register" },
  { to: "/transparency/audit", label: "Audit trail" },
];

export function TransparencyHead({ sub, children }: { sub: string; children?: ReactNode }) {
  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="page-title">Transparency</h1>
          <p className="page-sub">{sub}</p>
        </div>
        {children}
      </header>
      <nav className="tx-tabs" aria-label="Transparency">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end viewTransition className={({ isActive }) => (isActive ? "tx-tab active" : "tx-tab")}>
            {t.label}
          </NavLink>
        ))}
      </nav>
    </>
  );
}

/** Filters live in the URL, so back, forward and a shared link keep the view. */
export function useFilters() {
  const [params, setParams] = useSearchParams();
  function set(changes: Record<string, string | null>, resetPage = true) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    if (resetPage && !("page" in changes)) next.delete("page");
    setParams(next, { replace: true });
  }
  function clear() {
    setParams(new URLSearchParams(), { replace: true });
  }
  return { params, set, clear, page: Number(params.get("page") ?? 1) };
}

export function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string | null) => void; placeholder: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (text === value) return;
    const timer = window.setTimeout(() => onChange(text.trim() || null), 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  return (
    <label className="search-box">
      <Search size={17} aria-hidden="true" />
      <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
      {text && (
        <button type="button" className="search-clear" aria-label="Clear search" onClick={() => setText("")}>
          <X size={15} />
        </button>
      )}
    </label>
  );
}

export function Pager({ total, page, pageSize, onPage }: { total: number; page: number; pageSize: number; onPage: (p: number) => void }) {
  if (!total) return null;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav className="pager" aria-label="Pages">
      <span className="muted">
        {from}–{to} of {total}
      </span>
      <div className="actions">
        <Button size="sm" icon={<ChevronLeft size={15} />} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <span className="pager-n">
          Page {page} of {pages}
        </span>
        <Button size="sm" icon={<ChevronRight size={15} />} disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </nav>
  );
}

export function when(iso: string | null): string {
  if (!iso) return "Not recorded";
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function day(iso: string | null): string {
  if (!iso) return "Not recorded";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function clock(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "";
  const m = Math.floor(seconds / 60);
  return `${m}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

/** The search term marked where it appears, so a match is seen, not hunted for. */
export function Highlight({ text, term }: { text: string; term: string }) {
  if (!term) return <>{text}</>;
  const lower = text.toLowerCase();
  const needle = term.toLowerCase();
  const parts: ReactNode[] = [];
  let at = 0;
  for (let i = lower.indexOf(needle); i >= 0; i = lower.indexOf(needle, at)) {
    parts.push(text.slice(at, i), <mark key={i}>{text.slice(i, i + needle.length)}</mark>);
    at = i + needle.length;
  }
  parts.push(text.slice(at));
  return <>{parts}</>;
}

/** Fetch on every change of `url`; a refetch keeps the current view, dimmed. */
export function useFetch<T>(url: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    let live = true;
    setRefreshing(true);
    api
      .get<T>(url)
      .then((d) => {
        if (!live) return;
        setData(d);
        setError(null);
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setRefreshing(false));
    return () => {
      live = false;
    };
  }, [url]);
  return { data, error, refreshing };
}
