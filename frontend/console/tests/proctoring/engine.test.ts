// Ported from Track I (frontend/candidate-app/tests/proctoring/engine.test.ts), unchanged but for
// the import paths and this import of the test functions their setup made global.
import { describe, expect, it } from "vitest";
// The proctoring engine on constructed sessions at the live rate (8 frames a
// second): the pass-bar scenarios from DEV_PLAN section 7 as far as numbers
// can stand in for a camera, plus every browser signal and the event shape.
import { session, variedSession, variedSessionEvents } from "./sessions";
import { LOOK_DOWN, LOOK_UP_SIDE, seeded, stream } from "./streams";

describe("pass bar, as numbers", () => {
  it("catches a 10-second phone glance: confirmed 6 s in, one reminder, start and end at the look", () => {
    const s = session();
    s.feed(stream(0, 30));
    s.feed(stream(30, 40, () => LOOK_DOWN));
    s.feed(stream(40, 60));
    const [ev] = s.events();
    expect(s.events()).toHaveLength(1);
    expect(ev).toMatchObject({ kind: "looking_away", start_seconds: 30, reminder: "nudged", reminder_index: 1 });
    expect(ev.end_seconds).toBeCloseTo(39.88, 1);
    expect(s.reminders).toEqual([{ kind: "looking_away", message: "Please keep your eyes on the screen", t: 36 }]);
  });

  it("catches leaning in to 30 cm within 5 s, asks them to sit back, and ends when they do", () => {
    const s = session();
    s.feed(stream(0, 20));
    s.feed(stream(20, 32, () => ({ distanceCm: 30, irisPx: 39 })));
    s.feed(stream(32, 50));
    const [ev] = s.events();
    expect(ev).toMatchObject({ kind: "distance_out_of_band", reminder: "nudged", detail: "about 30 cm" });
    // The 1 s median needs half its window at 30 cm before it reads 30.
    expect(ev.start_seconds).toBeGreaterThanOrEqual(20);
    expect(ev.start_seconds).toBeLessThan(21);
    expect(s.reminders[0].message).toBe("Please sit back a little");
    // Decided when confirmed, not when it began: 4 s held, plus the median's lag.
    expect(s.reminders[0].t).toBeGreaterThanOrEqual(24);
    expect(s.reminders[0].t).toBeLessThan(25.5);
    expect(s.engine.decisions[0].t).toBe(s.reminders[0].t);
    expect(ev.end_seconds).toBeGreaterThan(31);
    expect(ev.end_seconds).toBeLessThan(33.5);
  });

  it("asks them to come closer when too far", () => {
    const s = session();
    s.feed(stream(0, 15, () => ({ distanceCm: 95 })));
    expect(s.reminders[0].message).toBe("Please come a little closer");
  });

  it("does not react to a wobble on the band's edge (hysteresis)", () => {
    const s = session();
    const r = seeded(7);
    s.feed(stream(0, 120, () => ({ distanceCm: 49 + r() * 2 }))); // 49-51 cm: inside the 2 cm margin
    expect(s.events()).toEqual([]);
  });

  it("gives at most one reminder in an ordinary 10-minute session with thinking glances", () => {
    // Glances of 1-4 s every 15-40 s, up and to the side or down, plus two
    // longer 5 s pauses; distance drifting 55-75 cm. Deterministic seed.
    for (const seed of [1, 2, 3, 4, 5]) {
      const r = seeded(seed);
      const looks: [number, number, object][] = [];
      for (let t = 20; t < 590; t += 15 + r() * 25) looks.push([t, t + 1 + r() * 3, r() < 0.5 ? LOOK_UP_SIDE : LOOK_DOWN]);
      looks.push([200, 205, LOOK_UP_SIDE], [420, 425, LOOK_UP_SIDE]);
      const s = session();
      s.feed(
        stream(0, 600, (t) => {
          const look = looks.find(([a, b]) => t >= a && t < b);
          return { distanceCm: 65 + 10 * Math.sin(t / 50), ...(look ? look[2] : {}) };
        }),
      );
      expect(s.reminders.length, `seed ${seed}`).toBeLessThanOrEqual(1);
    }
  });
});

