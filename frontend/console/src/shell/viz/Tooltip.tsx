import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

/** One floating readout for all charts. Values lead, labels follow. */
interface Tip {
  x: number;
  y: number;
  value: string;
  label: string;
}

const TipContext = createContext<{ show: (tip: Tip) => void; hide: () => void }>({ show: () => {}, hide: () => {} });

export function TooltipLayer({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = useCallback((t: Tip) => setTip(t), []);
  const hide = useCallback(() => setTip(null), []);
  return (
    <TipContext.Provider value={{ show, hide }}>
      {children}
      {tip && (
        <div className="viz-tip" style={{ left: tip.x, top: tip.y }} role="status">
          <strong>{tip.value}</strong>
          <span>{tip.label}</span>
        </div>
      )}
    </TipContext.Provider>
  );
}

export function useTip() {
  return useContext(TipContext);
}

/** Props that give an element the shared tooltip on hover and keyboard focus. */
export function tipProps(tip: ReturnType<typeof useTip>, value: string, label: string) {
  const at = (el: Element) => {
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top };
  };
  return {
    tabIndex: 0,
    "aria-label": `${label}: ${value}`,
    onPointerEnter: (e: React.PointerEvent) => tip.show({ ...at(e.currentTarget), value, label }),
    onPointerLeave: () => tip.hide(),
    onFocus: (e: React.FocusEvent) => tip.show({ ...at(e.currentTarget), value, label }),
    onBlur: () => tip.hide(),
  };
}
