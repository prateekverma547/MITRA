/**
 * The ProctoringSink that talks to the backend (Session C's intake,
 * backend/proctoring/routes.py and intake.py).
 *
 * Events: queued and sent in batches about every 2 s (at most 100 a batch, the
 * intake's limit). Each event_id keeps only its latest copy while it waits, so
 * an event opened and closed between two batches is sent once, closed (the
 * intake stores a first copy as it is). The accommodation chosen at consent
 * goes with the first batch, even an empty one.
 *
 * What each answer means here:
 *   200: every per-event outcome is FINAL: stored, closed, duplicate, ignored
 *        and refused are never resent (refused and ignored are counted, with
 *        the reason, for the dev view). The answer's clock re-syncs the clock.
 *   401: the pass is not accepted: stop sending for good and tell the call
 *        screen, which shows a technical message, never an accusation.
 *   409: the interview is not running (not started yet, or ended beyond the
 *        intake's grace): hold the batch and try again later.
 *   413: the batch is too large: halve the batch size and resend.
 *   429, 5xx, no answer: back off (1 s doubling to 30 s, with jitter) and resend.
 *   other 4xx (422): the batch as a whole is malformed; resending cannot help,
 *        so it is dropped and counted.
 * When the page is hidden or closing, whatever is queued is sent at once with
 * fetch keepalive, so it survives the page.
 *
 * Status readings: the latest one, at most one a second (the route allows one a
 * second with a burst of 3); a reading refused for rate is simply skipped.
 */
import type { IntegrityAccommodation, IntegrityEvent, IntegrityStatus } from "../../../contracts";
import type { ApiResult, IntakeAnswer, ProctoringApi } from "./api";
import type { ServerClock } from "./serverClock";
import type { ProctoringSink } from "./sink";

export interface Timers {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
  now: () => number;
}

const browserTimers: Timers = {
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (h) => window.clearTimeout(h as number),
  now: () => performance.now(),
};

export const BATCH_MS = 2000;
export const MAX_BATCH = 100;
export const HOLD_MS = 5000;
export const BACKOFF_START_MS = 1000;
export const BACKOFF_MAX_MS = 30_000;
export const STATUS_MIN_MS = 1000;

export type SinkState = "running" | "holding" | "backing-off" | "stopped" | "closed";

export interface HttpSinkStats {
  batches: number;
  stored: number;
  closed: number;
  duplicates: number;
  ignored: { event_id: string; reason: string }[];
  refused: { event_id: string; reason: string }[];
  rejectedBatches: number;
  retries: number;
  holds: number;
  keepaliveFlushes: number;
  statusSent: number;
  statusSkipped: number;
  clockSyncs: number;
  lastHttpStatus: number | null;
}

export interface HttpSinkOptions {
  interviewId: string;
  /** The proctoring pass from the join response. Held in memory only. */
  pass: string;
  api: ProctoringApi;
  clock: ServerClock;
  timers?: Timers;
  random?: () => number;
  /** Called once if the pass is refused (401). */
  onStopped?: () => void;
}

export class HttpSink implements ProctoringSink {
  readonly stats: HttpSinkStats = {
    batches: 0,
    stored: 0,
    closed: 0,
    duplicates: 0,
    ignored: [],
    refused: [],
    rejectedBatches: 0,
    retries: 0,
    holds: 0,
    keepaliveFlushes: 0,
    statusSent: 0,
    statusSkipped: 0,
    clockSyncs: 0,
    lastHttpStatus: null,
  };
  private state: SinkState = "running";
  private queue = new Map<string, IntegrityEvent>();
  private inFlight: IntegrityEvent[] | null = null;
  private accommodation: IntegrityAccommodation | null = null;
  private accommodationSent = false;
  private timer: unknown = null;
  private backoff = 0;
  private batchSize = MAX_BATCH;
  private latestStatus: IntegrityStatus | null = null;
  private statusInFlight = false;
  private lastStatusAt = -Infinity;
  private statusTimer: unknown = null;
  private readonly t: Timers;
  private readonly random: () => number;
  // The pass is a private field: never serialised, never logged.
  readonly #pass: string;

