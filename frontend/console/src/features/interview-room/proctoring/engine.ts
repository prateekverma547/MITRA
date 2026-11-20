import type { IntegrityEvent, IntegrityKind } from "../../../contracts";
import { GazeTracker, isAway, Sustained, type GazeBaseline } from "./gaze";
import { median } from "./geometry";
import { reminderMessage, ReminderPolicy, type ReminderDecision } from "./reminders";
import type { ProctoringSink } from "./sink";
import {
  BAND_ENTER_MARGIN_CM,
  BAND_MAX_CM,
  BAND_MIN_CM,
  DISTANCE_SMOOTH_S,
  DISTANCE_SUSTAIN_S,
  FACE_GRACE_S,
  FACE_MISSING_S,
  PASTE_MERGE_S,
  SECOND_FACE_S,
  UNFOCUSED_S,
} from "./thresholds";
import type { TimedReading } from "./types";

/** Browser-level signals, each already placed on the interview clock by the caller. */
export type BrowserSignal =
  | { type: "visibility"; visible: boolean }
  | { type: "focus"; focused: boolean }
  | { type: "fullscreen"; on: boolean }
  | { type: "paste" }
  | { type: "monitors"; extended: boolean }
  | { type: "share"; state: "monitor" | "not_monitor" | "stopped" };

export interface Reminder {
  kind: IntegrityKind;
  message: string;
  /** Interview clock when it was decided. */
  t: number;
}

export interface EngineOptions {
  sink: ProctoringSink;
  /** Gaze baseline from the warm-up; null means gaze is not measured at all. */
  baseline: GazeBaseline | null;
  /** The accommodation chosen at consent: gaze reminders off, gaze still recorded. */
  gazeRemindersOff: boolean;
  /** Called when a reminder should be shown on screen (already allowed by the policy). */
  onReminder: (r: Reminder) => void;
  /** Prefix for event ids, so ids from two sessions never collide. */
  idPrefix?: string;
}

const round = (s: number) => Math.max(0, Math.round(s * 100) / 100);

/**
 * The proctoring state machine. Pure: it owns no timers, no DOM and no
 * camera. Time comes in with every call (interview clock, seconds), so tests
 * drive it with constructed streams and the browser drives it live.
 *
 * Each signal becomes at most one open event at a time per kind. An event is
 * written to the sink when it is confirmed and again when it ends.
 */
export class ProctoringEngine {
  readonly policy: ReminderPolicy;
  private readonly sink: ProctoringSink;
  private baseline: GazeBaseline | null;
  private readonly gazeOff: boolean;
  private readonly onReminder: (r: Reminder) => void;
  private readonly prefix: string;
  private seq = 0;
  private open = new Map<IntegrityKind, IntegrityEvent>();
  /** Decisions made, in order, for tests and the dev view. */
  readonly decisions: { kind: IntegrityKind; t: number; decision: ReminderDecision }[] = [];

  private distances: { t: number; cm: number }[] = [];
  private distance = new Sustained(DISTANCE_SUSTAIN_S, 1);
  private distanceDirection: "near" | "far" = "near";
  private faceMissing = new Sustained(FACE_MISSING_S, FACE_GRACE_S);
  private secondFace = new Sustained(SECOND_FACE_S, FACE_GRACE_S);
  private gaze = new GazeTracker();
  private unfocusedSince: number | null = null;
  private visible = true;
  private focused = true;
  private wasFullScreen = false;
  private pendingTabReminder: Reminder | null = null;
  private lastPasteAt: number | null = null;
  private latest: { faces: number | null; smoothedCm: number | null } = { faces: null, smoothedCm: null };
  private lastStatusAt = -Infinity;

  constructor(opts: EngineOptions) {
    this.sink = opts.sink;
    this.baseline = opts.baseline;
    this.gazeOff = opts.gazeRemindersOff;
    this.onReminder = opts.onReminder;
    this.prefix = opts.idPrefix ?? "ie";
    this.policy = new ReminderPolicy({ gazeRemindersOff: opts.gazeRemindersOff });
    this.sink.start({
      accommodation: {
        gaze_reminders_off: opts.gazeRemindersOff,
        note: opts.gazeRemindersOff ? "Gaze reminders turned off at consent; gaze still recorded." : null,
      },
    });
  }

  /**
   * Set the gaze baseline after the engine started (the dev route calibrates
   * on the running engine). Gaze is measured from the next frame on.
   */
  setBaseline(b: GazeBaseline | null): void {
    this.baseline = b;
    this.gaze = new GazeTracker();
  }

