// The pieces of the candidate page. Track H.
//
// The page answers three questions in order: what happens now, what are the
// facts at a glance, and what is the evidence. Figures are small and quiet;
// titles say what each block is for.

import { ChevronDown, FileText } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { STAGE_LABELS, type CandidateDetail, type CandidateStage, type DecisionEntry, type FitAnalysis, type HRQuestion, type Role } from "../../contracts";
import { Pips } from "../../shell/ui/Pips";
import type { Results, StrengthOrGap } from "../results/types";

const RATING_WORDS = ["Clearly lacking", "Adjacent only", "Claimed, no specifics", "Specific work", "Specific, owned, measured"];

// ---- what happens now -------------------------------------------------------------------------

const NEXT: Partial<Record<CandidateStage, { who: Role | null; text: string }>> = {
  new: { who: "hr", text: "Check the CV MITRA read, then send it to screening." },
  screened: { who: "hr", text: "Decide after screening: begin the HR round, hold, or reject." },
  ready_for_hr: { who: "hr", text: "Start the HR round room and invite the candidate, or skip the HR round." },
  hr_booked: { who: "hr", text: "Join the HR round room. It is recorded, and MITRA evaluates it when you end it." },
  hr_done: { who: "hr", text: "Read MITRA's evaluation of the HR round and decide on the MITRA interview round." },
  ready_for_technical: { who: "hr", text: "Book MITRA's proctored interview once its plan is ready." },
  technical_booked: { who: null, text: "MITRA runs the proctored MITRA interview. Follow it in the live room." },
  technical_done: { who: "hr", text: "Read MITRA's report, then send it to the BU head." },
  with_bu_head: { who: "bu_head", text: "The BU head makes the final decision." },
  on_hold: { who: "bu_head", text: "On hold. The BU head shortlists or rejects." },
  shortlisted: { who: null, text: "Shortlisted. The journey in MITRA is complete." },
  rejected: { who: null, text: "Rejected. The reason is in the history." },
  parked: { who: "hr", text: "Not now. HR can bring them back to the HR round, or reject." },
  withdrew: { who: null, text: "The candidate withdrew." },
};

const WHO = { hr: "HR", bu_head: "the BU head", org_admin: "an admin" } as const;

export function NextStepText({ stage, role }: { stage: CandidateStage; role: Role }) {
  const next = NEXT[stage];
  if (!next) return null;
  const yours = next.who === role;
  return (
    <p className="next-text">
      {next.who && <span className={yours ? "next-who yours" : "next-who"}>{yours ? "Your move" : `Waiting on ${WHO[next.who]}`}</span>}
      {next.text}
    </p>
  );
}

// ---- at a glance -------------------------------------------------------------------------------

const HR_WORDS = { proceed_to_technical: "Proceed to MITRA interview", reject: "Reject", discuss: "Discuss" } as const;
const SIGNAL_WORDS = {
  strong_evidence_for: "Strong evidence for",
  some_evidence_for: "Some evidence for",
  mixed: "Mixed",
  limited_evidence: "Limited evidence",
  insufficient_signal: "Not enough to judge",
} as const;

const HR_STAGES: CandidateStage[] = ["ready_for_hr", "hr_booked"];
const TECH_STAGES: CandidateStage[] = ["ready_for_technical", "technical_booked"];

function Tile({ title, value, note, tone }: { title: string; value: ReactNode; note?: string; tone?: "good" | "attention" | "closed" | "muted" }) {
  return (
    <div className={`glance-tile${tone ? ` g-${tone}` : ""}`}>
      <span className="glance-title">{title}</span>
      <span className="glance-value">{value}</span>
      {note && <span className="glance-note">{note}</span>}
    </div>
  );
}

