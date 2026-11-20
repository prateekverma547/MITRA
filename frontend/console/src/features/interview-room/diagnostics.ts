// Measurement only (2026-11-20): what this browser was doing during a MITRA round.
//
// The first MITRA round after the console is opened is slow and later ones in the
// same tab are fast. The stored reply timings put the slowness in OpenAI's turn
// detection, and the face check is loaded once per tab (proctor.ts), so the room
// reports: how long the face check took to load, main-thread stalls ("long tasks",
// over 50 ms) during the call, how long joining took, whether this was the tab's
// first call, and the microphone's processing settings. Numbers only; sent to
// hiring/diagnostics.py with the proctoring token. Remove once the cause is known.

import { useEffect, useRef } from "react";
import type { ProctoringPass } from "./proctoring/useIntegrity";

/** Filled in as the room goes: the face check and the join. */
export const roomTimings: {
  faceCheckLoadMs: number | null;
  faceCheckStatus: string | null;
  joinRequestMs: number | null;
  dailyJoinMs: number | null;
} = { faceCheckLoadMs: null, faceCheckStatus: null, joinRequestMs: null, dailyJoinMs: null };

/** Calls made in this tab before the current one (the page, not the console, decides "tab"). */
let callsInThisTab = 0;

interface LongTaskTally {
  count: number;
  totalMs: number;
  maxMs: number;
  firstMinuteCount: number;
  firstMinuteMs: number;
}

export function useRoomDiagnostics({ active, pass, mic }: { active: boolean; pass: ProctoringPass | null; mic?: MediaStreamTrack }) {
  const micRef = useRef(mic);
  micRef.current = mic;

  useEffect(() => {
    if (!active || !pass) return;
    const earlierCalls = callsInThisTab++;
    const startedAt = performance.now();
    const tabAgeAtJoin = startedAt / 1000;
    const tally: LongTaskTally = { count: 0, totalMs: 0, maxMs: 0, firstMinuteCount: 0, firstMinuteMs: 0 };

    let observer: PerformanceObserver | null = null;
    try {
      observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          tally.count += 1;
          tally.totalMs += entry.duration;
          tally.maxMs = Math.max(tally.maxMs, entry.duration);
          if (entry.startTime - startedAt < 60_000) {
            tally.firstMinuteCount += 1;
            tally.firstMinuteMs += entry.duration;
          }
        }
      });
      observer.observe({ type: "longtask", buffered: false });
    } catch {
      observer = null; // a browser without long-task timing still sends the rest
    }

    const send = (phase: "minute" | "end") => {
      const settings = (micRef.current?.getSettings() ?? {}) as MediaTrackSettings;
      const body = {
        phase,
        seconds_in_call: Math.round((performance.now() - startedAt) / 100) / 10,
        tab_age_seconds_at_join: Math.round(tabAgeAtJoin * 10) / 10,
        earlier_calls_in_tab: earlierCalls,
        face_check_load_ms: roomTimings.faceCheckLoadMs,
        face_check_status: roomTimings.faceCheckStatus,
        join_request_ms: roomTimings.joinRequestMs,
        daily_join_ms: roomTimings.dailyJoinMs,
        long_tasks: {
          count: tally.count,
          total_ms: Math.round(tally.totalMs),
          max_ms: Math.round(tally.maxMs),
          first_minute_count: tally.firstMinuteCount,
          first_minute_ms: Math.round(tally.firstMinuteMs),
        },
        mic: {
          echo_cancellation: typeof settings.echoCancellation === "boolean" ? settings.echoCancellation : null,
          noise_suppression: typeof settings.noiseSuppression === "boolean" ? settings.noiseSuppression : null,
          auto_gain_control: typeof settings.autoGainControl === "boolean" ? settings.autoGainControl : null,
          sample_rate: typeof settings.sampleRate === "number" ? settings.sampleRate : null,
        },
        browser: navigator.userAgent.slice(0, 80),
      };
      try {
        fetch(`/api/room/diagnostics/${encodeURIComponent(pass.interviewId)}`, {
          method: "POST",
          keepalive: true,
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${pass.token}` },
          body: JSON.stringify(body),
          credentials: "omit",
        }).catch(() => undefined);
      } catch {
        // measurement never breaks an interview
      }
    };

    const minute = window.setTimeout(() => send("minute"), 60_000);
    const onHide = () => send("end");
    window.addEventListener("pagehide", onHide);
    return () => {
      window.clearTimeout(minute);
      window.removeEventListener("pagehide", onHide);
      observer?.disconnect();
      send("end");
    };
  }, [active, pass]);
}