  /**
   * Track H addition (option A, 2026-11-19): a "looking away" judged by the
   * console room's own face check (head turned, `proctor.ts`), for a detector
   * with no warm-up baseline. It goes through the same GazeTracker, so the same
   * rules apply: a single glance never counts, only a sustained look or repeated
   * ones. Call it with exactly one face in view; `frame()` handles everything else.
   */
  judgedGaze(t: number, away: boolean): void {
    if (this.baseline) return; // the landmark path decides when a baseline exists
    for (const g of this.gaze.update(t, away)) this.gazeOut(g, t);
  }

  /** True when gaze is measured (a warm-up baseline exists). */
  get gazeMeasured(): boolean {
    return this.baseline !== null;
  }

  /** For the dev view. */
  get readout() {
    return {
      faces: this.latest.faces,
      smoothedCm: this.latest.smoothedCm,
      lookingAway: this.gaze.looking,
      openKinds: [...this.open.keys()],
    };
  }

  /** One analysed frame. */
  frame(r: TimedReading): void {
    const t = r.t;
    this.latest.faces = r.faces;

    this.handle(t, "face_missing", this.faceMissing.update(t, r.faces === 0), "no face in view");
    this.handle(t, "second_face", this.secondFace.update(t, r.faces >= 2), "two faces in view");

    if (r.faces === 1 && r.distanceCm !== null && Number.isFinite(r.distanceCm)) {
      this.distances.push({ t, cm: r.distanceCm });
      this.distances = this.distances.filter((d) => t - d.t <= DISTANCE_SMOOTH_S);
      const cm = median(this.distances.map((d) => d.cm));
      this.latest.smoothedCm = cm;
      // Hysteresis: start a little outside the band, end back inside it.
      const out = this.distance.isOpen
        ? cm < BAND_MIN_CM || cm > BAND_MAX_CM
        : cm < BAND_MIN_CM - BAND_ENTER_MARGIN_CM || cm > BAND_MAX_CM + BAND_ENTER_MARGIN_CM;
      if (out && !this.distance.isOpen) this.distanceDirection = cm < BAND_MIN_CM ? "near" : "far";
      this.handle(t, "distance_out_of_band", this.distance.update(t, out), `about ${Math.round(cm)} cm`, this.distanceDirection);
    }

    if (this.baseline && r.faces === 1 && r.yaw !== null && r.pitch !== null && r.eyeH !== null && r.eyeV !== null) {
      const away = isAway({ yaw: r.yaw, pitch: r.pitch, eyeH: r.eyeH, eyeV: r.eyeV }, this.baseline);
      for (const g of this.gaze.update(t, away)) this.gazeOut(g, t);
    }
    this.tick(t);
  }

  /** A browser signal. */
  browser(t: number, s: BrowserSignal): void {
    switch (s.type) {
      case "visibility": {
        this.visible = s.visible;
        if (!s.visible) {
          this.closeKind("tab_unfocused", t);
          this.unfocusedSince = null;
          const ev = this.openEvent("tab_hidden", t, t, "interview tab hidden");
          // A toast on a hidden tab is never seen: hold it until they are back.
          if (ev.reminder === "nudged") this.pendingTabReminder = { kind: "tab_hidden", message: reminderMessage("tab_hidden")!, t };
        } else {
          this.closeKind("tab_hidden", t);
          if (this.pendingTabReminder) {
            this.onReminder({ ...this.pendingTabReminder, t });
            this.pendingTabReminder = null;
          }
          if (!this.focused) this.unfocusedSince = t;
        }
        break;
      }
      case "focus":
        this.focused = s.focused;
        if (!s.focused && this.visible) this.unfocusedSince = t;
        if (s.focused) {
          this.unfocusedSince = null;
          this.closeKind("tab_unfocused", t);
        }
        break;
      case "fullscreen":
        if (s.on) {
          this.wasFullScreen = true;
          this.closeKind("left_full_screen", t);
        } else if (this.wasFullScreen) {
          this.openEvent("left_full_screen", t, t, "left full screen");
        }
        break;
      case "paste": {
        // One open event per burst: pastes within PASTE_MERGE_S of the last one
        // only count (repeats). It is written open now and once more when it
        // closes (tick, after PASTE_MERGE_S quiet), because the intake never
        // accepts a change to an open event except its closing copy.
        const open = this.open.get("paste");
        if (open && this.lastPasteAt !== null && t - this.lastPasteAt <= PASTE_MERGE_S) open.repeats += 1;
        else {
          this.closeKind("paste", this.lastPasteAt ?? t);
          this.openEvent("paste", t, t, "paste on the interview page");
        }
        this.lastPasteAt = t;
        break;
      }
      case "monitors":
        if (s.extended) this.openEvent("second_monitor", t, t, "second screen attached");
        else this.closeKind("second_monitor", t);
        break;
      case "share":
        if (s.state === "monitor") {
          this.closeKind("share_stopped", t);
          this.closeKind("share_not_whole_screen", t);
        } else if (s.state === "stopped") {
          this.closeKind("share_not_whole_screen", t);
          this.openEvent("share_stopped", t, t, "screen share stopped");
        } else {
          this.closeKind("share_stopped", t);
          this.openEvent("share_not_whole_screen", t, t, "a window or tab was shared, not the whole screen");
        }
        break;
    }
  }

