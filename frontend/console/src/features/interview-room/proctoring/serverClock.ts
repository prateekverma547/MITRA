/**
 * The server is the clock. Interview time ("interview_seconds") comes from the
 * backend (live/clock.py interview_time_now): exact once the bot has stored its
 * transcript zero, an estimate before that. Every event is stamped in it, so
 * integrity events, the transcript and the recording share one zero.
 *
 * How a reading is turned into a clock: the server computed `interview_seconds`
 * somewhere between sending the request (t0) and receiving the answer (t1), so
 * it is taken to belong to their midpoint, and the error is at most half the
 * round trip. Later samples replace the current one when they are better:
 *   - an exact sample always replaces an estimate (the bot stored its zero);
 *   - an estimate never replaces an exact sample;
 *   - otherwise a sample with a round trip no more than 1.5 times the best one
 *     seen replaces it, so a slow, uncertain answer does not move the clock,
 *     and any sample replaces one older than 60 s (drift).
 *
 * Survives a page reload: the anchor (when interview time zero fell, on this
 * device's wall clock) is kept in sessionStorage under the interview id. No
 * secret is in it. After a reload the clock reads from the anchor until the
 * next server sample.
 */

export interface ServerClockReading {
  interview_seconds: number | null;
  estimate: boolean;
  basis: string;
  server_time: string;
}

export interface ClockState {
  synced: boolean;
  estimate: boolean;
  basis: string | null;
  /** Round trip of the sample in use, milliseconds (null when restored or unsynced). */
  rttMs: number | null;
  samples: number;
  /** "server" after a sync, "restored" after a reload until the next sync. */
  source: "none" | "server" | "restored";
}

export interface ClockDeps {
  /** Monotonic milliseconds (performance.now). */
  perf: () => number;
  /** Wall-clock milliseconds (Date.now). */
  wall: () => number;
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
}

const browserDeps = (): ClockDeps => ({
  perf: () => performance.now(),
  wall: () => Date.now(),
  storage: typeof sessionStorage === "undefined" ? null : sessionStorage,
});

export const CLOCK_KEY_PREFIX = "mitra.clock.";
const STALE_MS = 60_000;
const RTT_FACTOR = 1.5;

export class ServerClock {
  /** perf time at which interview time was zero, by the sample in use. */
  private zeroPerf: number | null = null;
  private best: { rtt: number; estimate: boolean; at: number; basis: string } | null = null;
  private samples = 0;
  private source: ClockState["source"] = "none";
  private readonly deps: ClockDeps;

  constructor(
    readonly interviewId: string,
    deps: Partial<ClockDeps> = {},
  ) {
    this.deps = { ...browserDeps(), ...deps };
    this.restore();
  }

  /** Interview seconds now, or null before any sync or restore. */
  now(): number | null {
    return this.at(this.deps.perf());
  }

  /** Interview seconds at a performance.now() time (a camera frame's capture time). */
  at(perfMs: number): number | null {
    if (this.zeroPerf === null) return null;
    return Math.max(0, (perfMs - this.zeroPerf) / 1000);
  }

  get state(): ClockState {
    return {
      synced: this.zeroPerf !== null,
      estimate: this.best?.estimate ?? true,
      basis: this.best?.basis ?? null,
      rttMs: this.source === "server" ? (this.best?.rtt ?? null) : null,
      samples: this.samples,
      source: this.source,
    };
  }

  /**
   * Offer a reading from a server response. `sentAt` and `receivedAt` are the
   * performance.now() times of the request and the answer. Returns true when
   * it became the clock.
   */
  sync(reading: ServerClockReading | null | undefined, sentAt: number, receivedAt: number): boolean {
    if (!reading || reading.interview_seconds === null || !Number.isFinite(reading.interview_seconds)) return false;
    const rtt = Math.max(0, receivedAt - sentAt);
    const mid = sentAt + rtt / 2;
    const b = this.best;
    const take =
      b === null ||
      this.source !== "server" ||
      (b.estimate && !reading.estimate) ||
      (b.estimate === reading.estimate && (rtt <= b.rtt * RTT_FACTOR || mid - b.at > STALE_MS));
    this.samples++;
    if (!take) return false;
    this.best = { rtt, estimate: reading.estimate, at: mid, basis: reading.basis };
    this.zeroPerf = mid - reading.interview_seconds * 1000;
    this.source = "server";
    this.persist();
    return true;
  }

  /** Forget the stored anchor (the interview ended). */
  clear() {
    this.deps.storage?.removeItem(CLOCK_KEY_PREFIX + this.interviewId);
  }

  private persist() {
    if (this.zeroPerf === null || !this.deps.storage) return;
    // Wall time of interview zero = wall now - interview seconds now.
    const zeroWall = this.deps.wall() - (this.deps.perf() - this.zeroPerf);
    this.deps.storage.setItem(
      CLOCK_KEY_PREFIX + this.interviewId,
      JSON.stringify({ zeroWall, estimate: this.best?.estimate ?? true, basis: this.best?.basis ?? null }),
    );
  }

  private restore() {
    const raw = this.deps.storage?.getItem(CLOCK_KEY_PREFIX + this.interviewId);
    if (!raw) return;
    try {
      const { zeroWall, estimate, basis } = JSON.parse(raw) as { zeroWall: number; estimate: boolean; basis: string | null };
      if (!Number.isFinite(zeroWall)) return;
      this.zeroPerf = this.deps.perf() - (this.deps.wall() - zeroWall);
      // A restored clock is never "better" than a server sample: the next one replaces it.
      this.best = { rtt: Number.POSITIVE_INFINITY, estimate: !!estimate, at: this.deps.perf(), basis: basis ?? "restored" };
      this.source = "restored";
    } catch {
      /* a broken entry is ignored */
    }
  }
}
