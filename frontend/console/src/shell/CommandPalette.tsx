// ⌘K from anywhere: find a candidate, a job or a business unit, or jump to a
// screen. Search is scoped on the server like everything else.

import { Briefcase, Building2, CornerDownLeft, Home as HomeIcon, Plus, Search, User, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { STAGE_LABELS, type SearchResults } from "../contracts";
import { useSession } from "../session";

interface Item {
  id: string;
  section: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  to: string;
}

export function useCommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    function key(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  return { open, setOpen };
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { me } = useSession();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (open) {
      setQ("");
      setResults(null);
      setActive(0);
      window.setTimeout(() => input.current?.focus(), 20);
    }
  }, [open]);

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults(null);
      return;
    }
    const timer = window.setTimeout(() => {
      api.get<SearchResults>(`/search?q=${encodeURIComponent(q.trim())}`).then(setResults).catch(() => setResults(null));
    }, 150);
    return () => window.clearTimeout(timer);
  }, [q]);

  const items = useMemo<Item[]>(() => {
    const needle = q.trim().toLowerCase();
    const actions: Item[] = [
      { id: "home", section: "Go to", label: "Home", icon: <HomeIcon size={16} />, to: "/home" },
      { id: "jobs", section: "Go to", label: "Job requests", icon: <Briefcase size={16} />, to: "/jobs" },
      { id: "cands", section: "Go to", label: "Candidates", icon: <Users size={16} />, to: "/candidates" },
      ...(me?.role === "bu_head"
        ? [{ id: "new", section: "Go to", label: "New job request", icon: <Plus size={16} />, to: "/jobs/new" }]
        : []),
    ].filter((a) => !needle || a.label.toLowerCase().includes(needle));
    const found: Item[] = [
      ...(results?.candidates ?? []).map((c) => ({
        id: `c-${c.id}`, section: "Candidates", label: c.name, hint: `${STAGE_LABELS[c.stage]}, ${c.job_title}`,
        icon: <User size={16} />, to: `/candidates/${c.id}`,
      })),
      ...(results?.jobs ?? []).map((j) => ({
        id: `j-${j.id}`, section: "Job requests", label: j.title, hint: j.bu_name ?? undefined,
        icon: <Briefcase size={16} />, to: me?.role === "hr" ? `/jobs/${j.id}/candidates` : `/jobs/${j.id}`,
      })),
      ...(results?.business_units ?? []).map((b) => ({
        id: `b-${b.id}`, section: "Business units", label: b.name, hint: "Focus the dashboard",
        icon: <Building2 size={16} />, to: `/home?bu=${b.id}`,
      })),
    ];
    return [...found, ...actions];
  }, [q, results, me]);

  useEffect(() => setActive(0), [items.length]);

  if (!open) return null;

  function go(item: Item | undefined) {
    if (!item) return;
    onClose();
    navigate(item.to, { viewTransition: true });
  }

  let lastSection = "";
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Search" onMouseDown={(e) => e.stopPropagation()}>
        <label className="palette-input">
          <Search size={18} aria-hidden="true" />
          <input
            ref={input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search candidates, jobs and business units"
            aria-label="Search"
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => Math.min(items.length - 1, a + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => Math.max(0, a - 1));
              } else if (e.key === "Enter") go(items[active]);
            }}
          />
          <kbd>esc</kbd>
        </label>
        <ul className="palette-list" role="listbox" aria-label="Results">
          {items.length === 0 && <li className="palette-empty">Nothing matches “{q}”.</li>}
          {items.map((item, i) => {
            const head = item.section !== lastSection ? item.section : null;
            lastSection = item.section;
            return (
              <li key={item.id}>
                {head && <span className="palette-section">{head}</span>}
                <button
                  type="button"
                  role="option"
                  aria-selected={i === active}
                  className={i === active ? "palette-item active" : "palette-item"}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(item)}
                >
                  <span className="palette-icon">{item.icon}</span>
                  <span className="palette-label">{item.label}</span>
                  {item.hint && <span className="palette-hint">{item.hint}</span>}
                  {i === active && <CornerDownLeft size={14} className="palette-enter" aria-hidden="true" />}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
