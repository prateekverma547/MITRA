// Media helpers for the candidate room: sound levels, whole-screen sharing,
// the page-out-of-view watcher, and Daily participants reduced to what the room
// draws. Nothing here reaches the interviewer.

import type { DailyParticipant } from "@daily-co/daily-js";
import { useEffect, useRef, useState } from "react";

// ---- sound level -------------------------------------------------------------------------------

let shared: AudioContext | null = null;
function audioContext(): AudioContext | null {
  try {
    shared ??= new AudioContext();
    if (shared.state === "suspended") shared.resume().catch(() => undefined);
    return shared;
  } catch {
    return null;
  }
}

/**
 * Calls `onLevel` about 30 times a second with the track's loudness, 0 to 1.
 * The callback writes straight to the DOM (a CSS variable), so a level change
 * never re-renders React.
 */
export function useLevel(track: MediaStreamTrack | undefined, onLevel: (level: number) => void) {
  const callback = useRef(onLevel);
  callback.current = onLevel;
  useEffect(() => {
    if (!track) {
      callback.current(0);
      return;
    }
    const ctx = audioContext();
    if (!ctx) return;
    const source = ctx.createMediaStreamSource(new MediaStream([track]));
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    source.connect(analyser);
    const data = new Float32Array(analyser.fftSize);
    let smooth = 0;
    let frame = 0;
    let last = 0;
    const tick = (t: number) => {
      frame = requestAnimationFrame(tick);
      if (t - last < 33) return;
      last = t;
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += v * v;
      const rms = Math.sqrt(sum / data.length);
      const level = Math.min(1, rms * 6);
      smooth = level > smooth ? level : smooth * 0.82 + level * 0.18;
      callback.current(smooth);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      source.disconnect();
      analyser.disconnect();
    };
  }, [track]);
}

/** True while the track is audibly speaking, with a short hold so it does not flicker. */
export function useSpeaking(track: MediaStreamTrack | undefined, threshold = 0.06): boolean {
  const [speaking, setSpeaking] = useState(false);
  const since = useRef(0);
  useLevel(track, (level) => {
    const now = performance.now();
    if (level > threshold) {
      since.current = now;
      setSpeaking(true);
    } else if (now - since.current > 450) {
      setSpeaking(false);
    }
  });
  return speaking;
}

// ---- whole-screen sharing ----------------------------------------------------------------------

export type ShareProblem = "denied" | "wrong-surface" | "unsupported" | "failed";

export const shareProblemText: Record<ShareProblem, string> = {
  denied: "Screen sharing was cancelled. Share your entire screen to continue.",
  "wrong-surface": "You shared a window or a tab. Choose Entire screen instead.",
  unsupported: "This browser cannot share the screen. Use Chrome or Edge on a laptop or desktop.",
  failed: "Screen sharing did not start. Try again.",
};

export function canShareScreen(): boolean {
  return typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getDisplayMedia);
}

/** Asks for the entire screen and refuses a window or a tab. */
export async function pickWholeScreen(): Promise<{ stream: MediaStream } | { problem: ShareProblem }> {
  if (!canShareScreen()) return { problem: "unsupported" };
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: "monitor", frameRate: { ideal: 5, max: 10 } } as MediaTrackConstraints,
      audio: false,
      // Chrome's hints: offer screens first, hide this tab, no switching mid-share.
      ...({ monitorTypeSurfaces: "include", selfBrowserSurface: "exclude", surfaceSwitching: "exclude" } as object),
    });
    const track = stream.getVideoTracks()[0];
    const surface = (track?.getSettings() as { displaySurface?: string } | undefined)?.displaySurface;
    // Browsers that do not report the surface are taken at their word.
    if (surface && surface !== "monitor") {
      stream.getTracks().forEach((t) => t.stop());
      return { problem: "wrong-surface" };
    }
    return { stream };
  } catch (e) {
    return { problem: (e as DOMException)?.name === "NotAllowedError" ? "denied" : "failed" };
  }
}

/** Chrome reports a second display; other browsers say nothing, which is not a yes. */
export function hasSecondDisplay(): boolean {
  return Boolean((window.screen as Screen & { isExtended?: boolean }).isExtended);
}

// ---- participants ------------------------------------------------------------------------------

export interface Peer {
  id: string;
  name: string;
  local: boolean;
  audio?: MediaStreamTrack;
  video?: MediaStreamTrack;
  screen?: MediaStreamTrack;
  micOn: boolean;
  camOn: boolean;
  /** Permission state of the local camera or microphone. */
  camBlocked: boolean;
  micBlocked: boolean;
}

type TrackState = { state: string; persistentTrack?: MediaStreamTrack } | undefined;
const playable = (s: TrackState) => (s && s.state === "playable" ? s.persistentTrack : undefined);
const on = (s: TrackState) => Boolean(s && ["playable", "loading", "sendable"].includes(s.state));

export function toPeer(p: DailyParticipant): Peer {
  const t = p.tracks;
  return {
    id: p.session_id,
    name: p.user_name || (p.local ? "You" : "Guest"),
    local: p.local,
    // Our own sound is measured but never played back to us.
    audio: playable(t.audio as TrackState),
    video: playable(t.video as TrackState),
    screen: playable(t.screenVideo as TrackState),
    micOn: on(t.audio as TrackState),
    camOn: on(t.video as TrackState),
    camBlocked: t.video?.state === "blocked",
    micBlocked: t.audio?.state === "blocked",
  };
}

/** Mitra joins as "Interviewer" (app/interviews.py) or by its own name. */
export function isMitra(peer: Peer, botName: string): boolean {
  const n = peer.name.trim().toLowerCase();
  return n === "interviewer" || n === botName.trim().toLowerCase();
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
