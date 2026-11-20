// Development only (`npm run dev`): `#/meet?preview=consent|lobby|room|room-hr|done`
// shows a step with sample details, so each screen can be reviewed without
// booking an interview. Never reachable in a production build.

import type { DailyCall } from "@daily-co/daily-js";
import type { Peer } from "./media";
import type { RoomFacts } from "./room-api";

export const PREVIEW_STEPS = ["consent", "lobby", "room", "room-hr", "done"] as const;
export type PreviewStep = (typeof PREVIEW_STEPS)[number];

export function previewStep(value: string | null): PreviewStep | null {
  if (!import.meta.env.DEV || !value) return null;
  return (PREVIEW_STEPS as readonly string[]).includes(value) ? (value as PreviewStep) : null;
}

export function previewFacts(step: PreviewStep): RoomFacts {
  return {
    round: step === "room-hr" ? "hr" : "technical",
    status: "open",
    role_title: "AI Engineer, Applied LLM",
    organisation: "Appinventiv",
    first_name: "Aarav",
    duration_minutes: 40,
    recording: true,
    retention_days: 10,
    bot_name: "Mitra",
  };
}

export function previewPeers(step: PreviewStep): Peer[] {
  const base = { micOn: true, camOn: false, camBlocked: false, micBlocked: false };
  const me: Peer = { ...base, id: "me", name: "Aarav Mehta", local: true };
  if (step === "room-hr") return [me, { ...base, id: "hr", name: "Neha Kapoor", local: false }];
  return [me, { ...base, id: "bot", name: "Interviewer", local: false }, { ...base, id: "w", name: "Neha Kapoor", local: false }];
}

/** A call object that does nothing, for the room preview. */
export function previewCall(): DailyCall {
  const local = { tracks: { audio: { state: "playable" }, video: { state: "playable" } } };
  const stub: Record<string, unknown> = {
    on: () => stub,
    off: () => stub,
    participants: () => ({ local }),
    leave: async () => undefined,
    setLocalAudio: () => stub,
    setLocalVideo: () => stub,
    startScreenShare: () => undefined,
    stopScreenShare: () => undefined,
  };
  return stub as unknown as DailyCall;
}
