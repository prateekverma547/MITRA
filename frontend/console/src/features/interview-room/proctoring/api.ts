// The proctoring part of Track I's frontend/candidate-app/src/api/backend.ts (lines 223-312),
// unchanged but for the import paths: the intake, status and clock routes, sent
// with the proctoring token from the join as a bearer header, never in a URL.
import type { IntegrityAccommodation, IntegrityEvent, IntegrityStatus } from "../../../contracts";
import type { ServerClockReading } from "./serverClock";


// -- proctoring routes (backend/proctoring/routes.py) -------------------------------------

export interface ApiResult<T> {
  /** HTTP status, or 0 when the request never got an answer (network error). */
  status: number;
  body: T | null;
  /** performance.now() when sent and when answered, for the clock's round trip. */
  sentAt: number;
  receivedAt: number;
}

export interface IntakeBody {
  events: IntegrityEvent[];
  accommodation?: IntegrityAccommodation;
}

export interface IntakeAnswer {
  stored: number;
  closed: number;
  duplicates: number;
  ignored: { event_id: string; reason: string }[];
  refused: { event_id: string; reason: string }[];
  accommodation_set: boolean;
  clock: ServerClockReading;
  detail?: string;
}

export interface StatusAnswer {
  accepted: boolean;
  version: number;
  clock: ServerClockReading;
  detail?: string;
}

export interface ClockAnswer {
  clock: ServerClockReading;
  detail?: string;
}

export interface ProctoringApi {
  postEvents(interviewId: string, pass: string, body: IntakeBody, opts?: { keepalive?: boolean }): Promise<ApiResult<IntakeAnswer>>;
  postStatus(interviewId: string, pass: string, body: IntegrityStatus): Promise<ApiResult<StatusAnswer>>;
  getClock(interviewId: string, pass: string): Promise<ApiResult<ClockAnswer>>;
}

/** The real routes, over fetch. `fetchImpl` and `now` are injectable for tests. */
/**
 * A status reading gives up after this long (D-30: with no timeout, the request in
 * flight at a network cut hung for up to 68 s, and as nothing else is sent while one
 * is in flight, readings resumed up to 23 s after the network came back). A reading
 * is only the latest state, so a lost one costs nothing: the next one replaces it.
 */
export const STATUS_TIMEOUT_MS = 3000;

export function proctoringApi(fetchImpl: typeof fetch = (...a) => fetch(...a), now: () => number = () => performance.now()): ProctoringApi {
  async function call<T>(path: string, pass: string, init: RequestInit, timeoutMs?: number): Promise<ApiResult<T>> {
    const sentAt = now();
    const abort = timeoutMs ? new AbortController() : null;
    const timer = abort ? setTimeout(() => abort.abort(), timeoutMs) : null;
    try {
      const response = await fetchImpl(path, {
        ...init,
        ...(abort ? { signal: abort.signal } : {}),
        headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), Authorization: `Bearer ${pass}` },
        credentials: "omit",
        cache: "no-store",
      });
      const receivedAt = now();
      let body: T | null = null;
      try {
        body = (await response.json()) as T;
      } catch {
        /* not JSON */
      }
      return { status: response.status, body, sentAt, receivedAt };
    } catch {
      return { status: 0, body: null, sentAt, receivedAt: now() };
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
  const base = (id: string) => `/api/proctoring/interviews/${encodeURIComponent(id)}`;
  return {
    postEvents: (id, pass, body, opts) =>
      call<IntakeAnswer>(`${base(id)}/events`, pass, { method: "POST", body: JSON.stringify(body), keepalive: !!opts?.keepalive }),
    postStatus: (id, pass, body) => call<StatusAnswer>(`${base(id)}/status`, pass, { method: "POST", body: JSON.stringify(body) }, STATUS_TIMEOUT_MS),
    getClock: (id, pass) => call<ClockAnswer>(`${base(id)}/clock`, pass, { method: "GET" }),
  };
}
