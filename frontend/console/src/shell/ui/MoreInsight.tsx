import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

function remembered(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "open";
  } catch {
    return false;
  }
}

/**
 * The charts a role reads now and then, kept one click away so the page
 * shows what people act on first. It remembers being opened, per browser.
 */
export function MoreInsight({ id, hint, children }: { id: string; hint: string; children: ReactNode }) {
  const key = `mitra.more.${id}`;
  const [open, setOpen] = useState(() => remembered(key));
  const body = useId();

  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      window.localStorage.setItem(key, next ? "open" : "closed");
    } catch {
      // Storage can be unavailable; the panel still works.
    }
  }

  return (
    <section className={open ? "more open" : "more"}>
      <button type="button" className="more-toggle" aria-expanded={open} aria-controls={body} onClick={toggle}>
        {open ? "Less insight" : "More insight"}
        <span className="more-hint">{hint}</span>
        <ChevronDown size={18} aria-hidden="true" />
      </button>
      {open && (
        <div id={body} className="more-body">
          {children}
        </div>
      )}
    </section>
  );
}