export function Glance({ c, results, history }: { c: CandidateDetail; results: Results | null; history: DecisionEntry[] }) {
  const fit = c.fit!;
  const enough = fit.assessed_weight_percent >= c.min_assessed_percent;
  const passes = enough && fit.score_percent >= fit.threshold_percent;
  const hr = results?.hr_round;
  const tech = results?.technical.report;
  const hrHeld = history.some((d) => d.to === "hr_done");
  const hrSkipped = results?.hr_skipped ?? null;
  return (
    <section className="glance" aria-label="At a glance">
      <Tile
        title="CV fit to the job"
        value={
          <>
            {Math.round(fit.score_percent)}%
            <span className="glance-meter" aria-hidden="true">
              <span style={{ width: `${Math.min(100, fit.score_percent)}%` }} className={passes ? "on" : "off"} />
              <i style={{ left: `${fit.threshold_percent}%` }} />
            </span>
          </>
        }
        note={!enough ? "Too little evidence to judge" : passes ? `At or above the ${fit.threshold_percent}% cut-off` : `Below the ${fit.threshold_percent}% cut-off`}
        tone={!enough ? "attention" : passes ? "good" : "closed"}
      />
      <Tile
        title="Evidence in the CV"
        value={`${Math.round(fit.assessed_weight_percent)}%`}
        note={`of what the job asks for. ${enough ? "Enough" : `MITRA needs ${c.min_assessed_percent}%`} to judge.`}
        tone={enough ? undefined : "attention"}
      />
      <Tile
        title="HR round"
        value={hr ? HR_WORDS[hr.recommendation] : hrSkipped ? "Skipped" : hrHeld ? "Held" : HR_STAGES.includes(c.stage) ? "Not held yet" : "Not reached"}
        note={hr ? "MITRA's recommendation" : hrSkipped ? (hrSkipped.reason ?? `Skipped by ${hrSkipped.by ?? "HR"}`) : hrHeld ? "No evaluation recorded" : HR_STAGES.includes(c.stage) ? STAGE_LABELS[c.stage] : "Comes after screening"}
        tone={hr ? (hr.recommendation === "proceed_to_technical" ? "good" : hr.recommendation === "reject" ? "closed" : "attention") : "muted"}
      />
      <Tile
        title="MITRA interview round"
        value={tech?.recommendation ? SIGNAL_WORDS[tech.recommendation] : TECH_STAGES.includes(c.stage) ? STAGE_LABELS[c.stage] : "No report yet"}
        note={tech ? "MITRA's report" : "Written when the interview ends"}
        tone={tech ? undefined : "muted"}
      />
    </section>
  );
}

// ---- the evidence -----------------------------------------------------------------------------

function Point({ p }: { p: StrengthOrGap }) {
  return (
    <li className="point">
      <span className="point-head">
        <span className="point-name">{p.name}</span>
        <span className="point-src">{p.source === "cv" ? "CV" : "HR round"}</span>
      </span>
      <Pips rating={p.rating} words={p.source === "cv" ? RATING_WORDS : undefined} none={p.kind === "no_evidence" ? "Nothing in the CV" : "Nothing to go on"} />
      {p.why && <span className="point-why">{p.why}</span>}
    </li>
  );
}

/**
 * Before and during the HR round, the questions lead the page: they are what
 * HR follows in the round. Gaps to probe move to the side beside them.
 */
