// the MITRA interview round report, in the console's own design. The data is
// Track I's FeedbackReport contract and the interview's transcript, shown as
// stored: every score carries its quotes, every quote opens its line in the
// transcript. With no report, it says so; it never shows sample data on a real
// person's page.

import { ChevronDown, FileText, MessageSquareQuote, ScrollText, ShieldCheck } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import type { ReportQuote, Results } from "./types";
import { TECHNICAL_SIGNAL } from "./types";

function clock(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const SIGNAL_TONE: Record<string, string> = {
  strong_evidence_for: "good",
  some_evidence_for: "good",
  mixed: "attention",
  limited_evidence: "attention",
  insufficient_signal: "closed",
};

function Quote({ q, onJump }: { q: ReportQuote; onJump: (turn: number) => void }) {
  return (
    <button type="button" className="quote-jump" onClick={() => onJump(q.turn_index)}>
      <blockquote className="cv-quote">{q.text}</blockquote>
      <span className="quote-at">
        <MessageSquareQuote size={13} aria-hidden="true" /> at {clock(q.audio_start_seconds ?? q.at_seconds)}, show in transcript
      </span>
    </button>
  );
}

export function TechnicalReport({ technical }: { technical: Results["technical"] }) {
  const report = technical.full;
  const [open, setOpen] = useState<string | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [lit, setLit] = useState<{ turn: number; at: number } | null>(null);

  useEffect(() => {
    if (lit) document.getElementById(`tturn-${lit.turn}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [lit]);

  function jump(turn: number) {
    setTranscriptOpen(true);
    setLit({ turn, at: Date.now() });
  }

  if (!report) {
    return (
      <section className="card-viz" aria-labelledby="technical-report">
        <h2 id="technical-report" className="card-title">
          MITRA interview round report
        </h2>
        <div className="report-empty">
          <FileText size={22} aria-hidden="true" />
          <p>No MITRA interview round report for this candidate yet. It appears here when MITRA's interview ends and is scored.</p>
        </div>
      </section>
    );
  }

  const scores = report.competency_scores ?? [];
  const strengths = report.strengths_observed ?? [];
  const flags = report.red_flags_observed ?? [];
  const gaps = report.coverage_gaps ?? [];
  const minutes = report.interview_duration_seconds ? Math.round(report.interview_duration_seconds / 60) : null;
  const health = report.conversation_health;

  return (
    <section className="card-viz tech-report" aria-labelledby="technical-report">
      <header className="card-viz-head">
        <div>
          <h2 id="technical-report" className="card-title">
            MITRA interview round report
          </h2>
          <p className="card-caption">
            MITRA's interview
            {minutes ? `, ${minutes} minutes` : ""}
            {technical.ended_at ? `, ${new Date(technical.ended_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : ""}. Every score
            carries the candidate's own words.
          </p>
        </div>
        <span className={`mark mark-lg tone-${SIGNAL_TONE[report.recommendation] ?? "attention"}`}>{TECHNICAL_SIGNAL[report.recommendation] ?? report.recommendation}</span>
      </header>

      {health?.degraded && (
        <p className="notice notice-attention">
          The connection was poor in this interview{health.summary ? `: ${health.summary}` : "."} Read low scores with that in mind; a bad line is not
          evidence about the candidate.
        </p>
      )}
      {report.summary && <p className="tr-summary">{report.summary}</p>}

      <table className="cv-table">
        <thead>
          <tr>
            <th>Competency</th>
            <th>Score</th>
            <th>Coverage</th>
            <th aria-label="Details" />
          </tr>
        </thead>
        <tbody>
          {scores.map((s) => {
            const isOpen = open === s.competency_id;
            const none = s.insufficient_signal || s.score === null;
            return (
              <Fragment key={s.competency_id}>
                <tr className={isOpen ? "cv-row open" : "cv-row"} onClick={() => setOpen(isOpen ? null : s.competency_id)}>
                  <td className="cv-name">{s.name}</td>
                  <td>
                    {none ? (
                      <span className="pips-none">Not enough signal</span>
                    ) : (
                      <span className="tr-score">
                        <span className="tr-bar" aria-hidden="true">
                          <span style={{ width: `${(s.score! / 5) * 100}%` }} />
                        </span>
                        {s.score!.toFixed(1)} of 5
                      </span>
                    )}
                  </td>
                  <td className="muted small">{s.coverage}</td>
                  <td className="cv-toggle">
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? "Hide" : "Show"} the evidence for ${s.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpen(isOpen ? null : s.competency_id);
                      }}
                    >
                      <ChevronDown size={16} />
                    </button>
                  </td>
                </tr>
                {isOpen && (
                  <tr className="cv-detail">
                    <td colSpan={4}>
                      {s.rationale && <p className="cv-why">{s.rationale}</p>}
                      {s.evidence.length ? s.evidence.map((q, i) => <Quote key={i} q={q} onJump={jump} />) : <p className="muted small">No quote supports a score here.</p>}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      {(strengths.length > 0 || flags.length > 0) && (
        <div className="sg">
          <div>
            <h3 className="brief-sub">Strengths observed</h3>
            {strengths.length ? (
              <ul className="points">
                {strengths.map((s, i) => (
                  <li key={i} className="point">
                    <span className="point-name">{s.description}</span>
                    {s.evidence.slice(0, 1).map((q, j) => (
                      <Quote key={j} q={q} onJump={jump} />
                    ))}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-note">None recorded.</p>
            )}
          </div>
          <div>
            <h3 className="brief-sub">Red flags observed</h3>
            {flags.length ? (
              <ul className="points">
                {flags.map((f, i) => (
                  <li key={i} className="point">
                    <span className="point-name">{f.description}</span>
                    {f.evidence.slice(0, 1).map((q, j) => (
                      <Quote key={j} q={q} onJump={jump} />
                    ))}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-note">None recorded.</p>
            )}
          </div>
        </div>
      )}

      {gaps.length > 0 && <p className="muted small">Not covered well enough to judge: {gaps.join(", ")}.</p>}

      <section className="tr-integrity" aria-labelledby="tr-integrity-title">
        <h3 id="tr-integrity-title" className="tr-integrity-title">
          <ShieldCheck size={16} aria-hidden="true" /> Integrity
        </h3>
        {technical.integrity && technical.integrity.total > 0 ? (
          <>
            <p className="tr-integrity-counts">
              {technical.integrity.counts.map((c) => `${c.label}: ${c.count} ${c.count === 1 ? "time" : "times"}`).join(". ")}.
            </p>
            <ol className="tr-integrity-list">
              {[...technical.integrity.events].reverse().map((e, i) => (
                <li key={i}>
                  <span className="tr-integrity-at">{clock(e.at_seconds)}</span> {e.label}
                </li>
              ))}
            </ol>
          </>
        ) : (
          <p className="tr-integrity-counts">The candidate's browser reported nothing: the interview stayed in front of them throughout.</p>
        )}
        <p className="tr-integrity-note">
          Facts for a person to review, never part of a score. The full proctoring record (camera, screen and voice checks) is added as it becomes
          available.
        </p>
      </section>

      {technical.transcript.length > 0 && (
        <details className="transcript-panel" open={transcriptOpen} onToggle={(e) => setTranscriptOpen(e.currentTarget.open)}>
          <summary>
            <ScrollText size={16} aria-hidden="true" /> Transcript ({technical.transcript.length} turns)
          </summary>
          <ol className="transcript transcript-hr">
            {technical.transcript.map((t) => (
              <li
                key={t.index}
                id={`tturn-${t.index}`}
                className={`line line-${t.speaker === "candidate" ? "you" : "mitra"}${lit?.turn === t.index ? " lit" : ""}`}
              >
                <span className="line-who">
                  {t.speaker === "candidate" ? "Candidate" : "Mitra"}
                  <span className="line-at">{clock(t.at_seconds)}</span>
                </span>
                <p>{t.text}</p>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