describe("faces", () => {
  it("records a face missing for 3 s, not for 1 s", () => {
    const s = session();
    s.feed(stream(0, 10));
    s.feed(stream(10, 11, () => ({ faces: 0, distanceCm: null })));
    s.feed(stream(11, 20));
    expect(s.events()).toEqual([]);
    s.feed(stream(20, 25, () => ({ faces: 0, distanceCm: null })));
    s.feed(stream(25, 30));
    expect(s.events()).toHaveLength(1);
    // Track H, 2026-11-20: a technical prompt. The room shows its own warning for as
    // long as the face is out of view, so the engine sends no capped toast.
    expect(s.events()[0]).toMatchObject({ kind: "face_missing", start_seconds: 20, reminder: "technical_prompt", reminder_index: null });
    expect(s.reminders).toEqual([]);
  });

  it("records a second face for 2 s, not for a passer-by of 1 s", () => {
    const s = session();
    s.feed(stream(0, 10, (t) => ({ faces: t >= 5 && t < 6 ? 2 : 1 })));
    expect(s.events()).toEqual([]);
    s.feed(stream(10, 20, (t) => ({ faces: t >= 12 && t < 16 ? 2 : 1 })));
    expect(s.events()[0]).toMatchObject({ kind: "second_face", start_seconds: 12 });
    expect(s.engine.decisions[0].t).toBe(14); // began at 12, confirmed 2 s later
    expect(s.reminders.map((r) => r.message)).toEqual(["Please make sure only you are in view."]);
    expect(s.events()[0].end_seconds).toBeCloseTo(15.88, 1);
  });

  it("does not judge gaze or distance on frames without exactly one face", () => {
    const s = session();
    s.feed(stream(0, 30, () => ({ faces: 2, ...LOOK_DOWN, distanceCm: 20 })));
    expect(s.events().map((e) => e.kind)).toEqual(["second_face"]);
  });
});

describe("reminder cap and cooldown, end to end", () => {
  /** A 7 s look down starting at each of `starts`. */
  const looksAt = (starts: number[]) => (t: number) => (starts.some((a) => t >= a && t < a + 7) ? LOOK_DOWN : {});

  it("shows three gaze reminders at most, then records the rest silently", () => {
    const s = session();
    s.feed(stream(0, 1000, looksAt([10, 200, 400, 600, 800])));
    const ev = s.events();
    expect(ev).toHaveLength(5);
    expect(ev.map((e) => e.reminder_index)).toEqual([1, 2, 3, null, null]);
    expect(ev.slice(3).every((e) => e.reminder === "recorded_only")).toBe(true);
    expect(s.reminders).toHaveLength(3);
  });

  it("does not show a second gaze reminder within two minutes, then shows it", () => {
    const s = session();
    s.feed(stream(0, 300, looksAt([10, 70, 150])));
    expect(s.events().map((e) => e.reminder)).toEqual(["nudged", "recorded_only", "nudged"]);
    expect(s.reminders.map((r) => r.t)).toEqual([16, 156]);
  });
});

describe("the accommodation", () => {
  it("records gaze, marks it, and never shows a gaze reminder; other kinds still show", () => {
    const s = session({ gazeRemindersOff: true });
    s.feed(stream(0, 20, () => LOOK_DOWN));
    s.feed(stream(20, 40, () => ({ distanceCm: 30 })));
    const [gaze, dist] = s.events();
    expect(gaze).toMatchObject({ kind: "looking_away", reminder: "recorded_only", reminder_index: null });
    expect(gaze.detail).toContain("accommodation");
    expect(dist).toMatchObject({ kind: "distance_out_of_band", reminder: "nudged" });
    expect(s.reminders.map((r) => r.kind)).toEqual(["distance_out_of_band"]);
    expect(s.sink.snapshot().accommodation).toEqual({
      gaze_reminders_off: true,
      note: "Gaze reminders turned off at consent; gaze still recorded.",
    });
  });

  it("without a warm-up baseline, gaze is not measured at all", () => {
    const s = session({ baseline: null });
    s.feed(stream(0, 30, () => LOOK_DOWN));
    expect(s.events()).toEqual([]);
    expect(s.engine.gazeMeasured).toBe(false);
  });
});

