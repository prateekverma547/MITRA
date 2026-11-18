// The place Track I's <InterviewReport> goes (docs/DEV_PLAN.md section 5).
// Until it lands this shows only what a real report for this candidate says,
// read-only, or says plainly that there is none. It never shows a fixture or
// sample report: a made-up report on a real person's page would be read as
// evidence about them.

import { FileText } from "lucide-react";
import type { Results } from "./types";
import { TECHNICAL_SIGNAL } from "./types";

export function TechnicalReportSlot({ technical }: { technical: Results["technical"] }) {
  const report = technical.report;
  return (
    <section className="card-viz report-slot" aria-labelledby="technical-report">
      <header className="card-viz-head">
        <div>
          <h2 id="technical-report" className="card-title">
            Technical round report
          </h2>
          <p className="card-caption">MITRA's technical interview: scores with quotes, the transcript, and the integrity section.</p>
        </div>
      </header>
      {report ? (
        <div className="report-headline">
          {report.recommendation && <p className="glance-value">{TECHNICAL_SIGNAL[report.recommendation]}</p>}
          {report.summary && <p className="fit-summary">{report.summary}</p>}
          {report.scores.length > 0 && (
            <table className="report-scores">
              <thead>
                <tr>
                  <th>Competency</th>
                  <th className="num">Score (0–5)</th>
                </tr>
              </thead>
              <tbody>
                {report.scores.map((s) => (
                  <tr key={s.name}>
                    <td>{s.name}</td>
                    <td className="num">{s.insufficient_signal || s.score === null ? "Not enough signal" : s.score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {report.coverage_gaps.length > 0 && <p className="muted small">Not covered well enough to judge: {report.coverage_gaps.join(", ")}.</p>}
          <p className="muted small">The full report, with every quote and its moment in the recording, opens here once the interview track's report view is connected.</p>
        </div>
      ) : (
        <div className="report-empty">
          <FileText size={22} aria-hidden="true" />
          <p>MITRA's technical report appears here once the interview track is connected. Nothing is shown in its place.</p>
        </div>
      )}
    </section>
  );
}