export function HRQuestionsLead({ questions }: { questions: HRQuestion[] }) {
  if (questions.length === 0) return null;
  return (
    <section className="hrq-lead" aria-labelledby="hrq-title">
      <header>
        <h2 id="hrq-title">Questions for the HR round</h2>
        <p>Written from this CV for this person. You follow them in the round, one at a time, and tick each as you ask it.</p>
      </header>
      <ol>
        {questions.map((q, i) => (
          <li key={q.id}>
            <span className="hrq-num">{i + 1}</span>
            <span>
              <span className="hrq-text">{q.text}</span>
              {q.why && <span className="hrq-why">{q.why}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function CandidateEvidence({
  fit,
  results,
  questionsFirst = [],
}: {
  fit: FitAnalysis;
  results: Results | null;
  /** Shown at the top, highlighted, while the HR round is ahead. */
  questionsFirst?: HRQuestion[];
}) {
  const [open, setOpen] = useState<string | null>(null);
  const total = fit.ratings.reduce((s, r) => s + r.weight, 0) || 1;
  const rows = [...fit.ratings].sort((a, b) => b.weight - a.weight);
  const lead = questionsFirst.length > 0;
  return (
    <div className="cand-main">
      {lead && <HRQuestionsLead questions={questionsFirst} />}
      {results && (results.strengths.length > 0 || (!lead && results.gaps.length > 0)) && (
        <div className={lead ? "sg one" : "sg"}>
          <section className="card-viz">
            <h2 className="card-title">Strengths</h2>
            <p className="card-caption">Rated 3 or 4 on something the job asks for.</p>
            {results.strengths.length ? <ul className="points">{results.strengths.map((p) => <Point key={p.source + p.name} p={p} />)}</ul> : <p className="empty-note">No clear strength yet.</p>}
          </section>
          {!lead && <GapsCard results={results} />}
        </div>
      )}

      <section className="card-viz">
        <header className="card-viz-head">
          <div>
            <h2 className="card-title">The CV against the job</h2>
            <p className="card-caption">Heaviest first. Select a row for MITRA's reasoning and the lines it quoted.</p>
          </div>
        </header>
        <table className="cv-table">
          <thead>
            <tr>
              <th>What the job asks for</th>
              <th className="num">Weight</th>
              <th>The CV shows</th>
              <th aria-label="Details" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isOpen = open === r.competency_id;
              return (
                <Fragment key={r.competency_id}>
                  <tr className={isOpen ? "cv-row open" : "cv-row"} onClick={() => setOpen(isOpen ? null : r.competency_id)}>
                    <td className="cv-name">{r.competency_name}</td>
                    <td className="num">{Math.round((r.weight / total) * 100)}%</td>
                    <td>
                      <Pips rating={r.rating} words={RATING_WORDS} none="No evidence in the CV" />
                    </td>
                    <td className="cv-toggle">
                      <button type="button" aria-expanded={isOpen} aria-label={`${isOpen ? "Hide" : "Show"} the evidence for ${r.competency_name}`} onClick={(e) => { e.stopPropagation(); setOpen(isOpen ? null : r.competency_id); }}>
                        <ChevronDown size={16} />
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="cv-detail">
                      <td colSpan={4}>
                        {r.reasoning && <p className="cv-why">{r.reasoning}</p>}
                        {r.evidence.length > 0 ? (
                          r.evidence.map((q, i) => (
                            <blockquote key={i} className="cv-quote">
                              {q}
                            </blockquote>
                          ))
                        ) : (
                          <p className="muted small">No line in the CV speaks to this.</p>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {fit.identity_fields_removed.length > 0 && (
          <p className="muted small">Removed before screening, so the scorer never saw them: {fit.identity_fields_removed.join(", ")}.</p>
        )}
      </section>
    </div>
  );
}

export function GapsCard({ results }: { results: Results }) {
  return (
    <section className="card-viz gaps-card">
      <h2 className="card-title">Gaps to probe</h2>
      <p className="card-caption">Rated low, or something weighty the CV says nothing about.</p>
      {results.gaps.length ? <ul className="points">{results.gaps.map((p) => <Point key={p.source + p.name} p={p} />)}</ul> : <p className="empty-note">No clear gap.</p>}
    </section>
  );
}

// ---- the side column ----------------------------------------------------------------------------

export function Sidebar({
  fit,
  questions,
  history,
  gaps,
}: {
  fit: FitAnalysis;
  /** Empty while the questions lead the main column. */
  questions: HRQuestion[];
  history: ReactNode;
  /** Gaps to probe, beside the questions while the HR round is ahead. */
  gaps?: Results | null;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? questions : questions.slice(0, 5);
  return (
    <aside className="cand-side">
      {gaps && <GapsCard results={gaps} />}
      {(fit.summary || fit.experience_note) && (
        <section className="card-viz">
          <h2 className="card-title">
            <FileText size={16} aria-hidden="true" /> MITRA's summary of the CV
          </h2>
          {fit.summary && <p className="side-text">{fit.summary}</p>}
          {fit.experience_note && <p className="side-text muted">{fit.experience_note}</p>}
        </section>
      )}
      {questions.length > 0 && (
        <section className="card-viz">
          <h2 className="card-title">Questions for the HR round</h2>
          <p className="card-caption">Written from this CV, each with the reason for asking.</p>
          <ol className="side-questions">
            {shown.map((q) => (
              <li key={q.id}>
                <span className="sq-text">{q.text}</span>
                <span className="sq-why">{q.why}</span>
              </li>
            ))}
          </ol>
          {questions.length > 5 && (
            <button type="button" className="btn btn-ghost btn-sm show-all" onClick={() => setAll((v) => !v)}>
              {all ? "Show fewer" : `Show all ${questions.length}`}
            </button>
          )}
        </section>
      )}
      {history}
    </aside>
  );
}

const ACTORS = { system: "MITRA", hr: "HR", bu_head: "BU head" };

export function History({ entries }: { entries: DecisionEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <section className="card-viz" aria-labelledby="history">
      <h2 id="history" className="card-title">
        How they got here
      </h2>
      <ol className="side-history">
        {[...entries].reverse().map((d, i) => (
          <li key={i}>
            <span className="sh-what">
              {ACTORS[d.actor]}: <strong>{STAGE_LABELS[d.to]}</strong>
            </span>
            {d.at && <span className="sh-when">{new Date(d.at).toLocaleDateString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>}
            {d.reason && <span className="sh-reason">{d.reason}</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}