  private readonly opts: Omit<HttpSinkOptions, "pass">;

  constructor(options: HttpSinkOptions) {
    // The pass is kept only in the private field, never on an enumerable
    // property, so a serialised or logged sink cannot carry it.
    const { pass, ...rest } = options;
    this.opts = rest;
    this.#pass = pass;
    this.t = rest.timers ?? browserTimers;
    this.random = rest.random ?? Math.random;
  }

  get current(): SinkState {
    return this.state;
  }

  /** Events waiting to be sent (not counting a batch in flight). */
  get pending(): number {
    return this.queue.size;
  }

  start(info: { accommodation: IntegrityAccommodation }): void {
    this.accommodation = { ...info.accommodation };
    this.schedule(0);
  }

  record(event: IntegrityEvent): void {
    if (this.state === "stopped") return;
    // The latest copy of an event replaces a waiting one, and moves to the back.
    this.queue.delete(event.event_id);
    this.queue.set(event.event_id, structuredClone(event));
    this.schedule(BATCH_MS);
  }

  status(reading: IntegrityStatus): void {
    if (this.state === "stopped" || this.state === "closed") return;
    this.latestStatus = { ...reading };
    this.sendStatusSoon();
  }

  /** End of the call: send everything left, surviving the page. */
  close(): void {
    if (this.state === "closed") return;
    void this.flush({ keepalive: true });
    if (this.state !== "stopped") this.state = "closed";
  }

  /** Send whatever is queued now (the page is being hidden or closed). */
  flushNow(): void {
    this.stats.keepaliveFlushes++;
    void this.flush({ keepalive: true });
  }

  /** Wait until nothing is queued or in flight, or the timeout passes (the End button). */
  async drain(timeoutMs = 3000): Promise<void> {
    const until = this.t.now() + timeoutMs;
    while ((this.queue.size || this.inFlight || !this.accommodationSent) && this.state !== "stopped" && this.t.now() < until) {
      if (!this.inFlight) await this.flush({});
      else await new Promise((r) => this.t.setTimeout(() => r(null), 50));
    }
  }

  // -- internals ------------------------------------------------------------------------------

  private schedule(ms: number) {
    if (this.state === "stopped" || this.timer !== null) return;
    this.timer = this.t.setTimeout(() => {
      this.timer = null;
      void this.flush({});
    }, ms);
  }

  private async flush({ keepalive = false }: { keepalive?: boolean }): Promise<void> {
    if (this.state === "stopped" || this.inFlight) return;
    const needAccommodation = !this.accommodationSent && this.accommodation !== null;
    if (this.queue.size === 0 && !needAccommodation) return;
    const batch = [...this.queue.values()].slice(0, this.batchSize);
    for (const e of batch) this.queue.delete(e.event_id);
    this.inFlight = batch;
    this.stats.batches++;
    const res = await this.opts.api.postEvents(
      this.opts.interviewId,
      this.#pass,
      { events: batch, ...(needAccommodation ? { accommodation: this.accommodation! } : {}) },
      { keepalive },
    );
    this.inFlight = null;
    this.handle(res, batch, needAccommodation);
  }

