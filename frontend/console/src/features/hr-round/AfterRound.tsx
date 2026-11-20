// After the HR round: the work in progress, then MITRA's evaluation with the
// candidate's own words, the transcript, and HR's decision. Track H.

import {
  CheckCircle2,
  FileAudio,
  MessageSquareQuote,
  NotebookPen,
  RotateCcw,
  ScrollText,
  Undo2,
  UserCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../api";
import type { CandidateStage, HRRoundState, HRRoundView } from "../../contracts";
import { useGoBack } from "../../shell/ui/BackButton";
import { Button } from "../../shell/ui/Button";
import { Pips } from "../../shell/ui/Pips";
import { Spinner } from "../../shell/ui/Spinner";
import { useToast } from "../../shell/ui/Toast";

const STEPS = [
  { key: "waiting_recording", label: "Collecting the recording", note: "Daily finishes the file a few minutes after the call." },
  { key: "transcribing", label: "Transcribing and telling the voices apart", note: "Usually a minute or two." },
  { key: "evaluating", label: "Evaluating the candidate's answers", note: "Every quote is checked against the transcript." },
] as const;

function clock(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** With a live transcript there is no recording to wait for: it goes straight to evaluation. */
const LIVE_STEPS = [
  { key: "transcribed", label: "Transcribed live during the call", note: "" },
  { key: "evaluating", label: "Evaluating the candidate's answers", note: "Every quote is checked against the transcript. Usually under a minute." },
] as const;

function Progress({ status, live }: { status: HRRoundState["eval_status"]; live: boolean }) {
  const steps: readonly { key: string; label: string; note: string }[] = live ? LIVE_STEPS : STEPS;
  const at = steps.findIndex((s) => s.key === status);
  return (
    <section className="after-progress live-border" aria-live="polite">
      <h2 className="section-title">MITRA is working on the HR round</h2>
      <ol className="progress-steps">
        {steps.map((step, i) => (
          <li key={step.key} className={i < at ? "done" : i === at ? "active" : ""}>
            <span className="progress-mark">
              {i < at ? <CheckCircle2 size={18} /> : i === at ? <Spinner size={18} /> : <span className="progress-dot" />}
            </span>
            <span>
              <span className="progress-label">{step.label}</span>
              {i === at && <span className="progress-note">{step.note}</span>}
            </span>
          </li>
        ))}
      </ol>
      <p className="muted small">You can leave this page. The evaluation will be here, and the candidate moves on to "HR round done".</p>
    </section>
  );
}

function Failed({ round, candidateId, onChange }: { round: HRRoundState; candidateId: string; onChange: (v: HRRoundView) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<"retry" | "complete" | null>(null);
  const [completing, setCompleting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "retry" | "complete") {
    setBusy(kind);
    setError(null);
    try {
      const body = kind === "complete" ? { reason } : undefined;
      onChange(await api.post<HRRoundView>(`/candidates/${candidateId}/hr-round/${kind}`, body));
      toast(kind === "retry" ? "Trying again" : "HR round completed on your notes");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="after-failed">
      <h2 className="section-title">MITRA could not evaluate this round</h2>
      <p className="notice notice-attention">{round.eval_error}</p>
      {error && <p className="error">{error}</p>}
      {completing ? (
        <form
          className="reason"
          onSubmit={(e) => {
            e.preventDefault();
            act("complete");
          }}
        >
          <label htmlFor="complete-reason">Why complete it without MITRA's evaluation? Your notes stand in for it.</label>
          <textarea id="complete-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
          <div className="actions">
            <Button type="submit" variant="primary" icon={<NotebookPen size={16} />} loading={busy === "complete"} disabled={!reason.trim()}>
              Complete on my notes
            </Button>
            <Button variant="ghost" icon={<Undo2 size={16} />} onClick={() => setCompleting(false)}>
              Back
            </Button>
          </div>
        </form>
      ) : (
        <div className="actions">
          {round.can_retry && (
            <Button variant="primary" icon={<RotateCcw size={16} />} loading={busy === "retry"} onClick={() => act("retry")}>
              Try again
            </Button>
          )}
          {round.can_complete_on_notes && (
            <Button icon={<NotebookPen size={16} />} onClick={() => setCompleting(true)}>
              Complete on my notes
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

const RECOMMEND = {
  proceed_to_technical: { label: "Proceed to the MITRA interview round", tone: "good" },
  reject: { label: "Reject", tone: "closed" },
  discuss: { label: "Discuss: the evidence points neither way", tone: "attention" },
} as const;

const CONFIDENCE = {
  clear: "Voices told apart clearly",
  likely: "Voices told apart, likely correct",
  uncertain: "Voices could not be reliably told apart: check the transcript",
};

function Evaluation({ round }: { round: HRRoundState }) {
  const ev = round.evaluation!;
  const [highlight, setHighlight] = useState<{ turn: number; at: number } | null>(null);
  const [open, setOpen] = useState(false);
  const rec = RECOMMEND[ev.recommendation];

  // Scroll once the transcript is open: a line inside a closed <details> has
  // no box to scroll to.
  useEffect(() => {
    if (highlight) document.getElementById(`turn-${highlight.turn}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlight]);

  function jump(turn: number) {
    setOpen(true);
    setHighlight({ turn, at: Date.now() });
  }

  return (
    <div className="hr-eval">
      <article className="eval-card live-border">
        <span className="eval-kicker">MITRA recommends</span>
        <h2 className={`eval-rec tone-${rec.tone}`}>{rec.label}</h2>
        <p className="fit-summary">{ev.summary}</p>
        {round.attribution && (
          <span className={round.attribution.confidence === "uncertain" ? "mark mark-lg tone-attention" : "mark mark-lg tone-good"}>
            {round.attribution.method === "live"
              ? "Transcribed live: who said what is known"
              : round.attribution.method === "live_one_mic"
                ? "A phone call, transcribed live: voices told apart by sound"
                : CONFIDENCE[round.attribution.confidence]}
            {round.attribution.other_voices > 0 ? `, and ${round.attribution.other_voices} other voice heard` : ""}
          </span>
        )}
        <h3 className="brief-sub">The candidate, in their own words</h3>
        <ol className="ratings">
          {ev.ratings.map((r) => (
            <li key={r.name} className="rating">
              <div className="rating-head">
                <span className="rating-name">{r.name}</span>
              </div>
              <Pips rating={r.rating} none="Nothing to go on" />
              {r.reasoning && <p className="rating-why">{r.reasoning}</p>}
              {r.evidence.map((q, i) => (
                <button key={i} type="button" className="quote-jump" onClick={() => jump(q.turn_index)}>
                  <blockquote className="cv-quote">{q.text}</blockquote>
                  <span className="quote-at">
                    <MessageSquareQuote size={13} aria-hidden="true" /> at {clock(q.at_seconds)}, show in transcript
                  </span>
                </button>
              ))}
            </li>
          ))}
        </ol>
        {ev.findings_for_technical.length > 0 && (
          <>
            <h3 className="brief-sub">For the MITRA interview round</h3>
            <ul className="dealbreakers">
              {ev.findings_for_technical.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </>
        )}
      </article>

      {round.transcript && (
        <details className="transcript-panel" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
          <summary>
            <ScrollText size={16} aria-hidden="true" /> Transcript ({round.transcript.length} turns)
          </summary>
          <ol className="transcript transcript-hr">
            {round.transcript.map((t) => (
              <li key={t.index} id={`turn-${t.index}`} className={`line line-${t.speaker === "hr" ? "mitra" : "you"} speaker-${t.speaker}${highlight?.turn === t.index ? " lit" : ""}`}>
                <span className="line-who">
                  {t.speaker === "hr" ? "HR" : t.speaker === "candidate" ? "Candidate" : "Other voice"}
                  <span className="line-at">{clock(t.start)}</span>
                </span>
                <p>{t.text}</p>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

interface Choice {
  to: CandidateStage;
  label: string;
  icon: ReactNode;
  variant: "primary" | "secondary" | "danger";
  prompt?: string;
}

const CHOICES: Choice[] = [
  { to: "ready_for_technical", label: "Proceed to MITRA interview", icon: <UserCheck size={17} />, variant: "primary" },
  { to: "rejected", label: "Reject", icon: <XCircle size={17} />, variant: "danger", prompt: "Why reject? This is kept on their record." },
];

export function HRDecision({ view }: { view: HRRoundView }) {
  const toast = useToast();
  const goBack = useGoBack(`/candidates/${view.candidate.id}`);
  const [picked, setPicked] = useState<Choice | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (view.candidate.stage !== "hr_done") return null;

  async function commit(choice: Choice) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/candidates/${view.candidate.id}/move`, { to: choice.to, reason: reason.trim() || undefined });
      toast(`${view.candidate.name}: ${choice.label}. Saved.`, choice.to === "rejected" ? "urgent" : "ok");
      setPicked(null);
      goBack();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="decision-bar decision-stack">
      {picked ? (
        <form
          className="reason"
          onSubmit={(e) => {
            e.preventDefault();
            commit(picked);
          }}
        >
          <label htmlFor="hr-reason">{picked.prompt}</label>
          <textarea id="hr-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
          <div className="actions">
            <Button type="submit" variant={picked.variant === "danger" ? "danger" : "primary"} icon={picked.icon} loading={busy} disabled={!reason.trim()}>
              {picked.label}
            </Button>
            <Button variant="ghost" icon={<Undo2 size={16} />} onClick={() => setPicked(null)}>
              Back
            </Button>
          </div>
        </form>
      ) : (
        <div className="actions">
          <span className="decision-q">Your decision after the HR round</span>
          {CHOICES.map((c) => (
            <Button key={c.to} variant={c.variant} icon={c.icon} loading={busy && !c.prompt} disabled={busy} onClick={() => (c.prompt ? setPicked(c) : commit(c))}>
              {c.label}
            </Button>
          ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export function AfterRound({ view, onChange }: { view: HRRoundView; onChange: (v: HRRoundView) => void }) {
  const round = view.round!;
  if (round.eval_status === "ready" && round.evaluation) return <Evaluation round={round} />;
  if (round.eval_status === "failed") return <Failed round={round} candidateId={view.candidate.id} onChange={onChange} />;
  if (["waiting_recording", "transcribing", "evaluating"].includes(round.eval_status))
    // Live lines go straight to the evaluation; a round waiting on its recording
    // (checked against the live lines after the call) shows the recording's steps.
    return (
      <Progress
        status={round.eval_status}
        live={(round.attribution?.method === "live" || round.attribution?.method === "live_one_mic") && round.eval_status === "evaluating"}
      />
    );
  return (
    <section className="after-failed">
      <p className="muted">
        <FileAudio size={16} aria-hidden="true" /> {view.candidate.stage === "hr_done" ? "Completed on HR's notes." : "Waiting for the round to end."}
      </p>
    </section>
  );
}