describe("browser signals", () => {
  it("records a tab switch at once and shows the reminder when they are back", () => {
    const s = session();
    s.engine.browser(50, { type: "visibility", visible: false });
    expect(s.events()[0]).toMatchObject({ kind: "tab_hidden", start_seconds: 50, end_seconds: null, reminder: "nudged" });
    expect(s.reminders).toEqual([]); // not shown while the tab is hidden
    s.engine.browser(58, { type: "visibility", visible: true });
    expect(s.events()[0].end_seconds).toBe(58);
    expect(s.reminders).toEqual([{ kind: "tab_hidden", message: "Please stay on the interview tab", t: 58 }]);
  });

  it("records an unfocused window only after 2 s, from when it lost focus", () => {
    const s = session();
    s.engine.browser(10, { type: "focus", focused: false });
    s.engine.tick(11);
    s.engine.browser(11.5, { type: "focus", focused: true });
    expect(s.events()).toEqual([]);
    s.engine.browser(20, { type: "focus", focused: false });
    s.engine.tick(21);
    s.engine.tick(22.25);
    s.engine.browser(30, { type: "focus", focused: true });
    expect(s.events()[0]).toMatchObject({ kind: "tab_unfocused", start_seconds: 20, end_seconds: 30, reminder: "nudged" });
  });

  it("logs paste only, merging pastes within 10 s into one event that closes after 10 s quiet", () => {
    const s = session();
    s.engine.browser(10, { type: "paste" });
    s.engine.browser(14, { type: "paste" });
    s.engine.tick(20);
    expect(s.events()[0]).toMatchObject({ repeats: 1, end_seconds: null }); // still open; nothing rewritten yet
    s.engine.tick(24.5);
    s.engine.browser(40, { type: "paste" });
    s.engine.close(45);
    expect(s.events().map((e) => [e.kind, e.repeats, e.start_seconds, e.end_seconds, e.reminder])).toEqual([
      ["paste", 2, 10, 14, "recorded_only"],
      ["paste", 1, 40, 40, "recorded_only"],
    ]);
    expect(s.reminders).toEqual([]);
  });

  it("logs leaving full screen only after having been in it", () => {
    const s = session();
    s.engine.browser(1, { type: "fullscreen", on: false });
    expect(s.events()).toEqual([]);
    s.engine.browser(2, { type: "fullscreen", on: true });
    s.engine.browser(30, { type: "fullscreen", on: false });
    s.engine.browser(45, { type: "fullscreen", on: true });
    expect(s.events()[0]).toMatchObject({ kind: "left_full_screen", start_seconds: 30, end_seconds: 45, reminder: "recorded_only" });
  });

  it("logs a second monitor once while it stays attached", () => {
    const s = session();
    for (const t of [5, 10, 15]) s.engine.browser(t, { type: "monitors", extended: true });
    s.engine.browser(20, { type: "monitors", extended: false });
    expect(s.events()).toHaveLength(1);
    expect(s.events()[0]).toMatchObject({ kind: "second_monitor", start_seconds: 5, end_seconds: 20, reminder: "recorded_only" });
  });

  it("treats every share stop as a technical prompt, never capped", () => {
    const s = session();
    for (let i = 0; i < 5; i++) {
      s.engine.browser(10 + i * 10, { type: "share", state: "stopped" });
      s.engine.browser(15 + i * 10, { type: "share", state: "monitor" });
    }
    s.engine.browser(70, { type: "share", state: "not_monitor" });
    const ev = s.events();
    expect(ev.map((e) => e.kind)).toEqual([...Array(5).fill("share_stopped"), "share_not_whole_screen"]);
    expect(s.engine.decisions.every((d) => d.decision.technicalPrompt)).toBe(true);
    // The contract's technical-prompt state: never a reminder, never an index, never capped.
    expect(ev.every((e) => e.reminder === "technical_prompt" && e.reminder_index === null)).toBe(true);
    expect(s.reminders).toEqual([]);
    expect(ev.map((e) => e.detail)).toEqual([...Array(5).fill("screen share stopped"), "a window or tab was shared, not the whole screen"]);
  });

  it("closes everything open when the call ends", () => {
    const s = session();
    s.feed(stream(0, 20, () => LOOK_DOWN));
    s.engine.browser(15, { type: "monitors", extended: true });
    s.engine.close(20);
    expect(s.events().every((e) => e.end_seconds !== null)).toBe(true);
    expect(s.sink.snapshot().closed).toBe(true);
  });

  it("sends a status reading about once a second", () => {
    const s = session();
    s.feed(stream(0, 3, () => ({ distanceCm: 62 })));
    expect(s.sink.snapshot().status).toEqual({
      kind: "integrity_status",
      distance_cm: 62,
      distance_in_band: true,
      faces_in_view: 1,
      page_visible: true,
    });
  });
});

