// The MITRA interview round on the candidate page. HR waits for MITRA's plan, books
// the round (a real room and the credentials to send), can cancel it before
// anyone joins, and sends the finished result to the BU head. After that,
// everyone reaches the results page from here. backend/hiring/technical.py.

import { CalendarPlus, ClipboardCopy, ClipboardList, MonitorPlay, RotateCcw, Send, Undo2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type { CandidateStage } from "../../contracts";
import { useGoBack } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { CopyField, copyText } from "../../shell/ui/CopyField";
import { Spinner } from "../../shell/ui/Spinner";
import { useToast } from "../../shell/ui/Toast";
import { ROUND_STATE, RoundSteps } from "./RoundStatus";
import type { TechnicalView } from "./types";
import "./results.css";

const TECHNICAL: CandidateStage[] = ["ready_for_technical", "technical_booked", "technical_done"];
const RESULTS: CandidateStage[] = ["with_bu_head", "on_hold", "shortlisted"];
const POLL_MS = 5000;

function invitation(view: TechnicalView): string {
  const b = view.booking!;
  return [
    `Hello ${view.candidate.name},`,
    "",
    `Your MITRA interview for ${view.job.title} is ready. It runs for about ${b.duration_minutes} minutes with MITRA, our AI interviewer, and it is recorded.`,
    `Join here: ${b.candidate_join_url}`,
    `Meeting ID: ${b.meeting_id}`,
    `Password: ${b.password}`,
  ].join("\n");
}

export function TechnicalEntry({ candidateId, stage, isBuHead, onChange }: { candidateId: string; stage: CandidateStage; isBuHead: boolean; onChange: () => void }) {
  const toast = useToast();
  const [view, setView] = useState<TechnicalView | null>(null);
  const [mode, setMode] = useState<"cancel" | "send" | null>(null);
  const [text, setText] = useState("");
  /** The length HR books; the plan's own length until HR picks another. */
  const [minutes, setMinutes] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const relevant = TECHNICAL.includes(stage);
  const goBack = useGoBack(view ? `/jobs/${view.job.id}/candidates` : "/home");

  const load = useCallback(async () => {
    if (!relevant) return;
    try {
      const next = await api.get<TechnicalView>(`/candidates/${candidateId}/technical`);
      setView(next);
      // The round moved the candidate on (the report is in, the room expired):
      // reload the page around it, so the next step, such as sending to the
      // BU head, is offered at once.
      if (next.candidate.stage !== stage) onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [candidateId, relevant, stage, onChange]);

  useEffect(() => {
    load();
  }, [load, stage]);

  // While the plan is being prepared, or the round is running or being scored, check back.
  const roundState = view?.round?.state;
  const waiting =
    view &&
    (view.plan.status === "generating" ||
      view.plan.status === "pending" ||
      view.booking !== null ||
      (roundState !== undefined && !["done", "did_not_happen", "not_booked"].includes(roundState)));
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(load, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [view, waiting, load]);

  // The lengths HR can book, with the plan's own length among them.
  const lengths = view ? [...new Set([...(view.plan.bookable_minutes ?? [20, 30, 40, 50, 60]), view.plan.minutes])].filter((m) => m >= 20 && m <= 60).sort((a, b) => a - b) : [];
  const bookMinutes = minutes ?? (view && lengths.includes(view.plan.minutes) ? view.plan.minutes : lengths[0] ?? 20);

  async function act(kind: "book" | "cancel" | "send" | "plan") {
    setBusy(true);
    setError(null);
    try {
      const path = { book: "technical", cancel: "technical/cancel", send: "send-to-bu-head", plan: "technical/plan" }[kind];
      const body =
        kind === "cancel"
          ? { reason: text }
          : kind === "send"
            ? { note: text.trim() || undefined }
            : kind === "book"
              ? { duration_minutes: bookMinutes }
              : undefined;
      setView(await api.post<TechnicalView>(`/candidates/${candidateId}/${path}`, body));
      toast({ book: "MITRA interview round booked", cancel: "MITRA interview round cancelled", send: "Sent to the BU head", plan: "Preparing MITRA's plan again" }[kind]);
      setMode(null);
      setText("");
      // Sent: HR's part is done, so return to the list they came from.
      if (kind === "send") goBack();
      else if (kind !== "plan") onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (RESULTS.includes(stage)) {
    const deciding = isBuHead && (stage === "with_bu_head" || stage === "on_hold");
    return (
      <section className="hr-entry">
        <div>
          <h2 className="section-title">{deciding ? "Waiting for your decision" : "Results"}</h2>
          <p className="muted">The screening fit, the HR round and the MITRA interview round, with everything MITRA found.</p>
        </div>
        <ButtonLink variant={deciding ? "primary" : "secondary"} to={`/candidates/${candidateId}/results`} icon={<ClipboardList size={17} />}>
          {deciding ? "Open results and decide" : "Open results"}
        </ButtonLink>
      </section>
    );
  }
  if (!relevant || !view) return error ? <p className="error">{error}</p> : null;

  const b = view.booking;
  const plan = view.plan;
  return (
    <section className="hr-entry tech-entry">
      {stage === "ready_for_technical" && (
        <div className="tech-plan">
          {plan.status === "ready" ? (
            <span className="tech-state ok">MITRA's plan is ready: about {plan.minutes} minutes.</span>
          ) : plan.status === "failed" ? (
            <span className="tech-state bad">MITRA could not prepare the plan{plan.error ? `: ${plan.error}` : "."}</span>
          ) : (
            <span className="tech-state">
              <Spinner size={14} /> MITRA is preparing the plan from the job and the CV.
            </span>
          )}
        </div>
      )}

      {view.round && !["waiting", "consented", "not_booked"].includes(view.round.state) && (
        <div className="tech-status">
          <div className="tech-status-head">
            <span className={`mark round-mark round-${ROUND_STATE[view.round.state].tone}`}>{ROUND_STATE[view.round.state].label}</span>
            <span className="muted small">{ROUND_STATE[view.round.state].line}</span>
          </div>
          <RoundSteps state={view.round.state} scoringStep={view.round.scoring_step} reportFailed={view.round.report_failed} horizontal />
        </div>
      )}

      {b && (!view.round || ["waiting", "consented"].includes(view.round.state)) && (
        <div className="tech-booking">
          <span className="mark tone-attention">{ROUND_STATE[view.round?.state ?? "waiting"].label}</span>
          {b.password && (
            <div className="credentials">
              <CopyField label="Meeting ID" value={b.meeting_id} />
              <CopyField label="Password" value={b.password} />
              <CopyField label="Join page" value={b.candidate_join_url ?? ""} small />
            </div>
          )}
          {!b.password && <span className="muted small">Meeting ID {b.meeting_id}. HR sends the candidate the credentials.</span>}
          <span className="muted small">Follow the round live, with MITRA's plan and the integrity checks, in the live room. Its status and then the report appear here.</span>
        </div>
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
            {mode === "cancel" ? "Why is the MITRA interview round cancelled? They can be booked again." : "A note for the BU head (optional)"}
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
        <div className="actions">
          {(view.can_book || view.can_book_again) && (
            <label className="book-length">
              <span>Length</span>
              <select value={bookMinutes} onChange={(e) => setMinutes(Number(e.target.value))} disabled={busy}>
                {lengths.map((m) => (
                  <option key={m} value={m}>
                    {m} minutes{m === view.plan.minutes ? " (the plan's)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {view.can_book && (
            <Button variant="primary" icon={<CalendarPlus size={17} />} loading={busy} onClick={() => act("book")}>
              Book the MITRA interview round
            </Button>
          )}
          {view.can_retry_plan && plan.status === "failed" && (
            <Button icon={<RotateCcw size={16} />} loading={busy} onClick={() => act("plan")}>
              Prepare the plan again
            </Button>
          )}
          {b && (
            <ButtonLink variant="primary" to={`/candidates/${candidateId}/technical-room`} icon={<MonitorPlay size={17} />}>
              Open the live room
            </ButtonLink>
          )}
          {b?.password && (
            <Button
              icon={<ClipboardCopy size={16} />}
              onClick={async () => {
                if (await copyText(invitation(view))) toast("Copied a message you can send to the candidate");
              }}
            >
              Copy an invitation
            </Button>
          )}
          {view.can_send && (
            <Button variant="primary" icon={<Send size={16} />} onClick={() => setMode("send")}>
              Send to the BU head
            </Button>
          )}
          {view.can_book_again && (
            <Button icon={<CalendarPlus size={16} />} loading={busy} onClick={() => act("book")}>
              Book another MITRA round
            </Button>
          )}
          {stage === "technical_done" && (
            <ButtonLink to={`/candidates/${candidateId}/results`} icon={<ClipboardList size={17} />}>
              Open results
            </ButtonLink>
          )}
          {view.can_cancel && (
            <Button variant="ghost" icon={<XCircle size={16} />} onClick={() => setMode("cancel")}>
              Cancel the booking
            </Button>
          )}
        </div>
      )}
      {view.rounds.length > 1 && (
        <p className="muted small tech-rounds">
          {view.rounds.length} MITRA rounds so far. Each has its own report:{" "}
          {view.rounds.map((round, i) => (
            <span key={round.interview_id}>
              {i > 0 && ", "}
              <a href={`#/candidates/${candidateId}/results?round=${round.interview_id}`}>round {round.number}</a>
            </span>
          ))}
          .
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
