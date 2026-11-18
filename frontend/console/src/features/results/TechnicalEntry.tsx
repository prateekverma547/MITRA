// The technical round on the candidate page: HR books it (Track I's booking,
// stubbed until it lands), cancels it, and sends the finished result to the BU
// head. After that, everyone reaches the results page from here.

import { CalendarPlus, ClipboardList, Send, Undo2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type { CandidateStage } from "../../contracts";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { CopyField } from "../../shell/ui/CopyField";
import { useToast } from "../../shell/ui/Toast";
import type { TechnicalView } from "./types";
import "./results.css";

const TECHNICAL: CandidateStage[] = ["ready_for_technical", "technical_booked", "technical_done"];
const RESULTS: CandidateStage[] = ["with_bu_head", "on_hold", "shortlisted"];

export function TechnicalEntry({ candidateId, stage, isBuHead, onChange }: { candidateId: string; stage: CandidateStage; isBuHead: boolean; onChange: () => void }) {
  const toast = useToast();
  const [view, setView] = useState<TechnicalView | null>(null);
  const [minutes, setMinutes] = useState<number | null>(null);
  const [mode, setMode] = useState<"cancel" | "send" | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const relevant = TECHNICAL.includes(stage);

  const load = useCallback(async () => {
    if (!relevant) return;
    try {
      const v = await api.get<TechnicalView>(`/candidates/${candidateId}/technical`);
      setView(v);
      setMinutes((m) => m ?? v.duration.default);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [candidateId, relevant]);

  useEffect(() => {
    load();
  }, [load, stage]);

  async function act(kind: "book" | "cancel" | "send") {
    setBusy(true);
    setError(null);
    try {
      const path = kind === "book" ? "technical" : kind === "cancel" ? "technical/cancel" : "send-to-bu-head";
      const body = kind === "book" ? { duration_minutes: minutes } : kind === "cancel" ? { reason: text } : { note: text.trim() || undefined };
      setView(await api.post<TechnicalView>(`/candidates/${candidateId}/${path}`, body));
      toast(kind === "book" ? "Technical round booked" : kind === "cancel" ? "Technical round cancelled" : "Sent to the BU head");
      setMode(null);
      setText("");
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (RESULTS.includes(stage)) {
    const deciding = isBuHead && (stage === "with_bu_head" || stage === "on_hold");
    return (
      <section className={deciding ? "hr-entry live-border" : "hr-entry"}>
        <div>
          <h2 className="section-title">{deciding ? "Waiting for your decision" : "Results"}</h2>
          <p className="muted">
            {deciding
              ? "The screening fit, the HR round and the technical round, with everything MITRA found. Shortlist, hold or reject from there."
              : "The screening fit, the HR round, the technical round and the BU head's decision."}
          </p>
        </div>
        <ButtonLink variant={deciding ? "primary" : "secondary"} to={`/candidates/${candidateId}/results`} icon={<ClipboardList size={17} />}>
          {deciding ? "Open results and decide" : "Open results"}
        </ButtonLink>
      </section>
    );
  }
  if (!relevant || !view) return error ? <p className="error">{error}</p> : null;

  const b = view.booking;
  return (
    <section className={view.can_book || view.can_send ? "hr-entry tech-entry live-border" : "hr-entry tech-entry"}>
      <div className="tech-entry-row">
        <div>
          <h2 className="section-title">Technical round</h2>
          <p className="muted">
            {stage === "ready_for_technical" &&
              (view.can_book ? "MITRA interviews them on the job, their CV and what the HR round raised. Choose how long it runs." : "Ready for the technical round. HR books it.")}
            {stage === "technical_booked" && `Booked${b?.duration_minutes ? ` for ${b.duration_minutes} minutes` : ""}${b?.booked_by ? ` by ${b.booked_by}` : ""}.`}
            {stage === "technical_done" &&
              (view.can_send
                ? "The technical round is done. Send the result to the BU head, who decides."
                : "The technical round is done. HR sends the result to the BU head.")}
          </p>
        </div>
        {stage === "technical_done" && (
          <ButtonLink to={`/candidates/${candidateId}/results`} icon={<ClipboardList size={17} />}>
            Open results
          </ButtonLink>
        )}
      </div>

      {view.can_book && (
        <div className="actions">
          <div className="duration">
            <label htmlFor="tech-minutes">Length</label>
            <input
              id="tech-minutes"
              type="range"
              min={view.duration.min}
              max={view.duration.max}
              step={5}
              value={minutes ?? view.duration.default}
              onChange={(e) => setMinutes(Number(e.target.value))}
            />
            <output htmlFor="tech-minutes">{minutes ?? view.duration.default} minutes</output>
          </div>
          <Button variant="primary" icon={<CalendarPlus size={17} />} loading={busy} onClick={() => act("book")}>
            Book the technical round
          </Button>
        </div>
      )}

      {b && (
        <>
          <div className="tech-links">
            <CopyField label="Candidate's join link" value={b.candidate_link} small />
            <CopyField label="Live view, for HR and the BU head" value={b.live_view_link} small />
          </div>
          {b.stub && (
            <p className="notice notice-attention">
              These links are placeholders. The interview track is not connected on this build yet, so nobody can join with them.
            </p>
          )}
        </>
      )}

      {mode ? (
        <form
          className="reason"
          onSubmit={(e) => {
            e.preventDefault();
            act(mode);
          }}
        >
          <label htmlFor="tech-text">
            {mode === "cancel" ? "Why is the technical round cancelled? They can be booked again." : "A note for the BU head (optional)"}
          </label>
          <textarea id="tech-text" rows={2} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
          <div className="actions">
            <Button
              type="submit"
              variant={mode === "cancel" ? "danger" : "primary"}
              icon={mode === "cancel" ? <XCircle size={17} /> : <Send size={16} />}
              loading={busy}
              disabled={mode === "cancel" && !text.trim()}
            >
              {mode === "cancel" ? "Cancel the round" : "Send to the BU head"}
            </Button>
            <Button variant="ghost" icon={<Undo2 size={16} />} disabled={busy} onClick={() => setMode(null)}>
              Back
            </Button>
          </div>
        </form>
      ) : (
        (view.can_cancel || view.can_send) && (
          <div className="actions">
            {view.can_send && (
              <Button variant="primary" icon={<Send size={16} />} onClick={() => setMode("send")}>
                Send to the BU head
              </Button>
            )}
            {view.can_cancel && (
              <Button variant="ghost" icon={<XCircle size={16} />} onClick={() => setMode("cancel")}>
                Cancel the booking
              </Button>
            )}
          </div>
        )
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
