// One candidate, read in the order a person needs it: what happens now (and
// the buttons for it), the four facts at a glance, then strengths and gaps,
// the CV against the job, and, at the side, MITRA's summary, the HR questions
// and the history. What this person may do comes from the server (`next`,
// `can_proceed`, `can_reupload`). Track H.

import { ArrowRightCircle, Clock3, FileCheck2, ScanSearch, SkipForward, Undo2, Upload, UserCheck, Video, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import {
  STAGE_LABELS,
  STAGE_TONES,
  type CandidateDetail,
  type CandidateStage,
  type DecisionEntry,
} from "../../contracts";
import { BackButton, useGoBack } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { Spinner } from "../../shell/ui/Spinner";
import { useToast } from "../../shell/ui/Toast";
import { useSession } from "../../session";
import { TechnicalEntry } from "../results/TechnicalEntry";
import { ScreeningSteps } from "./AddCandidate";
import type { Results } from "../results/types";
import { CandidateEvidence, Glance, History, NextStepText, Sidebar } from "./CandidateParts";
import "./candidate.css";

const POLL_MS = 2500;

// ---- waiting: check what MITRA read ---------------------------------------------------------

function CheckCv({ c, onUpdate }: { c: CandidateDetail; onUpdate: (c: CandidateDetail) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"replace" | "proceed" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function replace(file: File) {
    setBusy("replace");
    setError(null);
    const form = new FormData();
    form.append("file", file);
    try {
      onUpdate(await api.postForm<CandidateDetail>(`/candidates/${c.id}/cv`, form));
      toast(`Replaced the CV with ${file.name}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function proceed() {
    setBusy("proceed");
    setError(null);
    try {
      onUpdate(await api.post<CandidateDetail>(`/candidates/${c.id}/proceed`));
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  const first = c.name.split(/\s+/)[0] || c.name;
  return (
    <section className="cv-ready" aria-labelledby="cv-ready-title">
      <div className="cv-ready-main">
        <span className="cv-ready-icon" aria-hidden="true">
          <FileCheck2 size={44} strokeWidth={1.6} />
        </span>
        <h2 id="cv-ready-title" className="cv-ready-title">
          {first}'s résumé is uploaded
        </h2>
        <p className="cv-ready-file">{c.source_filename ?? "CV"}</p>
        {c.screening_status === "failed" && c.screening_error && (
          <p className="notice notice-attention">Screening did not finish: {c.screening_error} You can try again.</p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {(c.can_proceed || c.can_reupload) && (
          <div className="cv-ready-actions">
            {c.can_proceed && (
              <Button variant="primary" icon={<ScanSearch size={18} />} loading={busy === "proceed"} disabled={busy !== null} onClick={proceed}>
                {c.screening_status === "failed" ? "Try screening again" : "Proceed to screening"}
              </Button>
            )}
            {c.can_reupload && (
              <>
                <Button icon={<Upload size={17} />} loading={busy === "replace"} disabled={busy !== null} onClick={() => input.current?.click()}>
                  Re-upload
                </Button>
                <input
                  ref={input}
                  type="file"
                  accept=".pdf,.docx,.txt,.md"
                  className="visually-hidden"
                  aria-label="Replacement CV"
                  onChange={(e) => e.target.files?.[0] && replace(e.target.files[0])}
                />
              </>
            )}
          </div>
        )}
      </div>
      <ScreeningSteps />
    </section>
  );
}

// ---- running ----------------------------------------------------------------------------------

function Screening() {
  return (
    <section className="screening-now" aria-live="polite">
      <div className="screening-head">
        <Spinner size={34} />
        <div>
          <h2 className="section-title">MITRA is screening the CV</h2>
          <p className="muted">This usually takes under a minute. You can leave this page; the result will be here.</p>
        </div>
      </div>
      <ul className="steps-static">
        <li>Removing the name, contact details and institutions</li>
        <li>Rating the CV against each thing the job asks for</li>
        <li>Checking every quoted line is really in the CV</li>
        <li>Writing ten questions for the HR round</li>
      </ul>
    </section>
  );
}

// ---- the decision -----------------------------------------------------------------------------

interface Choice {
  to: CandidateStage;
  label: string;
  icon: ReactNode;
  primary?: boolean;
  danger?: boolean;
  needsReason: boolean;
  prompt?: string;
}

function choicesFor(c: CandidateDetail): Choice[] {
  const below = c.below_threshold;
  const all: Record<string, Choice> = {
    ready_for_hr: below
      ? { to: "ready_for_hr", label: "Proceed anyway", icon: <ArrowRightCircle size={17} />, needsReason: true, prompt: "Why should they go ahead despite the score?" }
      : { to: "ready_for_hr", label: "Begin HR round", icon: <UserCheck size={17} />, primary: true, needsReason: false },
    parked: { to: "parked", label: "Not now", icon: <Clock3 size={16} />, needsReason: true, prompt: "Why not now? HR and the BU head will read this." },
    rejected: { to: "rejected", label: "Reject", icon: <XCircle size={17} />, danger: true, needsReason: true, prompt: "Why reject? This is kept on their record." },
  };
  // No "Not now": a person proceeds or rejects (product decision, 2026-11-19).
  const order: CandidateStage[] = c.stage === "ready_for_hr" ? ["rejected"] : below ? ["rejected", "ready_for_hr"] : ["ready_for_hr", "rejected"];
  return order.filter((s) => c.next.includes(s)).map((s) => all[s]);
}

/**
 * The decision at this stage. A rejection is the end of the work here, so it
 * returns to the list; going ahead stays on the page, which then shows the
 * next step (for "Begin HR round": open the room, or reject).
 */
function Decide({ c, onDone, only }: { c: CandidateDetail; onDone: () => void; only?: CandidateStage[] }) {
  const goBack = useGoBack(`/jobs/${c.job.id}/candidates`);
  const [picked, setPicked] = useState<Choice | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const choices = choicesFor(c).filter((choice) => !only || only.includes(choice.to));
  if (choices.length === 0) return null;

  async function commit(choice: Choice) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/candidates/${c.id}/move`, { to: choice.to, reason: reason.trim() || undefined });
      toast(`${c.name}: ${STAGE_LABELS[choice.to]}. Saved.`, choice.to === "rejected" ? "urgent" : "ok");
      setPicked(null);
      setReason("");
      if (choice.to === "rejected") goBack();
      else onDone();
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
          <label htmlFor="reason">{picked.prompt}</label>
          <textarea id="reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
          <div className="actions">
            <Button type="submit" variant={picked.danger ? "danger" : "primary"} icon={picked.icon} loading={busy} disabled={!reason.trim()}>
              {picked.label}
            </Button>
            <Button variant="ghost" icon={<Undo2 size={16} />} disabled={busy} onClick={() => setPicked(null)}>
              Back
            </Button>
          </div>
        </form>
      ) : (
        <div className="actions">
          {c.below_threshold && (
            <span className="decision-q">
              {c.enough_evidence ? "This CV scored below the cut-off." : "This CV has too little evidence to judge."}
            </span>
          )}
          {choices.map((choice) => (
            <Button
              key={choice.to}
              variant={choice.danger ? "danger" : choice.primary ? "primary" : "secondary"}
              icon={choice.icon}
              loading={busy && !choice.needsReason}
              disabled={busy}
              onClick={() => (choice.needsReason ? setPicked(choice) : commit(choice))}
            >
              {choice.label}
            </Button>
          ))}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

// ---- the HR round ---------------------------------------------------------------------------

const AFTER_BOOKING: CandidateStage[] = ["hr_booked", "hr_done", "ready_for_technical", "technical_booked", "technical_done", "with_bu_head", "shortlisted", "on_hold"];

/** The HR round is optional: HR may skip it, with an optional reason, before it has happened. */
function SkipHR({ c, onDone }: { c: CandidateDetail; onDone: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function skip() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/candidates/${c.id}/hr-round/skip`, { reason: reason.trim() });
      toast(`${c.name}: HR round skipped. MITRA is preparing the interview round.`);
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Button variant="ghost" icon={<SkipForward size={16} />} onClick={() => setOpen(true)}>
        Skip HR round
      </Button>
    );
  }
  return (
    <form
      className="reason skip-hr"
      onSubmit={(e) => {
        e.preventDefault();
        skip();
      }}
    >
      <label htmlFor="skip-reason">
        Skip the HR round and go straight to the MITRA interview round? <span className="muted">Reason (optional)</span>
      </label>
      <textarea id="skip-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
      <div className="actions">
        <Button type="submit" variant="primary" icon={<SkipForward size={16} />} loading={busy}>
          Skip HR round
        </Button>
        <Button variant="ghost" icon={<Undo2 size={16} />} disabled={busy} onClick={() => setOpen(false)}>
          Back
        </Button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function HRRoundEntry({ c, onChange }: { c: CandidateDetail; onChange: () => void }) {
  const isHR = c.next.length > 0 || c.can_proceed;
  if (c.stage === "ready_for_hr") {
    // The room is started from inside it: one place to book, invite and join.
    return isHR ? (
      <section className="hr-entry">
        <ButtonLink variant="primary" to={`/candidates/${c.id}/hr-round`} icon={<Video size={17} />}>
          Open the HR round room
        </ButtonLink>
        <SkipHR c={c} onDone={onChange} />
      </section>
    ) : null;
  }
  if (AFTER_BOOKING.includes(c.stage)) {
    return (
      <section className="hr-entry">
        <div>
          <h2 className="section-title">HR round</h2>
          <p className="muted">{c.stage === "hr_booked" ? "Booked. Send the credentials, then join from here." : "The HR round has happened."}</p>
        </div>
        <ButtonLink variant={c.stage === "hr_booked" || c.stage === "hr_done" ? "primary" : "secondary"} to={`/candidates/${c.id}/hr-round`} icon={<Video size={17} />}>
          {c.stage === "hr_done" ? "Open the HR round to decide" : c.stage === "hr_booked" ? "Open the HR round" : "See the HR round"}
        </ButtonLink>
        {c.stage === "hr_booked" && isHR && <SkipHR c={c} onDone={onChange} />}
      </section>
    );
  }
  return null;
}

// ---- the page ---------------------------------------------------------------------------------

export function CandidatePage() {
  const { candidateId } = useParams();
  const { me } = useSession();
  const [c, setC] = useState<CandidateDetail | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [history, setHistory] = useState<DecisionEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [detail, decisions, summary] = await Promise.all([
        api.get<CandidateDetail>(`/candidates/${candidateId}`),
        api.get<{ decisions: DecisionEntry[] }>(`/candidates/${candidateId}/decisions`),
        api.get<Results>(`/candidates/${candidateId}/results`).catch(() => null),
      ]);
      setC(detail);
      setHistory(decisions.decisions);
      setResults(summary);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [candidateId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (c?.screening_status !== "running") return;
    const timer = window.setTimeout(load, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [c, load]);

  if (error && !c) {
    return (
      <div className="page">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!c || !me) return <PageSkeleton />;

  const screened = c.screening_status === "ready" && c.fit;
  // Each stage shows only its own action in "What happens now".
  const decidesHere = screened && (c.stage === "screened" || c.stage === "parked");
  const hrStep = c.stage === "ready_for_hr" || c.stage === "hr_booked" || c.stage === "hr_done";
  // Until the HR round has happened, its questions lead the page.
  const beforeHR = ["screened", "parked", "ready_for_hr", "hr_booked"].includes(c.stage);
  const questions = c.hr_questions?.questions ?? [];
  const techStep = ["ready_for_technical", "technical_booked", "technical_done", "with_bu_head", "on_hold"].includes(c.stage);

  return (
    <div className="page page-wide cand">
      <BackButton fallback={`/jobs/${c.job.id}/candidates`} label={c.job.title} />
      <header className="page-head cand-head">
        <div>
          <h1 className="page-title">{c.name}</h1>
          <p className="page-sub">
            {c.job.title}, {c.job.bu_name}
            {c.source_filename ? `. CV: ${c.source_filename}` : ""}
          </p>
        </div>
        <span className={`mark mark-lg tone-${STAGE_TONES[c.stage]}`}>{STAGE_LABELS[c.stage]}</span>
      </header>

      <section className="next-step" aria-labelledby="next-title">
        <div className="next-copy">
          <h2 id="next-title" className="next-title">
            What happens now
          </h2>
          <NextStepText stage={c.stage} role={me.role} />
        </div>
        <div className="next-actions">
          {decidesHere && <Decide c={c} onDone={load} />}
          {hrStep && <HRRoundEntry c={c} onChange={load} />}
          {c.stage === "ready_for_hr" && <Decide c={c} onDone={load} only={["rejected"]} />}
          {techStep && <TechnicalEntry candidateId={c.id} stage={c.stage} isBuHead={me.role === "bu_head"} onChange={load} />}
        </div>
      </section>

      {(c.screening_status === "waiting" || c.screening_status === "failed") && <CheckCv c={c} onUpdate={setC} />}
      {c.screening_status === "running" && <Screening />}

      {screened && (
        <>
          <Glance c={c} results={results} history={history} />
          <div className="cand-grid">
            <CandidateEvidence fit={c.fit!} results={results} questionsFirst={beforeHR ? questions : []} />
            <Sidebar fit={c.fit!} questions={beforeHR ? [] : questions} gaps={beforeHR ? results : null} history={<History entries={history} />} />
          </div>
        </>
      )}
      {!screened && <History entries={history} />}
    </div>
  );
}