  /** Time passing with no frame (timers): confirms "unfocused" and sends status. */
  tick(t: number): void {
    if (this.lastPasteAt !== null && this.open.has("paste") && t - this.lastPasteAt > PASTE_MERGE_S) this.closeKind("paste", this.lastPasteAt);
    if (this.unfocusedSince !== null && this.visible && !this.focused && !this.open.has("tab_unfocused")) {
      if (t - this.unfocusedSince >= UNFOCUSED_S) this.openEvent("tab_unfocused", this.unfocusedSince, t, "interview window not in focus");
    }
    if (t - this.lastStatusAt >= 1) {
      this.lastStatusAt = t;
      const cm = this.latest.smoothedCm;
      this.sink.status({
        kind: "integrity_status",
        distance_cm: cm === null ? null : Math.round(cm),
        distance_in_band: cm === null ? null : cm >= BAND_MIN_CM && cm <= BAND_MAX_CM,
        faces_in_view: this.latest.faces,
        page_visible: this.visible,
      });
    }
  }

  /** End of the call: close everything still open. */
  close(t: number): void {
    for (const g of this.gaze.flush()) this.gazeOut(g, t);
    this.handle(t, "face_missing", this.faceMissing.flush(), "");
    this.handle(t, "second_face", this.secondFace.flush(), "");
    this.handle(t, "distance_out_of_band", this.distance.flush(), "");
    if (this.lastPasteAt !== null) this.closeKind("paste", this.lastPasteAt);
    for (const kind of [...this.open.keys()]) this.closeKind(kind, t);
    this.sink.close();
  }

  // -- internals --------------------------------------------------------------------------

  private gazeOut(g: ReturnType<GazeTracker["update"]>[number], t: number) {
    const note = this.gazeOff ? " (gaze reminders off: accommodation)" : "";
    if (g.type === "sustained_start") this.openEvent("looking_away", g.start, t, `looked away from the screen${note}`);
    else if (g.type === "sustained_end") this.closeKind("looking_away", g.end);
    else {
      const ev = this.openEvent("looking_away", g.start, t, `looked away ${g.repeats} times within a minute${note}`);
      ev.repeats = g.repeats;
      ev.end_seconds = round(g.end);
      this.open.delete("looking_away");
      this.sink.record(ev);
    }
  }

  /**
   * Apply a Sustained change. `t` is now: a run that began at `change.start` is
   * confirmed, and its reminder decided, at `t`.
   */
  private handle(
    t: number,
    kind: IntegrityKind,
    change: { type: "start"; start: number } | { type: "end"; end: number } | null,
    detail: string,
    direction?: "near" | "far",
  ) {
    if (!change) return;
    if (change.type === "start") this.openEvent(kind, change.start, t, detail, direction);
    else this.closeKind(kind, change.end);
  }

  /**
   * Open an event that started at `start`, confirmed (and decided) at `decidedAt`.
   * The reminder decision is made at confirmation time, on the interview clock.
   */
  private openEvent(kind: IntegrityKind, start: number, decidedAt: number, detail: string, direction?: "near" | "far"): IntegrityEvent {
    // A state that is reported again while already open (the monitor poll, a
    // second "share stopped") is the same event, not a new one.
    const already = this.open.get(kind);
    if (already) return already;
    const decision = this.policy.decide(kind, decidedAt);
    this.decisions.push({ kind, t: decidedAt, decision });
    const ev: IntegrityEvent = {
      event_id: `${this.prefix}-${++this.seq}`,
      kind,
      source: "browser",
      start_seconds: round(start),
      end_seconds: null,
      repeats: 1,
      reminder: decision.reminder,
      reminder_index: decision.reminder_index,
      detail: detail.slice(0, 120),
      evidence: [],
    };
    this.open.set(kind, ev);
    this.sink.record(ev);
    if (decision.reminder === "nudged" && kind !== "tab_hidden") {
      const message = reminderMessage(kind, direction);
      if (message) this.onReminder({ kind, message, t: decidedAt });
    }
    return ev;
  }

  private closeKind(kind: IntegrityKind, end: number) {
    const ev = this.open.get(kind);
    if (!ev) return;
    this.open.delete(kind);
    ev.end_seconds = Math.max(ev.start_seconds, round(end));
    this.sink.record(ev);
  }
}
