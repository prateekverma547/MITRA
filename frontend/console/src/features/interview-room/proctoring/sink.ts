import type { IntegrityAccommodation, IntegrityEvent, IntegrityKind, IntegrityStatus } from "../../../contracts";

/**
 * Where proctoring events go. One interface, so the in-memory sink used now
 * and the backend intake Session C is building are interchangeable: the
 * engine never knows which it is talking to.
 *
 * `record` is an upsert keyed by event_id. An event is recorded when it is
 * confirmed (end_seconds null) and recorded again when it ends, with the same
 * id, so a live viewer sees it as it happens and the stored copy ends complete.
 *
 * Events and status readings are the ONLY things that leave the detection
 * pipeline. No frame, image or landmark ever reaches a sink.
 */
export interface ProctoringSink {
  start(info: { accommodation: IntegrityAccommodation }): void;
  record(event: IntegrityEvent): void;
  /** The browser's current reading for the live view's tiles, about once a second. Not an event. */
  status(reading: IntegrityStatus): void;
  close(): void;
}

export interface SinkSnapshot {
  accommodation: IntegrityAccommodation | null;
  events: IntegrityEvent[];
  status: IntegrityStatus | null;
  closed: boolean;
  /** How many times record() was called (opens plus closes). */
  writes: number;
  /** Every record() call in order, as written (what an intake would receive). */
  history: IntegrityEvent[];
}

/** Keeps everything in memory. Used now, and by the dev view to show the live event list. */
export class InMemorySink implements ProctoringSink {
  private accommodation: IntegrityAccommodation | null = null;
  private byId = new Map<string, IntegrityEvent>();
  private order: string[] = [];
  private latest: IntegrityStatus | null = null;
  private closed = false;
  private writes = 0;
  private history: IntegrityEvent[] = [];
  private listeners = new Set<() => void>();

  start(info: { accommodation: IntegrityAccommodation }): void {
    this.accommodation = { ...info.accommodation };
    this.emit();
  }

  record(event: IntegrityEvent): void {
    if (!this.byId.has(event.event_id)) this.order.push(event.event_id);
    this.byId.set(event.event_id, structuredClone(event));
    this.history.push(structuredClone(event));
    this.writes++;
    this.emit();
  }

  status(reading: IntegrityStatus): void {
    this.latest = { ...reading };
    this.emit();
  }

  close(): void {
    this.closed = true;
    this.emit();
  }

  snapshot(): SinkSnapshot {
    return {
      accommodation: this.accommodation,
      events: this.order.map((id) => this.byId.get(id)!),
      status: this.latest,
      closed: this.closed,
      writes: this.writes,
      history: this.history,
    };
  }

  /** Same counting as the backend's IntegritySummary.from_events. */
  summary(): { events_by_kind: Partial<Record<IntegrityKind, number>>; reminders_by_kind: Partial<Record<IntegrityKind, number>> } {
    const events_by_kind: Partial<Record<IntegrityKind, number>> = {};
    const reminders_by_kind: Partial<Record<IntegrityKind, number>> = {};
    for (const e of this.snapshot().events) {
      events_by_kind[e.kind] = (events_by_kind[e.kind] ?? 0) + 1;
      if (e.reminder === "nudged") reminders_by_kind[e.kind] = (reminders_by_kind[e.kind] ?? 0) + 1;
    }
    return { events_by_kind, reminders_by_kind };
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit() {
    for (const l of this.listeners) l();
  }
}
