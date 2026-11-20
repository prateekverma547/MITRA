// The BU head's results page. Everything here is evidence Track H already
// holds (the CV against the job, the HR round), plus a bounded slot for
// Track I's MITRA interview report. The BU head decides at the bottom; HR and admins
// read the same page without the decision.

import { Columns3, PauseCircle, ScrollText, Star, Undo2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS, STAGE_TONES, type CandidateStage } from "../../contracts";
import { BackButton, useGoBack } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { Pips } from "../../shell/ui/Pips";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { useToast } from "../../shell/ui/Toast";
import { MoreInsight } from "../../shell/ui/MoreInsight";
import { ChartCard } from "../../shell/viz/ChartCard";
import { TooltipLayer } from "../../shell/viz/Tooltip";
import { Rubric, RubricTable } from "./Rubric";
import { TechnicalReport } from "./TechnicalReport";
import { HR_RECOMMENDATION, TECHNICAL_SIGNAL, type Results, type StrengthOrGap } from "./types";
import "./results.css";
import "../screening/candidate.css";

const SOURCE = { cv: "CV", hr_round: "HR round" };
const ACTORS = { system: "MITRA", hr: "HR", bu_head: "BU head" };

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
}

// ---- at a glance --------------------------------------------------------------------------------

function Glance({ r }: { r: Results }) {
  const hr = r.hr_round ? HR_RECOMMENDATION[r.hr_round.recommendation] : null;
  const tech = r.technical.report?.recommendation;
  const m = r.jd_match;
  const passes = m ? m.enough_evidence && m.passes_screen : false;
  return (
    <section className="glance" aria-label="At a glance">
      <div className={`glance-tile${m ? (passes ? " g-good" : " g-attention") : " g-muted"}`}>
        <span className="glance-title">CV fit to the job</span>
        <span className="glance-value">
          {m ? `${Math.round(m.score_percent)}%` : "Not screened"}
          {m && (
            <span className="glance-meter" aria-hidden="true">
              <span style={{ width: `${Math.min(100, m.score_percent)}%` }} className={passes ? "on" : "off"} />
              <i style={{ left: `${m.threshold_percent}%` }} />
            </span>
          )}
        </span>
        {m && <span className="glance-note">{!m.enough_evidence ? "Too little evidence to judge" : passes ? `At or above the ${m.threshold_percent}% cut-off` : `Below the ${m.threshold_percent}% cut-off`}</span>}
      </div>
      <div className={`glance-tile${hr ? ` g-${hr.tone === "closed" ? "attention" : hr.tone}` : " g-muted"}`}>
        <span className="glance-title">HR round</span>
        <span className="glance-value">{hr ? hr.label : r.hr_skipped ? "Skipped" : "No evaluation"}</span>
        <span className="glance-note">
          {r.hr_round?.attribution_confidence === "uncertain"
            ? "Voices were hard to tell apart"
            : hr
              ? "MITRA's recommendation"
              : r.hr_skipped
                ? (r.hr_skipped.reason ?? `Skipped by ${r.hr_skipped.by ?? "HR"}`)
                : "None recorded"}
        </span>
      </div>
      <div className={`glance-tile${tech ? "" : " g-muted"}`}>
        <span className="glance-title">MITRA interview round</span>
        <span className="glance-value">{tech ? TECHNICAL_SIGNAL[tech] : "No report yet"}</span>
        <span className="glance-note">{tech ? "MITRA's report, below" : "Appears when the interview is scored"}</span>
      </div>
      <div className="glance-tile">
        <span className="glance-title">Sent to you</span>
        <span className="glance-value">{r.sent_to_bu_head ? r.sent_to_bu_head.by ?? "HR" : "Not yet"}</span>
        <span className="glance-note">{r.sent_to_bu_head?.note ? `"${r.sent_to_bu_head.note}"` : r.sent_to_bu_head?.at ? when(r.sent_to_bu_head.at) : "HR sends it after the MITRA interview round"}</span>
      </div>
    </section>
  );
}

// ---- strengths and gaps -------------------------------------------------------------------------

