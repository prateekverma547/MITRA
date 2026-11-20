// The live room for the MITRA interview round, for HR and the job's BU head.
//
// Nobody joins the call from here: the candidate is interviewed by MITRA
// alone, and a watcher in the room would be a second person in it. What shows
// is what is known without disturbing them: where the round stands, MITRA's
// plan by the clock, and the integrity facts the candidate's browser reports as
// they happen. backend/hiring/technical.py technical_live.

import { ClipboardList, Eye, ListChecks, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import { BackButton } from "../../shell/ui/BackButton";
import { ButtonLink } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { ROUND_STATE, RoundSteps } from "./RoundStatus";
import type { LiveState, TechnicalLive } from "./types";
import "./tech-room.css";

const POLL_MS = 4000;
const MOVING: LiveState[] = ["waiting", "consented", "starting", "live", "candidate_left", "closing", "scoring"];
const IN_ROOM: LiveState[] = ["starting", "live", "candidate_left", "closing"];

function clock(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return "0:00";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}

/** Elapsed time, ticking in the browser between polls while the round is live. */
function useElapsed(live: TechnicalLive | null) {
  const [now, setNow] = useState(() => Date.now());
  const running = live && IN_ROOM.includes(live.state) && live.started_at;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [running]);
  if (!live) return 0;
  if (running) return (now - new Date(live.started_at!).getTime()) / 1000;
  return live.elapsed_seconds;
}

export function TechnicalRoom() {
  const { candidateId } = useParams();
  const [live, setLive] = useState<TechnicalLive | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLive(await api.get<TechnicalLive>(`/candidates/${candidateId}/technical/live`));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [candidateId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!live || !MOVING.includes(live.state)) return;
    const t = window.setTimeout(load, POLL_MS);
    return () => window.clearTimeout(t);
  }, [live, load]);

  const elapsed = useElapsed(live);

  if (error && !live) {
    return (
      <div className="page">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!live) return <PageSkeleton />;

  const state = ROUND_STATE[live.state];
  const sections = live.plan.sections;
  const currentIndex = sections.findIndex((s) => s.key === live.plan.current);
  const integrity = live.integrity;

  return (
    <div className="page page-wide troom">
      <BackButton fallback={`/candidates/${live.candidate.id}`} label={live.candidate.name} />
      <header className="page-head troom-head">
        <div>
          <h1 className="page-title">MITRA interview round</h1>
          <p className="page-sub">
            {live.candidate.name}, {live.job.title}
            {live.job.bu_name ? `, ${live.job.bu_name}` : ""}
          </p>
        </div>
        {live.state === "done" && (
          <ButtonLink variant="primary" to={`/candidates/${live.candidate.id}/results`} icon={<ClipboardList size={17} />}>
            Open the report
          </ButtonLink>
        )}
      </header>

      <div className="troom-grid">
        <section className="troom-stage" aria-live="polite">
          <div className="troom-stage-top">
            <span className={`troom-pill troom-${live.state}`}>
              <span className="troom-dot" aria-hidden="true" />
              {state.label}
            </span>
            <span className="troom-clock" aria-label="Elapsed time">
              {clock(elapsed)}
              <span className="troom-of"> of about {live.planned_minutes} min</span>
            </span>
          </div>

          <div className={live.state === "live" || live.state === "starting" ? "troom-mitra speaking" : "troom-mitra"}>
            <span className="troom-orb" aria-hidden="true" />
            <div>
              <p className="troom-name">MITRA</p>
              <p className="troom-line">{state.line}</p>
              {live.failure_reason && <p className="troom-line troom-muted">Reason recorded: {live.failure_reason}.</p>}
              <RoundSteps state={live.state} scoringStep={live.scoring_step} />
            </div>
          </div>

          <dl className="troom-facts">
            <div>
              <dt>Planned length</dt>
              <dd>{live.planned_minutes} minutes</dd>
            </div>
            <div>
              <dt>Recording</dt>
              <dd>{live.recording_status && live.recording_status !== "not_recorded" ? "On" : "Off"}</dd>
            </div>
            <div>
              <dt>Candidate's time zone</dt>
              <dd>{live.candidate_timezone ?? "Known when they join"}</dd>
            </div>
          </dl>

          <p className="troom-see">
            <Eye size={14} aria-hidden="true" /> The candidate is interviewed by MITRA alone, so watching from here never puts a second person in their
            room. Live video for HR and the BU head joins this page in a coming release.
          </p>
        </section>

        <section className="card-viz troom-plan" aria-labelledby="troom-plan-title">
          <h2 id="troom-plan-title" className="card-title">
            <ListChecks size={16} aria-hidden="true" /> MITRA's plan
          </h2>
          <p className="card-caption">{IN_ROOM.includes(live.state) ? "Where the plan expects to be, by the clock." : "The sections MITRA works through, in order."}</p>
          {sections.length ? (
            <ol className="troom-sections">
              {sections.map((s, i) => {
                const cls = IN_ROOM.includes(live.state) ? (i < currentIndex ? "done" : i === currentIndex ? "now" : "") : live.state === "done" || live.state === "scoring" ? "done" : "";
                return (
                  <li key={s.key} className={cls}>
                    <span className="troom-step" aria-hidden="true" />
                    <span className="troom-section-name">{s.name}</span>
                    <span className="troom-section-min">{Math.round(s.minutes)} min</span>
                    {cls === "now" && <span className="troom-now">by the clock</span>}
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="empty-note">MITRA's plan appears once it is prepared.</p>
          )}
        </section>

        <section className="card-viz troom-integrity" aria-labelledby="troom-int-title">
          <h2 id="troom-int-title" className="card-title">
            <ShieldCheck size={16} aria-hidden="true" /> Integrity, live
          </h2>
          <p className="card-caption">Facts for a person to review. Never part of a score.</p>
          {integrity.counts.length > 0 && (
            <ul className="troom-counts">
              {integrity.counts.map((c) => (
                <li key={c.kind}>
                  <span className="troom-count">{c.count}</span>
                  <span>{c.label}</span>
                </li>
              ))}
            </ul>
          )}
          {integrity.events.length ? (
            <ol className="troom-events">
              {integrity.events.map((e, i) => (
                <li key={i}>
                  <span className="troom-at">{clock(e.at_seconds)}</span>
                  <span>{e.label}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="empty-note">
              {live.state === "live" ? "Nothing so far: the interview has stayed in front of the candidate." : "Nothing reported."}
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
