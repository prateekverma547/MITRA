import { ArrowLeft } from "lucide-react";
import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Going back where you came from, with the reverse page transition. Opened
 * directly (a pasted link), there is no "back", so it goes to `fallback`.
 * Also used after a decision is saved: the work on this page is finished, so
 * you are returned to the list you came from instead of being left on it.
 */
export function useGoBack(fallback: string) {
  const navigate = useNavigate();
  return useCallback(() => {
    document.documentElement.dataset.nav = "back";
    if (window.history.length > 1 && window.history.state?.idx > 0) navigate(-1);
    else navigate(fallback, { viewTransition: true });
  }, [navigate, fallback]);
}

export function BackButton({ fallback, label }: { fallback: string; label: string }) {
  const go = useGoBack(fallback);
  return (
    <button type="button" className="back" onClick={go}>
      <ArrowLeft size={16} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}
