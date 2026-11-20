import { median } from "./geometry";
import {
  GAZE_EYE_H,
  GAZE_EYE_V,
  GAZE_GLANCE_MIN_S,
  GAZE_HEAD_PITCH_DEG,
  GAZE_HEAD_YAW_DEG,
  GAZE_REPEAT_COUNT,
  GAZE_REPEAT_WINDOW_S,
  GAZE_RETURN_GRACE_S,
  GAZE_SUSTAINED_S,
  WARMUP_MIN_FACE_SHARE,
} from "./thresholds";

/** One gaze sample: head pose in degrees and iris offset in the eye. */
export interface GazeSample {
  yaw: number;
  pitch: number;
  eyeH: number;
  eyeV: number;
}

/** Where this person's head and eyes sit when looking at the screen (task 13). */
export interface GazeBaseline extends GazeSample {
  samples: number;
}

/**
 * The warm-up baseline: the median of each measure over the frames that showed
 * exactly one face. Medians, so a few glances away during the warm-up do not
 * move it. Null when too few frames showed a face to trust it.
 */
export function buildBaseline(samples: GazeSample[], framesSeen: number): GazeBaseline | null {
  if (framesSeen === 0 || samples.length / framesSeen < WARMUP_MIN_FACE_SHARE || samples.length < 20) return null;
  return {
    yaw: median(samples.map((s) => s.yaw)),
    pitch: median(samples.map((s) => s.pitch)),
    eyeH: median(samples.map((s) => s.eyeH)),
    eyeV: median(samples.map((s) => s.eyeV)),
    samples: samples.length,
  };
}

/** Is this frame looking away from the screen, compared with the baseline? */
export function isAway(s: GazeSample, b: GazeSample): boolean {
  return (
    Math.abs(s.yaw - b.yaw) > GAZE_HEAD_YAW_DEG ||
    Math.abs(s.pitch - b.pitch) > GAZE_HEAD_PITCH_DEG ||
    Math.abs(s.eyeH - b.eyeH) > GAZE_EYE_H ||
    Math.abs(s.eyeV - b.eyeV) > GAZE_EYE_V
  );
}

export type GazeOutput =
  /** A single look away has passed GAZE_SUSTAINED_S: open an event starting at `start`. */
  | { type: "sustained_start"; start: number }
  /** That sustained look ended. */
  | { type: "sustained_end"; end: number }
  /** Enough shorter looks in the window: one event covering them all, already ended. */
  | { type: "repeated"; start: number; end: number; repeats: number };

/**
 * Turns per-frame away/not-away into looks ("episodes") and decides which
 * count. Only two things count, never a single glance:
 *
 * - sustained: one look of at least GAZE_SUSTAINED_S;
 * - repeated: GAZE_REPEAT_COUNT looks of at least GAZE_GLANCE_MIN_S inside
 *   GAZE_REPEAT_WINDOW_S. The looks that made it are then cleared, so the next
 *   one alone does not fire it again.
 *
 * A look ends only after GAZE_RETURN_GRACE_S back on the screen, so a blink or
 * a landmark wobble does not split one look into two. Frames without a face
 * are skipped here (face_missing covers them).
 */
export class GazeTracker {
  private awayStart: number | null = null;
  private lastAway = 0;
  private sustainedOpen = false;
  private glances: { start: number; end: number }[] = [];

  update(t: number, away: boolean): GazeOutput[] {
    const out: GazeOutput[] = [];
    if (away) {
      if (this.awayStart === null) this.awayStart = t;
      this.lastAway = t;
      if (!this.sustainedOpen && t - this.awayStart >= GAZE_SUSTAINED_S) {
        this.sustainedOpen = true;
        out.push({ type: "sustained_start", start: this.awayStart });
      }
    } else if (this.awayStart !== null && t - this.lastAway >= GAZE_RETURN_GRACE_S) {
      out.push(...this.endLook());
    }
    return out;
  }

  /** End any look in progress (the call ended). */
  flush(): GazeOutput[] {
    return this.awayStart === null ? [] : this.endLook();
  }

  /** True while the candidate is in a look away (for the dev view). */
  get looking(): boolean {
    return this.awayStart !== null;
  }

  private endLook(): GazeOutput[] {
    const out: GazeOutput[] = [];
    const start = this.awayStart!;
    const end = this.lastAway;
    this.awayStart = null;
    if (this.sustainedOpen) {
      this.sustainedOpen = false;
      out.push({ type: "sustained_end", end });
      return out;
    }
    if (end - start >= GAZE_GLANCE_MIN_S) {
      this.glances.push({ start, end });
      this.glances = this.glances.filter((g) => end - g.start <= GAZE_REPEAT_WINDOW_S);
      if (this.glances.length >= GAZE_REPEAT_COUNT) {
        out.push({ type: "repeated", start: this.glances[0].start, end, repeats: this.glances.length });
        this.glances = [];
      }
    }
    return out;
  }
}

/**
 * A condition that must hold for `sustainS` before it counts, and ends after
 * `graceS` of not holding. Used for distance and face count.
 */
export class Sustained {
  private since: number | null = null;
  private lastTrue = 0;
  private open = false;

  constructor(
    private readonly sustainS: number,
    private readonly graceS: number,
  ) {}

  /** Returns "start" (with when it began), "end" (with when it last held), or null. */
  update(t: number, holds: boolean): { type: "start"; start: number } | { type: "end"; end: number } | null {
    if (holds) {
      if (this.since === null) this.since = t;
      this.lastTrue = t;
      if (!this.open && t - this.since >= this.sustainS) {
        this.open = true;
        return { type: "start", start: this.since };
      }
      return null;
    }
    if (this.since !== null && t - this.lastTrue >= this.graceS) {
      const wasOpen = this.open;
      this.since = null;
      this.open = false;
      if (wasOpen) return { type: "end", end: this.lastTrue };
    }
    return null;
  }

  get isOpen(): boolean {
    return this.open;
  }

  /** Close an open run (the call ended). */
  flush(): { type: "end"; end: number } | null {
    if (!this.open) return null;
    this.open = false;
    this.since = null;
    return { type: "end", end: this.lastTrue };
  }
}
