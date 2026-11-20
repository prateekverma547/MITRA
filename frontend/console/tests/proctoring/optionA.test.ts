// Option A: the console room's own face check and the browser's events, through
// Track I's engine. What the candidate does, what they are shown, and what is
// stored for the report.
import { describe, expect, it } from "vitest";
import { ProctoringEngine, type Reminder } from "../../src/features/interview-room/proctoring/engine";
import { InMemorySink } from "../../src/features/interview-room/proctoring/sink";
import type { TimedReading } from "../../src/features/interview-room/proctoring/types";

/** One read of our face check, 4 times a second, as useIntegrity feeds it. */
function reading(t: number, faces: number): TimedReading {
  return {
    t, capturedAt: t * 1000, faces, irisPx: null, distanceCm: null, yaw: null, pitch: null, eyeH: null, eyeV: null,
    faceLuma: null, frameLuma: null, hands: null, width: 0, height: 0, detectMs: 0,
  };
}

function room(gazeRemindersOff = false) {
  const sink = new InMemorySink();
  const reminders: Reminder[] = [];
  const engine = new ProctoringEngine({ sink, baseline: null, gazeRemindersOff, onReminder: (r) => reminders.push(r), idPrefix: "t" });
  /** Our face check from `from` to `to` seconds: `faces` in view, head turned away or not. */
  const feed = (from: number, to: number, faces: number, away = false) => {
    for (let t = from; t < to; t += 0.25) {
      engine.frame(reading(t, faces));
      if (faces === 1) engine.judgedGaze(t, away);
    }
  };
  return { engine, sink, reminders, feed, events: () => sink.snapshot().events };
}

describe("a camera covered by a hand", () => {
  it("is one face_missing event, with its start and its end, recorded as a warning shown throughout", () => {
    const r = room();
    r.feed(0, 10, 1);
    r.feed(10, 22, 0); // hand over the camera for 12 s
    r.feed(22, 30, 1);
    const [ev] = r.events();
    expect(r.events()).toHaveLength(1);
    expect(ev).toMatchObject({ kind: "face_missing", start_seconds: 10, reminder: "technical_prompt", reminder_index: null });
    expect(ev.end_seconds).toBeCloseTo(21.75, 1);
    // The room shows its own warning while the face is gone (Room.tsx), not a capped toast.
    expect(r.reminders).toEqual([]);
  });

  it("every time is recorded, with no limit (the warning is never capped at three)", () => {
    const r = room();
    for (let i = 0; i < 5; i++) {
      const at = i * 30;
      r.feed(at, at + 10, 1);
      r.feed(at + 10, at + 20, 0);
    }
    r.feed(150, 155, 1);
    const shown = r.events().filter((e) => e.kind === "face_missing");
    expect(shown).toHaveLength(5);
    expect(shown.every((e) => e.reminder === "technical_prompt")).toBe(true);
  });

  it("other warnings keep their limit of three", () => {
    const r = room();
    for (let i = 0; i < 4; i++) {
      const at = i * 150;
      r.feed(at, at + 10, 1);
      r.feed(at + 10, at + 20, 2); // a second person
    }
    r.feed(600, 605, 1);
    const seen = r.events().filter((e) => e.kind === "second_face");
    expect(seen.map((e) => e.reminder_index)).toEqual([1, 2, 3, null]);
    expect(r.reminders).toHaveLength(3);
  });
});

describe("the screen share", () => {
  it("stopped mid-interview is a share_stopped event, a set-up prompt rather than a warning, closed when shared again", () => {
    const r = room();
    r.engine.browser(5, { type: "share", state: "monitor" });
    r.engine.browser(40, { type: "share", state: "stopped" });
    r.engine.browser(75, { type: "share", state: "monitor" });
    const [ev] = r.events();
    expect(ev).toMatchObject({ kind: "share_stopped", start_seconds: 40, end_seconds: 75, reminder: "technical_prompt" });
    expect(r.reminders).toEqual([]); // the room's own banner asks them to share again
  });

  it("still stopped when the call ends is closed at the end, never left open", () => {
    const r = room();
    r.engine.browser(40, { type: "share", state: "stopped" });
    r.engine.close(90);
    expect(r.events()[0]).toMatchObject({ kind: "share_stopped", end_seconds: 90 });
    expect(r.sink.snapshot().closed).toBe(true);
  });
});

describe("looking away, from our head-turn check", () => {
  it("a glance never counts; six seconds does", () => {
    const r = room();
    r.feed(0, 10, 1);
    r.feed(10, 12, 1, true); // a 2 s glance
    r.feed(12, 30, 1);
    expect(r.events()).toEqual([]);
    r.feed(30, 40, 1, true); // 10 s away
    r.feed(40, 45, 1);
    expect(r.events()[0]).toMatchObject({ kind: "looking_away", start_seconds: 30, reminder: "nudged" });
  });

  it("with gaze reminders turned off at consent, it is recorded but never shown", () => {
    const r = room(true);
    r.feed(0, 5, 1);
    r.feed(5, 15, 1, true);
    r.feed(15, 20, 1);
    expect(r.events()[0]).toMatchObject({ kind: "looking_away", reminder: "recorded_only" });
    expect(r.reminders).toEqual([]);
    expect(r.sink.snapshot().accommodation?.gaze_reminders_off).toBe(true);
  });
});

describe("a second person, and leaving the tab", () => {
  it("two faces for a few seconds is second_face", () => {
    const r = room();
    r.feed(0, 5, 1);
    r.feed(5, 12, 2);
    r.feed(12, 15, 1);
    expect(r.events()[0]).toMatchObject({ kind: "second_face", start_seconds: 5, reminder: "nudged" });
  });

  it("a hidden tab is recorded, and its reminder waits until they are back", () => {
    const r = room();
    r.engine.browser(20, { type: "visibility", visible: false });
    expect(r.reminders).toEqual([]);
    r.engine.browser(31, { type: "visibility", visible: true });
    expect(r.events()[0]).toMatchObject({ kind: "tab_hidden", start_seconds: 20, end_seconds: 31 });
    expect(r.reminders.map((x) => x.message)).toEqual(["Please stay on the interview tab"]);
  });
});

describe("what leaves the page", () => {
  it("only events and status readings: a reading once a second with the faces in view", () => {
    const r = room();
    r.feed(0, 3, 1);
    expect(r.sink.snapshot().status).toEqual({
      kind: "integrity_status", distance_cm: null, distance_in_band: null, faces_in_view: 1, page_visible: true,
    });
  });
});
