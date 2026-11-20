// The MITRA round's integrity events, from the console room to Track I's intake.
//
// Option A (2026-11-19): our own face check (`proctor.ts`) and the browser's own
// events feed Track I's engine (`engine.ts`), which decides each event and each
// reminder (at most 3 per kind, 2 minutes apart), and Track I's sender
// (`httpSink.ts`) posts them with the proctoring token from the join. The same
// sender posts a status reading once a second, which is how the server notices a
// dropped call in seconds rather than half a minute. Times are on the server's
// interview clock (`serverClock.ts`), so events line up with the transcript.
//
// Nothing but events and readings leaves the page: no image, no video, no sound.

import { useCallback, useEffect, useRef, useState } from "react";
import type { IntegrityKind } from "../../../contracts";
import type { FaceState } from "../proctor";
import { proctoringApi } from "./api";
import { ProctoringEngine, type Reminder } from "./engine";
import { HttpSink, syncClock } from "./httpSink";
import { ServerClock } from "./serverClock";
import { MONITOR_POLL_MS } from "./thresholds";

/** From the join response, held in memory only. */
export interface ProctoringPass {
  interviewId: string;
  token: string;
}

export interface ShownReminder {
  kind: IntegrityKind;
  message: string;
  /** 1 to 3, as the policy counted it. */
  index: number | null;
}

/** How often the face check is read into the engine. */
const TICK_MS = 250;

type ShareState = "monitor" | "not_monitor" | "stopped";

function shareState(screen: MediaStream | null): ShareState {
  const track = screen?.getVideoTracks()[0];
  if (!track || track.readyState !== "live") return "stopped";
  const surface = (track.getSettings() as { displaySurface?: string }).displaySurface;
  return surface && surface !== "monitor" ? "not_monitor" : "monitor";
}

function isExtended(): boolean | null {
  const s = window.screen as Screen & { isExtended?: boolean };
  return typeof s.isExtended === "boolean" ? s.isExtended : null;
}

export function useIntegrity({
  active,
  pass,
  face,
  screen,
  gazeRemindersOff,
}: {
  active: boolean;
  pass: ProctoringPass | null;
  face: FaceState;
  screen: MediaStream | null;
  gazeRemindersOff: boolean;
}): { reminder: ShownReminder | null; dismiss: () => void; finish: () => Promise<void> } {
  const [reminder, setReminder] = useState<ShownReminder | null>(null);
  const engineRef = useRef<ProctoringEngine | null>(null);
  const sinkRef = useRef<HttpSink | null>(null);
  const clockRef = useRef<ServerClock | null>(null);
  const closed = useRef(false);
  const faceRef = useRef(face);
  faceRef.current = face;
  const shareRef = useRef<ShareState | null>(null);

  const now = () => clockRef.current?.now() ?? null;

  useEffect(() => {
    if (!active || !pass) return;
    closed.current = false;
    const clock = new ServerClock(pass.interviewId);
    const api = proctoringApi();
    const sink = new HttpSink({ interviewId: pass.interviewId, pass: pass.token, api, clock });
    const engine = new ProctoringEngine({
      sink,
      baseline: null,
      gazeRemindersOff,
      idPrefix: `meet-${Math.random().toString(36).slice(2, 8)}`, // fresh ids on every page load
      onReminder: (r: Reminder) => {
        const decided = [...engine.decisions].reverse().find((d) => d.kind === r.kind);
        setReminder({ kind: r.kind, message: r.message, index: decided?.decision.reminder_index ?? null });
      },
    });
    clockRef.current = clock;
    sinkRef.current = sink;
    engineRef.current = engine;
    // The clock first; until it is set, nothing is timed, so nothing is sent.
    void syncClock(api, pass.interviewId, pass.token, clock);

    const tick = window.setInterval(() => {
      const t = clock.now();
      if (t === null || closed.current) return;
      const f = faceRef.current;
      if (f.status === "ready") {
        engine.frame({
          t, capturedAt: performance.now(), faces: f.faces, irisPx: null, distanceCm: null, yaw: null, pitch: null,
          eyeH: null, eyeV: null, faceLuma: null, frameLuma: null, hands: null, width: 0, height: 0, detectMs: 0,
        });
        if (f.faces === 1) engine.judgedGaze(t, f.away);
      } else engine.tick(t);
    }, TICK_MS);

    const at = (fn: (t: number) => void) => {
      const t = clock.now();
      if (t !== null && !closed.current) fn(t);
    };
    const onVisibility = () => {
      at((t) => engine.browser(t, { type: "visibility", visible: document.visibilityState === "visible" }));
      if (document.visibilityState === "hidden") sink.flushNow(); // survives the page going away
    };
    const onBlur = () => at((t) => engine.browser(t, { type: "focus", focused: false }));
    const onFocus = () => at((t) => engine.browser(t, { type: "focus", focused: true }));
    const onPaste = () => at((t) => engine.browser(t, { type: "paste" }));
    let monitors: boolean | null = null;
    const checkMonitors = () => {
      const extended = isExtended();
      if (extended === null || extended === monitors) return;
      at((t) => {
        monitors = extended;
        engine.browser(t, { type: "monitors", extended });
      });
    };
    const monitorPoll = window.setInterval(checkMonitors, MONITOR_POLL_MS);
    // Closing the page: every open event goes with its end, by keepalive.
    const onPageHide = () => {
      const t = clock.now();
      if (t === null || closed.current) return;
      closed.current = true;
      engine.close(t);
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);
    document.addEventListener("paste", onPaste);
    window.addEventListener("pagehide", onPageHide);

    return () => {
      window.clearInterval(tick);
      window.clearInterval(monitorPoll);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("paste", onPaste);
      window.removeEventListener("pagehide", onPageHide);
      onPageHide();
      engineRef.current = null;
      sinkRef.current = null;
      clockRef.current = null;
    };
  }, [active, pass, gazeRemindersOff]);

  // The screen share: whole screen, a window instead, or stopped.
  useEffect(() => {
    if (!active || !pass) return;
    const report = () => {
      const state = shareState(screen);
      if (state === shareRef.current) return;
      const t = now();
      const engine = engineRef.current;
      if (t === null || !engine || closed.current) return; // retried on the next check
      shareRef.current = state;
      engine.browser(t, { type: "share", state });
    };
    report();
    const track = screen?.getVideoTracks()[0];
    track?.addEventListener("ended", report);
    const poll = window.setInterval(report, 1000);
    return () => {
      track?.removeEventListener("ended", report);
      window.clearInterval(poll);
    };
  }, [active, pass, screen]);

  /** The End button: close every open event and give the sender up to 3 s to send them. */
  const finish = useCallback(async () => {
    const engine = engineRef.current;
    const sink = sinkRef.current;
    const t = now();
    if (!engine || !sink || t === null || closed.current) return;
    closed.current = true;
    engine.close(t);
    await sink.drain(3000);
  }, []);

  const dismiss = useCallback(() => setReminder(null), []);
  return { reminder, dismiss, finish };
}
