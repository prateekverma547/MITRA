// The CV against the job: one row per competency, heaviest first. Two things
// per row and they never share an encoding: how much the job cares (a thin
// neutral weight bar and its number) and what the CV shows (a 0–4 bar in the
// signal blue). "No evidence" is a hatched, labelled track, never a short bar,
// because saying nothing is not the same as a low score.

import { RATING_WORDS } from "../../shell/ui/Pips";
import { tipProps, useTip } from "../../shell/viz/Tooltip";
import type { Results } from "./types";

export function Rubric({ rows }: { rows: Results["rubric"] }) {
  const tip = useTip();
  const heaviest = Math.max(...rows.map((r) => r.weight_percent), 1);
  return (
    <ol className="rubric" aria-label="Each competency the job asks for, with its weight and the CV's rating">
      <li className="rubric-scale" aria-hidden="true">
        <span />
        <span />
        <span className="rubric-ticks">
          {[0, 1, 2, 3, 4].map((n) => (
            <span key={n} style={{ left: `${(n / 4) * 100}%` }}>
              {n}
            </span>
          ))}
        </span>
      </li>
      {rows.map((r) => (
        <li key={r.competency_id} className="rubric-row">
          <span className="rubric-name">{r.name}</span>
          <span className="rubric-weight" title={`${r.weight_percent}% of the job`}>
            <span className="rubric-weight-bar" style={{ width: `${(r.weight_percent / heaviest) * 100}%` }} />
            <span className="rubric-weight-num">{Math.round(r.weight_percent)}%</span>
          </span>
          {r.rating === null ? (
            <span className="rubric-track rubric-none" {...tipProps(tip, "No evidence", r.name)}>
              <span>No evidence in the CV</span>
            </span>
          ) : (
            <span className="rubric-track" {...tipProps(tip, `${r.rating} of 4, ${RATING_WORDS[r.rating].toLowerCase()}`, r.name)}>
              {[1, 2, 3].map((n) => (
                <span key={n} className="rubric-grid" style={{ left: `${(n / 4) * 100}%` }} />
              ))}
              <span className="rubric-bar" style={{ width: `max(6px, ${(r.rating / 4) * 100}%)` }} />
              <span className={r.rating >= 3.5 ? "rubric-value inside" : "rubric-value"} style={{ left: `max(6px, ${(r.rating / 4) * 100}%)` }}>
                {r.rating}
              </span>
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

export function RubricTable({ rows }: { rows: Results["rubric"] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Competency</th>
          <th className="num">Weight</th>
          <th className="num">CV rating (0–4)</th>
          <th className="num">Quotes</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.competency_id}>
            <td>{r.name}</td>
            <td className="num">{r.weight_percent}%</td>
            <td className="num">{r.rating === null ? "No evidence" : r.rating}</td>
            <td className="num">{r.evidence.length}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
