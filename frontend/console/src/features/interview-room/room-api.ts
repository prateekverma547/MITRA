// The candidate room's calls to the backend. The candidate is never signed in:
// their meeting ID and password are the credential for every call here.

export type Round = "technical" | "hr";

export interface Credentials {
  meetingId: string;
  password: string;
}

/** POST /api/room/lookup (hiring/room.py). */
export interface RoomFacts {
  round: Round;
  status: "open" | "finished" | "closed";
  role_title: string;
  organisation: string | null;
  first_name: string | null;
  duration_minutes: number;
  recording: boolean;
  retention_days: number;
  bot_name: string;
}

/** POST /interviews/join (app/interviews.py, Track I). */
export interface JoinResult {
  interview_id: string;
  room_url: string;
  token: string;
  candidate_name: string | null;
  role_title: string;
  /** Lets this page post the MITRA round's integrity events (Track I's intake).
   * Held in memory only: never stored, never logged, never in a URL. */
  proctoring_token?: string | null;
}

export class RoomError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new RoomError(0, "We could not reach MITRA. Check your internet connection and try again.");
  }
  if (!response.ok) {
    let message = "Something went wrong. Try again in a moment.";
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") message = data.detail;
    } catch {
      // keep the generic message
    }
    throw new RoomError(response.status, message);
  }
  return (await response.json()) as T;
}

export function lookup(c: Credentials): Promise<RoomFacts> {
  return post<RoomFacts>("/api/room/lookup", { meeting_id: c.meetingId, password: c.password });
}

function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null; // never block a join over a greeting
  }
}

/** Records consent and, for a MITRA interview round, starts Mitra. */
export function join(c: Credentials): Promise<JoinResult> {
  return post<JoinResult>("/interviews/join", {
    meeting_id: c.meetingId,
    password: c.password,
    consent: true,
    timezone: browserTimezone(),
  });
}

// ---- remembered for a reload -------------------------------------------------------------------
// Session storage only: gone when the tab closes. Lets a candidate who reloads
// mid-interview land back in the lobby instead of retyping everything.

const KEY = "mitra.meet";

export interface Remembered extends Credentials {
  agreed: boolean;
}

export function remember(value: Remembered | null): void {
  try {
    if (value) sessionStorage.setItem(KEY, JSON.stringify(value));
    else sessionStorage.removeItem(KEY);
  } catch {
    // private window or blocked storage: nothing to remember
  }
}

export function recall(): Remembered | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (typeof v?.meetingId === "string" && typeof v?.password === "string") {
      return { meetingId: v.meetingId, password: v.password, agreed: Boolean(v.agreed) };
    }
  } catch {
    // fall through
  }
  return null;
}