function Points({ title, caption, items, empty }: { title: string; caption: string; items: StrengthOrGap[]; empty: string }) {
  return (
    <section className="card-viz">
      <header className="card-viz-head">
        <div>
          <h2 className="card-title">{title}</h2>
          <p className="card-caption">{caption}</p>
        </div>
      </header>
      {items.length === 0 ? (
        <p className="empty-note">{empty}</p>
      ) : (
        <ul className="points">
          {items.map((p, i) => (
            <li key={i}>
              <div className="point-head">
                <span className="point-name">{p.name}</span>
                <span className="source-chip">{SOURCE[p.source]}</span>
              </div>
              {p.kind === "no_evidence" ? (
                <span className="pips-none">No evidence in the CV, {Math.round(p.weight_percent ?? 0)}% of the job</span>
              ) : (
                <Pips rating={p.rating} />
              )}
              {p.why && <p className="rating-why">{p.why}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---- the HR round -------------------------------------------------------------------------------

function HRRound({ hr, candidateId }: { hr: NonNullable<Results["hr_round"]>; candidateId: string }) {
  const rec = HR_RECOMMENDATION[hr.recommendation];
  return (
    <section className="card-viz results-hr">
      <header className="card-viz-head">
        <div>
          <h2 className="card-title">The HR round</h2>
          <p className="card-caption">
            Evaluated by MITRA from the recording{hr.evaluated_at ? ` on ${when(hr.evaluated_at)}` : ""}. Quotes are the candidate's own words, checked
            against the transcript.
          </p>
        </div>
        <ButtonLink size="sm" variant="ghost" to={`/candidates/${candidateId}/hr-round`} icon={<ScrollText size={15} />}>
          Transcript
        </ButtonLink>
      </header>
      <p className={`eval-rec-sm tone-${rec.tone}`}>MITRA recommended: {rec.label}</p>
      <p className="fit-summary">{hr.summary}</p>
      <ol className="ratings ratings-2">
        {hr.ratings.map((s) => (
          <li key={s.name} className="rating">
            <span className="rating-name">{s.name}</span>
            <Pips rating={s.rating} none="Nothing to go on" />
            {s.evidence[0] && <blockquote className="cv-quote">{s.evidence[0].text}</blockquote>}
          </li>
        ))}
      </ol>
      {hr.findings_for_technical.length > 0 && (
        <>
          <h3 className="brief-sub">Raised for the MITRA interview round</h3>
          <ul className="dealbreakers">
            {hr.findings_for_technical.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </>
      )}
      {hr.notes && (
        <p className="hr-notes">
          <span className="label">HR's notes</span> {hr.notes}
        </p>
      )}
    </section>
  );
}

// ---- history ------------------------------------------------------------------------------------

function History({ entries }: { entries: Results["decisions"] }) {
  if (entries.length === 0) return null;
  return (
    <section aria-labelledby="results-history">
      <h2 id="results-history" className="section-title">
        How they got here
      </h2>
      <ol className="history">
        {entries.map((d, i) => (
          <li key={i}>
            <span className="history-what">
              {d.actor_name ?? ACTORS[d.actor]} moved them to <strong>{STAGE_LABELS[d.to]}</strong>
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

// ---- the BU head's decision ---------------------------------------------------------------------

interface Choice {
  to: CandidateStage;
  label: string;
  done: string;
  icon: ReactNode;
  variant: "primary" | "secondary" | "danger";
  prompt: string;
  reasonRequired: boolean;
}

const CHOICES: Choice[] = [
  { to: "shortlisted", label: "Shortlist", done: "Shortlisted", icon: <Star size={17} />, variant: "primary", prompt: "Anything HR should know? (optional)", reasonRequired: false },
  { to: "on_hold", label: "On hold", done: "Put on hold", icon: <PauseCircle size={17} />, variant: "secondary", prompt: "Why on hold, and until when? HR will read this.", reasonRequired: true },
  { to: "rejected", label: "Reject", done: "Rejected", icon: <XCircle size={17} />, variant: "danger", prompt: "Why reject? This is kept on their record.", reasonRequired: true },
];

function Decision({ r }: { r: Results }) {
  const toast = useToast();
  const goBack = useGoBack(`/candidates/${r.candidate.id}`);
  const [picked, setPicked] = useState<Choice | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choices = CHOICES.filter((c) => r.next.includes(c.to));
  if (!r.can_decide || choices.length === 0) return null;

  async function commit(choice: Choice) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/candidates/${r.candidate.id}/move`, { to: choice.to, reason: reason.trim() || undefined });
      toast(`${r.candidate.name}: ${choice.done}. Saved.`, choice.to === "rejected" ? "urgent" : choice.to === "on_hold" ? "warn" : "ok");
      setPicked(null);
      setReason("");
      goBack();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const reading = r.technical.report?.recommendation
    ? `MITRA interview report: ${TECHNICAL_SIGNAL[r.technical.report.recommendation].toLowerCase()}.`
    : r.hr_round
      ? `No MITRA interview report yet. The HR round recommended: ${HR_RECOMMENDATION[r.hr_round.recommendation].label.toLowerCase()}.`
      : "No MITRA interview report yet.";

  return (
    <section className="next-step decide-block" aria-labelledby="decide-title">
      <div className="next-copy">
        <h2 id="decide-title" className="next-title">
          Your decision
        </h2>
        <p className="next-text">
          <span className="next-who yours">Your move</span>
          MITRA's reading: {reading} MITRA recommends; the decision is yours.
        </p>
      </div>
      <div className="next-actions">
      {picked ? (
        <form
          className="reason"
          onSubmit={(e) => {
            e.preventDefault();
            commit(picked);
          }}
        >
          <label htmlFor="bu-reason">{picked.prompt}</label>
          <textarea id="bu-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
          <div className="actions">
            <Button type="submit" variant={picked.variant === "danger" ? "danger" : "primary"} icon={picked.icon} loading={busy} disabled={picked.reasonRequired && !reason.trim()}>
              {picked.label}
            </Button>
            <Button variant="ghost" icon={<Undo2 size={16} />} disabled={busy} onClick={() => setPicked(null)}>
              Back
            </Button>
          </div>
        </form>
      ) : (
        <div className="actions">
          {choices.map((c) => (
            <Button key={c.to} variant={c.variant} icon={c.icon} disabled={busy} onClick={() => setPicked(c)}>
              {c.label}
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
    </section>
  );
}

// ---- the page -----------------------------------------------------------------------------------

export function ResultsPage() {
  const { candidateId } = useParams();
  const [params, setParams] = useSearchParams();
  const round = params.get("round");
  const [r, setR] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setR(await api.get<Results>(`/candidates/${candidateId}/results${round ? `?interview=${encodeURIComponent(round)}` : ""}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [candidateId, round]);

  useEffect(() => {
    load();
  }, [load]);

  if (error && !r) {
    return (
      <div className="page">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!r) return <PageSkeleton />;

  return (
    <TooltipLayer>
      <div className="page page-wide results">
        <BackButton fallback={`/candidates/${r.candidate.id}`} label={r.candidate.name} />
        <header className="page-head">
          <div>
            <h1 className="page-title">{r.candidate.name}</h1>
            <p className="page-sub">
              Results for {r.job.title}
              {r.job.bu_name ? `, ${r.job.bu_name}` : ""}. Evidence first; the decision is a person's.
            </p>
          </div>
          <div className="results-head-actions">
            <span className={`mark mark-lg tone-${STAGE_TONES[r.candidate.stage]}`}>{STAGE_LABELS[r.candidate.stage]}</span>
            <ButtonLink size="sm" to={`/jobs/${r.job.id}/compare`} icon={<Columns3 size={15} />}>
              Compare candidates
            </ButtonLink>
          </div>
        </header>

        <Decision r={r} />
        <Glance r={r} />

        {r.technical.rounds.length > 1 && (
          <nav className="round-switch" aria-label="MITRA interview rounds">
            <span className="muted small">MITRA rounds:</span>
            {r.technical.rounds.map((x) => {
              const current = x.interview_id === r.technical.interview_id;
              return (
                <button
                  key={x.interview_id}
                  type="button"
                  className={current ? "round-chip on" : "round-chip"}
                  aria-current={current ? "page" : undefined}
                  onClick={() => setParams(x.number === r.technical.rounds.length ? {} : { round: x.interview_id })}
                >
                  Round {x.number}
                  {x.number === r.technical.rounds.length ? " (latest)" : ""}
                </button>
              );
            })}
          </nav>
        )}
        <TechnicalReport technical={r.technical} />

        <div className="split-2">
          <Points title="Strengths" caption="Rated 3 or 4, from the CV and the HR round." items={r.strengths} empty="Nothing rated 3 or above yet." />
          <Points title="Gaps to probe" caption="Rated low, or a weighty competency the CV says nothing about." items={r.gaps} empty="No low ratings, and the CV speaks to everything weighty." />
        </div>

        <MoreInsight id="results" hint="The CV against the job, the HR round, and the history">
          {r.rubric.length > 0 && (
            <ChartCard
              title="The CV against the job"
              caption="Each competency, heaviest first: its weight in the job, and what the CV shows on a 0–4 scale."
              table={<RubricTable rows={r.rubric} />}
            >
              <Rubric rows={r.rubric} />
            </ChartCard>
          )}
          {(r.jd_match?.summary || r.jd_match?.experience_note) && (
            <section className="card-viz">
              <h2 className="card-title">MITRA's summary of the CV</h2>
              {r.jd_match?.summary && <p className="side-text">{r.jd_match.summary}</p>}
              {r.jd_match?.experience_note && <p className="side-text muted">{r.jd_match.experience_note}</p>}
            </section>
          )}
          {r.hr_round && <HRRound hr={r.hr_round} candidateId={r.candidate.id} />}
          <History entries={r.decisions} />
        </MoreInsight>
      </div>
    </TooltipLayer>
  );
}
