// One candidate, read in the order a person needs it: what happens now (and
// the buttons for it), the four facts at a glance, then strengths and gaps,
// the CV against the job, and, at the side, MITRA's summary, the HR questions
// and the history. What this person may do comes from the server (`next`,
// `can_proceed`, `can_reupload`). Track H.

import {
  ArrowRightCircle,
  CalendarPlus,
  Clock3,
  ScanSearch,
  Undo2,
  Upload,
  UserCheck,
  Video,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import {
  STAGE_LABELS,
  STAGE_TONES,
  type CandidateDetail,
  type CandidateStage,
  type DecisionEntry,
} from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { Spinner } from "../../shell/ui/Spinner";
import { useToast } from "../../shell/ui/Toast";
import { useSession } from "../../session";
import { TechnicalEntry } from "../results/TechnicalEntry";
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

  return (
    <section className="check-cv">
      <div className="check-cv-head">
        <h2 className="section-title">Check the CV MITRA read</h2>
        <p className="muted">
          This is the text MITRA took from {c.source_filename ?? "the file"}. If it is the wrong file or reads badly,
          upload it again. Nothing is screened until you choose Proceed.
        </p>
      </div>
      <pre className="cv-text" tabIndex={0} aria-label="Text read from the CV">
        {c.cv_text}
      </pre>
      {c.screening_status === "failed" && c.screening_error && (
        <p className="notice notice-attention">Screening did not finish: {c.screening_error} You can try again.</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {(c.can_proceed || c.can_reupload) && (
        <div className="decision-bar">
          {c.can_proceed && (
            <Button variant="primary" icon={<ScanSearch size={17} />} loading={busy === "proceed"} disabled={busy !== null} onClick={proceed}>
              {c.screening_status === "failed" ? "Try screening again" : "Proceed to screening"}
            </Button>
          )}
          {c.can_reupload && (
            <>
              <Button icon={<Upload size={16} />} loading={busy === "replace"} disabled={busy !== null} onClick={() => input.current?.click()}>
                Re-upload CV
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
  const order: CandidateStage[] = below ? ["rejected", "ready_for_hr"] : ["ready_for_hr", "parked"];
  return order.filter((s) => c.next.includes(s)).map((s) => all[s]);
}

function Decide({ c, onDone }: { c: CandidateDetail; onDone: () => void }) {
  const [picked, setPicked] = useState<Choice | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const choices = choicesFor(c);
  if (choices.length === 0) return null;

  async function commit(choice: Choice) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/candidates/${c.id}/move`, { to: choice.to, reason: reason.trim() || undefined });
      toast(`${c.name}: ${STAGE_LABELS[choice.to]}`);
      setPicked(null);
      setReason("");
      onDone();
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

function HRRoundEntry({ c, onBooked }: { c: CandidateDetail; onBooked: () => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isHR = c.next.length > 0 || c.can_proceed;

  async function book() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/candidates/${c.id}/hr-round`);
      toast(`HR round booked for ${c.name}`);
      navigate(`/candidates/${c.id}/hr-round`, { viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      onBooked();
    }
  }

  if (c.stage === "ready_for_hr") {
    return (
      <section className="hr-entry live-border">
        <div>
          <h2 className="section-title">Ready for the HR round</h2>
          <p className="muted">Booking creates a private room and the meeting ID and password to send {c.name}.</p>
          {error && <p className="error">{error}</p>}
        </div>
        {isHR && (
          <Button variant="primary" icon={<CalendarPlus size={17} />} loading={busy} onClick={book}>
            Book the HR round
          </Button>
        )}
      </section>
    );
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
          {hrStep && <HRRoundEntry c={c} onBooked={load} />}
          {techStep && <TechnicalEntry candidateId={c.id} stage={c.stage} isBuHead={me.role === "bu_head"} onChange={load} />}
        </div>
      </section>

      {(c.screening_status === "waiting" || c.screening_status === "failed") && <CheckCv c={c} onUpdate={setC} />}
      {c.screening_status === "running" && <Screening />}

      {screened && (
        <>
          <Glance c={c} results={results} history={history} />
          <div className="cand-grid">
            <CandidateEvidence fit={c.fit!} results={results} />
            <Sidebar fit={c.fit!} questions={c.hr_questions?.questions ?? []} history={<History entries={history} />} />
          </div>
        </>
      )}
      {!screened && <History entries={history} />}
    </div>
  );
}
