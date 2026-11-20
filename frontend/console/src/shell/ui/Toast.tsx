import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

/** How a toast reads at a glance: done (green), heads-up (yellow), or something removed or refused (red). */
export type ToastTone = "ok" | "warn" | "urgent";

interface ToastItem {
  id: number;
  text: string;
  tone: ToastTone;
}

const ToastContext = createContext<(text: string, tone?: ToastTone) => void>(() => {});

/** Confirms that something a person did has happened, then gets out of the way. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const show = useCallback((text: string, tone: ToastTone = "ok") => {
    const id = Date.now() + Math.random();
    setItems((all) => [...all, { id, text, tone }]);
    window.setTimeout(() => setItems((all) => all.filter((t) => t.id !== id)), 3600);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            {t.tone === "urgent" ? <AlertTriangle size={18} aria-hidden="true" /> : t.tone === "warn" ? <Info size={18} aria-hidden="true" /> : <CheckCircle2 size={18} aria-hidden="true" />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
