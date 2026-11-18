import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";

/**
 * Goes back where you came from, with the reverse page transition. Opened
 * directly (a pasted link), there is no "back", so it goes to `fallback`.
 */
export function BackButton({ fallback, label }: { fallback: string; label: string }) {
  const navigate = useNavigate();
  function go() {
    document.documentElement.dataset.nav = "back";
    if (window.history.length > 1 && window.history.state?.idx > 0) navigate(-1);
    else navigate(fallback, { viewTransition: true });
  }
  return (
    <button type="button" className="back" onClick={go}>
      <ArrowLeft size={16} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}