describe("the varied session covers every browser kind", () => {
  it("produces each of the eleven kinds the browser can measure", () => {
    const kinds = new Set(variedSessionEvents().map((e) => e.kind));
    expect([...kinds].sort()).toEqual(
      [
        "distance_out_of_band",
        "face_missing",
        "left_full_screen",
        "looking_away",
        "paste",
        "second_face",
        "second_monitor",
        "share_not_whole_screen",
        "share_stopped",
        "tab_hidden",
        "tab_unfocused",
      ].sort(),
    );
  });
});

describe("what an intake receives", () => {
  // Session C's intake (backend/proctoring/intake.py, its documented rule):
  // the first copy of an event_id is stored as it is; a later copy is accepted
  // only if it closes a still-open event and changes nothing but end_seconds
  // and repeats (repeats never lower); a closed event is never changed. Written
  // here from that rule, not from the engine.
  function replay(history: import("@mitra/ui").IntegrityEvent[]) {
    const stored = new Map<string, import("@mitra/ui").IntegrityEvent>();
    const problems: string[] = [];
    for (const w of history) {
      const old = stored.get(w.event_id);
      if (!old) {
        stored.set(w.event_id, w);
        continue;
      }
      if (JSON.stringify(old) === JSON.stringify(w)) continue;
      if (old.end_seconds !== null) problems.push(`${w.event_id}: changes a closed event`);
      else if (w.end_seconds === null) problems.push(`${w.event_id}: changes an open event without closing it`);
      else {
        const changed = (Object.keys(w) as (keyof typeof w)[]).filter(
          (k) => !["end_seconds", "repeats"].includes(k) && JSON.stringify(w[k]) !== JSON.stringify(old[k]),
        );
        if (changed.length) problems.push(`${w.event_id}: closing copy changes ${changed.join(", ")}`);
        if (w.repeats < old.repeats) problems.push(`${w.event_id}: lowers repeats`);
        stored.set(w.event_id, w);
      }
    }
    return { problems, stored: [...stored.values()] };
  }

  it("every write is an open, or the one closing copy of an open event", () => {
    const { sink } = variedSession();
    const r = replay(sink.snapshot().history);
    expect(r.problems).toEqual([]);
    expect(r.stored.every((e) => e.end_seconds !== null)).toBe(true);
    expect(r.stored).toEqual(sink.snapshot().events);
  });

  it("the replay catches a rewrite of a closed event (it is able to fail)", () => {
    const ev = { ...variedSessionEvents()[0] };
    expect(replay([ev, { ...ev, repeats: ev.repeats + 1 }]).problems).toEqual([`${ev.event_id}: changes a closed event`]);
  });
});
