// One candidate, from CV upload to the screening gate. The page follows the
// screening row: waiting (check the CV), running (poll), failed (try again),
// ready (the fit, the questions and the decision). What this person may do
// comes from the server (`next`, `can_proceed`, `can_reupload`).

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import {
  STAGE_LABELS,
  STAGE_TONES,
  type CandidateDetail,
  type CandidateStage,
  type CompetencyRating,
  type DecisionEntry,
  type FitAnalysis,
  type HRQuestion,
} from "../../contracts";
import { ScoreMeter } from "./ScoreMeter";

const POLL_MS = 2500;

// ---- waiting: check what MITRA read ---------------------------------------------------------

function CheckCv({ c, onUpdate }: { c: CandidateDetail; onUpdate: (c: CandidateDetail) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function replace(file: File) {
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    try {
      onUpdate(await api.postForm<CandidateDetail>(`/candidates/${c.id}/cv`, form));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function proceed() {
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.post<CandidateDetail>(`/candidates/${c.id}/proceed`));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
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
            <button type="button" className="btn btn-primary" disabled={busy} onClick={proceed}>
              {c.screening_status === "failed" ? "Try screening again" : "Proceed to screening"}
            </button>
          )}
          {c.can_reupload && (
            <>
              <button type="button" className="btn" disabled={busy} onClick={() => input.current?.click()}>
                Re-upload CV
              </button>
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
      <h2 className="section-title">MITRA is screening the CV</h2>
      <p className="muted">This usually takes under a minute. You can leave this page; the result will be here.</p>
      <ol className="steps">
        <li>Removing the name, contact details and institutions</li>
        <li>Rating the CV against each thing the job asks for</li>
        <li>Checking every quoted line is really in the CV</li>
        <li>Writing ten questions for the HR round</li>
      </ol>
    </section>
  );
}

// ---- ready: the fit ---------------------------------------------------------------------------

const RATING_WORDS = ["Clearly lacking", "Adjacent only", "Claimed, no specifics", "Specific work", "Specific, owned, measured"];

function Pips({ rating }: { rating: number | null }) {
  if (rating === null) return <span className="pips-none">No evidence in the CV</span>;
  return (
    <span className="pips" role="img" aria-label={`${rating} of 4: ${RATING_WORDS[rating]}`}>
      {[1, 2, 3, 4].map((n) => (
        <span key={n} className={n <= rating ? "pip on" : "pip"} />
      ))}
      <span className="pips-word">{RATING_WORDS[rating]}</span>
    </span>
  );
}

function RatingRow({ r, total }: { r: CompetencyRating; total: number }) {
  return (
    <li className="rating">
      <div className="rating-head">
        <span className="rating-name">{r.competency_name}</span>
        <span className="rating-weight">{Math.round((r.weight / total) * 100)}% of the job</span>
      </div>
      <Pips rating={r.rating} />
      {r.reasoning && <p className="rating-why">{r.reasoning}</p>}
      {r.evidence.map((q, i) => (
        <blockquote key={i} className="cv-quote">
          {q}
        </blockquote>
      ))}
    </li>
  );
}

function Fit({ fit, minAssessed }: { fit: FitAnalysis; minAssessed: number }) {
  const total = fit.ratings.reduce((s, r) => s + r.weight, 0) || 1;
  const below = fit.score_percent < fit.threshold_percent;
  const enough = fit.assessed_weight_percent >= minAssessed;
  return (
    <article className="fit">
      <div className="fit-score">
        <ScoreMeter score={fit.score_percent} threshold={fit.threshold_percent} enoughEvidence={enough} />
        {enough ? (
          <p className="fit-caption">
            {below ? "Below" : "At or above"} the {fit.threshold_percent}% cut-off, judged on {fit.assessed_weight_percent}%
            of the job's weight. Competencies the CV says nothing about are left out, not scored as zero.
          </p>
        ) : (
          <p className="fit-caption fit-caption-warn">
            Too little evidence to judge. The CV speaks to only {fit.assessed_weight_percent}% of what the job asks for, so
            this score rests on very little. MITRA needs at least {minAssessed}% before a score means anything.
          </p>
        )}
      </div>
      {fit.summary && <p className="fit-summary">{fit.summary}</p>}
      {fit.experience_note && <p className="fit-experience">{fit.experience_note}</p>}

      <h3 className="brief-sub">Against each thing the job asks for</h3>
      <ol className="ratings">
        {fit.ratings.map((r) => (
          <RatingRow key={r.competency_id} r={r} total={total} />
        ))}
      </ol>
      {fit.identity_fields_removed.length > 0 && (
        <p className="muted small">
          Removed from the CV before screening: {fit.identity_fields_removed.join(", ")}. Quotes above are from that
          version.
        </p>
      )}
    </article>
  );
}

function Questions({ questions }: { questions: HRQuestion[] }) {
  return (
    <section className="questions" aria-labelledby="hr-questions">
      <h2 id="hr-questions" className="section-title">
        Questions for the HR round
      </h2>
      <ol className="question-list">
        {questions.map((q) => (
          <li key={q.id}>
            <p className="question-text">{q.text}</p>
            <p className="question-why">{q.why}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

// ---- the decision -----------------------------------------------------------------------------

interface Choice {
  to: CandidateStage;
  label: string;
  primary?: boolean;
  danger?: boolean;
  needsReason: boolean;
  prompt?: string;
}

function choicesFor(c: CandidateDetail): Choice[] {
  const below = c.below_threshold;
  const all: Record<string, Choice> = {
    ready_for_hr: below
      ? { to: "ready_for_hr", label: "Proceed anyway", needsReason: true, prompt: "Why should they go ahead despite the score?" }
      : { to: "ready_for_hr", label: "Begin HR round", primary: true, needsReason: false },
    parked: { to: "parked", label: "Not now", needsReason: true, prompt: "Why not now? HR and the BU head will read this." },
    rejected: { to: "rejected", label: "Reject", danger: true, needsReason: true, prompt: "Why reject? This is kept on their record." },
  };
  const order: CandidateStage[] = below ? ["rejected", "ready_for_hr"] : ["ready_for_hr", "parked"];
  return order.filter((s) => c.next.includes(s)).map((s) => all[s]);
}

function Decide({ c, onDone }: { c: CandidateDetail; onDone: () => void }) {
  const [picked, setPicked] = useState<Choice | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choices = choicesFor(c);
  if (choices.length === 0) return null;

  async function commit(choice: Choice) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/candidates/${c.id}/move`, { to: choice.to, reason: reason.trim() || undefined });
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
            <button type="submit" className={picked.danger ? "btn btn-danger" : "btn btn-primary"} disabled={busy || !reason.trim()}>
              {picked.label}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => setPicked(null)}>
              Back
            </button>
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
            <button
              key={choice.to}
              type="button"
              className={choice.danger ? "btn btn-danger" : choice.primary ? "btn btn-primary" : "btn"}
              disabled={busy}
              onClick={() => (choice.needsReason ? setPicked(choice) : commit(choice))}
            >
              {choice.label}
            </button>
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

const ACTORS = { system: "MITRA", hr: "HR", bu_head: "BU head" };

function History({ entries }: { entries: DecisionEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <section aria-labelledby="history">
      <h2 id="history" className="section-title">
        History
      </h2>
      <ol className="history">
        {entries.map((d, i) => (
          <li key={i}>
            <span className="history-what">
              {ACTORS[d.actor]} moved them to <strong>{STAGE_LABELS[d.to]}</strong>
            </span>
            {d.at && <span className="history-when">{new Date(d.at).toLocaleString()}</span>}
            {d.reason && <p className="history-reason">{d.reason}</p>}
            {d.model_recommendation && <p className="history-model">MITRA's reading at the time: {d.model_recommendation}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}

// ---- the page ---------------------------------------------------------------------------------

export function CandidatePage() {
  const { candidateId } = useParams();
  const [c, setC] = useState<CandidateDetail | null>(null);
  const [history, setHistory] = useState<DecisionEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [detail, decisions] = await Promise.all([
        api.get<CandidateDetail>(`/candidates/${candidateId}`),
        api.get<{ decisions: DecisionEntry[] }>(`/candidates/${candidateId}/decisions`),
      ]);
      setC(detail);
      setHistory(decisions.decisions);
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
  if (!c) return <div className="page muted">Loading…</div>;

  return (
    <div className="page page-wide">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to={`/jobs/${c.job.id}/candidates`}>{c.job.title}</Link>
      </nav>
      <header className="page-head">
        <div>
          <h1 className="page-title">{c.name}</h1>
          <p className="page-sub">
            Candidate for {c.job.title}, {c.job.bu_name}.
          </p>
        </div>
        <span className={`mark mark-lg tone-${STAGE_TONES[c.stage]}`}>{STAGE_LABELS[c.stage]}</span>
      </header>

      {(c.screening_status === "waiting" || c.screening_status === "failed") && <CheckCv c={c} onUpdate={setC} />}
      {c.screening_status === "running" && <Screening />}
      {c.screening_status === "ready" && c.fit && (
        <div className="screened">
          <Fit fit={c.fit} minAssessed={c.min_assessed_percent} />
          {c.hr_questions && <Questions questions={c.hr_questions.questions} />}
        </div>
      )}
      <History entries={history} />
      {c.screening_status === "ready" && <Decide c={c} onDone={load} />}
    </div>
  );
}
