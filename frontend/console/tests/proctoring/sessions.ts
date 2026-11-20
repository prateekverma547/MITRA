// Shared by the engine and event-shape tests (a test file must not import another test file).
import type { IntegrityEvent } from "../../src/contracts";
import { ProctoringEngine, type Reminder } from "../../src/features/interview-room/proctoring/engine";
import type { GazeBaseline } from "../../src/features/interview-room/proctoring/gaze";
import { InMemorySink } from "../../src/features/interview-room/proctoring/sink";
import type { TimedReading } from "../../src/features/interview-room/proctoring/types";
import { LOOK_DOWN, stream } from "./streams";

const BASELINE: GazeBaseline = { yaw: 0, pitch: 0, eyeH: 0, eyeV: 0, samples: 200 };

export function session(opts: { gazeRemindersOff?: boolean; baseline?: GazeBaseline | null } = {}) {
  const sink = new InMemorySink();
  const reminders: Reminder[] = [];
  const engine = new ProctoringEngine({
    sink,
    baseline: opts.baseline === undefined ? BASELINE : opts.baseline,
    gazeRemindersOff: opts.gazeRemindersOff ?? false,
    onReminder: (r) => reminders.push(r),
    idPrefix: "test",
  });
  const feed = (rs: TimedReading[]) => rs.forEach((r) => engine.frame(r));
  const events = () => sink.snapshot().events;
  return { sink, engine, reminders, feed, events };
}

/** A varied session touching every browser-measurable kind, closed. */
export function variedSession() {
  const s = session();
  s.feed(stream(0, 20));
  s.feed(stream(20, 32, () => LOOK_DOWN));
  s.feed(stream(32, 45, () => ({ distanceCm: 30 })));
  s.feed(stream(45, 50, () => ({ faces: 0, distanceCm: null })));
  s.feed(stream(50, 55, () => ({ faces: 2 })));
  s.feed(stream(55, 60));
  s.engine.browser(60, { type: "visibility", visible: false });
  s.engine.browser(63, { type: "visibility", visible: true });
  s.engine.browser(64, { type: "focus", focused: false });
  s.engine.tick(67);
  s.engine.browser(68, { type: "focus", focused: true });
  s.engine.browser(69, { type: "fullscreen", on: true });
  s.engine.browser(70, { type: "fullscreen", on: false });
  s.engine.browser(71, { type: "paste" });
  s.engine.browser(72, { type: "monitors", extended: true });
  s.engine.browser(73, { type: "share", state: "stopped" });
  s.engine.browser(74, { type: "share", state: "not_monitor" });
  s.engine.close(80);
  return s;
}


/** Every event a varied session produces, for the shape checks. */
export function variedSessionEvents(): IntegrityEvent[] {
  return variedSession().events();
}
