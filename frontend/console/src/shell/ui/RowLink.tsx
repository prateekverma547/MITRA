import { ChevronRight } from "lucide-react";
import type { MouseEvent, ReactNode } from "react";
import { useNavigate } from "react-router-dom";

/**
 * A table row you can click anywhere on. The first cell still holds a real
 * link, so keyboards and screen readers reach it the ordinary way.
 */
export function RowLink({ to, children }: { to: string; children: ReactNode }) {
  const navigate = useNavigate();
  function open(event: MouseEvent<HTMLTableRowElement>) {
    if ((event.target as HTMLElement).closest("a, button, input, textarea, select")) return;
    if (window.getSelection()?.toString()) return;
    navigate(to, { viewTransition: true });
  }
  return (
    <tr className="row-link" onClick={open}>
      {children}
      <td className="row-go" aria-hidden="true">
        <ChevronRight size={18} />
      </td>
    </tr>
  );
}
