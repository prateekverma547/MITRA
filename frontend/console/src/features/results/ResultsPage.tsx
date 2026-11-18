// The BU head's results page. Everything here is evidence Track H already
// holds (the CV against the job, the HR round), plus a bounded slot for
// Track I's technical report. The BU head decides at the bottom; HR and admins
// read the same page without the decision.

import { Columns3, PauseCircle, ScrollText, Star, Undo2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS, STAGE_TONES, type CandidateStage } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { Pips } from "../../shell/ui/Pips";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { useToast } from "../../shell/ui/Toast";
import { ChartCard } from "../../shell/viz/ChartCard";
import { TooltipLayer } from "../../shell/viz/Tooltip";
import { ScoreMeter } from "../screening/ScoreMeter";
import { Rubric, RubricTable } from "./Rubric";
import { TechnicalReportSlot } from "./TechnicalReportSlot";
import { HR_RECOMMENDATION, TECHNICAL_SIGNAL, type Results, type StrengthOrGap } from "./types";
import "./results.css";

const SOURCE = { cv: "CV", hr_round: "HR round" };
const ACTORS = { system: "MITRA", hr: "HR", bu_head: "BU head" };

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
}

// ---- at a glance --------------------------------------------------------------------------------

function Glance({ r }: { r: Results }) {
  const hr = r.hr_round ? HR_RECOMMENDATION[r.hr_round.recommendation] : null;
  const tech = r.technical.report?.recommendation;
  return (
    <div className="glance">
      <section className="glance-item">
        <h2 className="glance-label">Against the job description</h2>
        {r.jd_match ? (
          <>
            <ScoreMeter score={r.jd_match.score_percent} threshold={r.jd_match.threshold_percent} enoughEvidence={r.jd_match.enough_evidence} />
            <p className="glance-note">
              Judged on {r.jd_match.assessed_weight_percent}% of the job's weight. The cut-off is {r.jd_match.threshold_percent}%.
            </p>
          </>
        ) : (
          <p className="glance-empty">Not screened</p>
        )}
      </section>
      <section className="glance-item">
        <h2 className="glance-label">HR round</h2>
        {hr ? <p className={`glance-value tone-${hr.tone}`}>{hr.label}</p> : <p className="glance-empty">No evaluation recorded</p>}
        {r.hr_round?.attribution_confidence === "uncertain" && <p className="glance-note">Voices were hard to tell apart; check the transcript.</p>}
      </section>
      <section className="glance-item">
        <h2 className="glance-label">Technical round</h2>
        {tech ? <p className="glance-value">{TECHNICAL_SIGNAL[tech]}</p> : <p className="glance-empty">No report yet</p>}
      </section>
    </div>
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
          <h3 className="brief-sub">Raised for the technical round</h3>
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

function Decision({ r, onDone }: { r: Results; onDone: () => void }) {
  const toast = useToast();
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
      toast(`${r.candidate.name}: ${choice.done}`);
      setPicked(null);
      setReason("");
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const reading = r.technical.report?.recommendation
    ? `Technical report: ${TECHNICAL_SIGNAL[r.technical.report.recommendation].toLowerCase()}.`
    : r.hr_round
      ? `No technical report yet. The HR round recommended: ${HR_RECOMMENDATION[r.hr_round.recommendation].label.toLowerCase()}.`
      : "No technical report yet.";

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
          <span className="decision-q">{r.candidate.stage === "on_hold" ? "On hold. Your decision" : "Your decision"}</span>
          {choices.map((c) => (
            <Button key={c.to} variant={c.variant} icon={c.icon} disabled={busy} onClick={() => setPicked(c)}>
              {c.label}
            </Button>
          ))}
        </div>
      )}
      <p className="decision-reading">MITRA's reading: {reading} MITRA recommends; the decision is yours.</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

// ---- the page -----------------------------------------------------------------------------------

export function ResultsPage() {
  const { candidateId } = useParams();
  const [r, setR] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setR(await api.get<Results>(`/candidates/${candidateId}/results`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [candidateId]);

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

        {r.sent_to_bu_head && (
          <div className="sent-note">
            <p>
              Sent to the BU head by {r.sent_to_bu_head.by ?? "HR"}
              {r.sent_to_bu_head.at ? ` on ${when(r.sent_to_bu_head.at)}` : ""}.
            </p>
            {r.sent_to_bu_head.note && <blockquote>{r.sent_to_bu_head.note}</blockquote>}
          </div>
        )}

        <Glance r={r} />

        {r.rubric.length > 0 && (
          <ChartCard
            title="Against what the job asks for"
            caption="Each competency, heaviest first: its weight in the job, and what the CV shows on a 0–4 scale."
            table={<RubricTable rows={r.rubric} />}
          >
            <Rubric rows={r.rubric} />
          </ChartCard>
        )}

        {(r.jd_match?.summary || r.jd_match?.experience_note) && (
          <section className="results-summary">
            <h2 className="section-title">Summary of the CV</h2>
            {r.jd_match?.summary && <p className="fit-summary">{r.jd_match.summary}</p>}
            {r.jd_match?.experience_note && <p className="fit-experience">{r.jd_match.experience_note}</p>}
          </section>
        )}

        <div className="split-2">
          <Points title="Strengths" caption="Rated 3 or 4, from the CV and the HR round." items={r.strengths} empty="Nothing rated 3 or above yet." />
          <Points
            title="Gaps"
            caption="Rated 0 or 1, or a weighty competency the CV says nothing about."
            items={r.gaps}
            empty="No low ratings, and the CV speaks to everything weighty."
          />
        </div>

        {r.hr_round ? (
          <HRRound hr={r.hr_round} candidateId={r.candidate.id} />
        ) : (
          <section className="card-viz">
            <h2 className="card-title">The HR round</h2>
            <p className="empty-note">No evaluation of the HR round is recorded for this candidate.</p>
          </section>
        )}

        <TechnicalReportSlot technical={r.technical} />

        <History entries={r.decisions} />
        <Decision r={r} onDone={load} />
      </div>
    </TooltipLayer>
  );
}