  private handle(res: ApiResult<IntakeAnswer>, batch: IntegrityEvent[], hadAccommodation: boolean) {
    this.stats.lastHttpStatus = res.status;
    if (res.status === 200 && res.body) {
      const b = res.body;
      this.stats.stored += b.stored;
      this.stats.closed += b.closed;
      this.stats.duplicates += b.duplicates;
      this.stats.ignored.push(...b.ignored);
      this.stats.refused.push(...b.refused);
      if (hadAccommodation) this.accommodationSent = true;
      if (this.opts.clock.sync(b.clock, res.sentAt, res.receivedAt)) this.stats.clockSyncs++;
      this.backoff = 0;
      if (this.state === "holding" || this.state === "backing-off") this.state = "running";
      if (this.queue.size) this.schedule(0);
      return;
    }
    if (res.status === 401) {
      this.state = "stopped";
      this.queue.clear();
      this.opts.onStopped?.();
      return;
    }
    if (res.status >= 400 && res.status < 500 && ![409, 413, 429].includes(res.status)) {
      // Malformed as a whole: resending the same batch cannot help.
      this.stats.rejectedBatches++;
      if (this.queue.size) this.schedule(BATCH_MS);
      return;
    }
    // Retry: the batch goes back in front of anything newer for the same events.
    this.requeue(batch);
    if (res.status === 409) {
      this.stats.holds++;
      if (this.state !== "closed") this.state = "holding";
      this.schedule(HOLD_MS);
    } else if (res.status === 413) {
      this.batchSize = Math.max(1, Math.floor(batch.length / 2));
      this.schedule(0);
    } else {
      this.stats.retries++;
      if (this.state !== "closed") this.state = "backing-off";
      const base = Math.min(BACKOFF_MAX_MS, BACKOFF_START_MS * 2 ** this.backoff);
      this.backoff++;
      this.schedule(base * (0.75 + this.random() * 0.5));
    }
  }

  private requeue(batch: IntegrityEvent[]) {
    const newer = this.queue;
    this.queue = new Map();
    for (const e of batch) if (!newer.has(e.event_id)) this.queue.set(e.event_id, e);
    for (const [id, e] of newer) this.queue.set(id, e);
  }

  private sendStatusSoon() {
    if (this.statusTimer !== null || this.statusInFlight) return;
    const wait = Math.max(0, this.lastStatusAt + STATUS_MIN_MS - this.t.now());
    this.statusTimer = this.t.setTimeout(() => {
      this.statusTimer = null;
      void this.sendStatus();
    }, wait);
  }

  private async sendStatus() {
    const reading = this.latestStatus;
    if (!reading || this.state === "stopped" || this.state === "closed") return;
    this.latestStatus = null;
    this.statusInFlight = true;
    this.lastStatusAt = this.t.now();
    const res = await this.opts.api.postStatus(this.opts.interviewId, this.#pass, reading);
    this.statusInFlight = false;
    if (res.status === 200 && res.body) {
      this.stats.statusSent++;
      if (this.opts.clock.sync(res.body.clock, res.sentAt, res.receivedAt)) this.stats.clockSyncs++;
    } else if (res.status === 401) {
      this.state = "stopped";
      this.queue.clear();
      this.opts.onStopped?.();
    } else this.stats.statusSkipped++;
    if (this.latestStatus) this.sendStatusSoon();
  }
}

/**
 * The first clock reading, when the call screen opens. Retries a missing or
 * busy answer; 409 (not running yet) is retried too; 401 gives up.
 */
export async function syncClock(
  api: ProctoringApi,
  interviewId: string,
  pass: string,
  clock: ServerClock,
  opts: { attempts?: number; wait?: (ms: number) => Promise<void> } = {},
): Promise<"synced" | "unauthorised" | "failed"> {
  const wait = opts.wait ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  const attempts = opts.attempts ?? 6;
  for (let i = 0; i < attempts; i++) {
    const res = await api.getClock(interviewId, pass);
    if (res.status === 200 && res.body && clock.sync(res.body.clock, res.sentAt, res.receivedAt)) return "synced";
    if (res.status === 401) return "unauthorised";
    await wait(Math.min(BACKOFF_MAX_MS, 1000 * 2 ** i));
  }
  return "failed";
}

/** Both sinks at once: the in-memory one feeds the dev view, the HTTP one the backend. */
export class TeeSink implements ProctoringSink {
  constructor(private readonly sinks: ProctoringSink[]) {}
  start(info: { accommodation: IntegrityAccommodation }) {
    for (const s of this.sinks) s.start(info);
  }
  record(e: IntegrityEvent) {
    for (const s of this.sinks) s.record(e);
  }
  status(r: IntegrityStatus) {
    for (const s of this.sinks) s.status(r);
  }
  close() {
    for (const s of this.sinks) s.close();
  }
}
