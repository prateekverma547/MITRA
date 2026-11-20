// The console's only way to reach the backend. Same origin, so the session
// cookie travels with every call; nothing here ever handles the token itself.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// In-flight requests, for the progress bar at the top of the page.
let inFlight = 0;
const listeners = new Set<(busy: boolean) => void>();
function track(delta: number) {
  inFlight += delta;
  listeners.forEach((fn) => fn(inFlight > 0));
}
export function onBusyChange(fn: (busy: boolean) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  track(1);
  try {
    return await send<T>(method, path, body);
  } finally {
    track(-1);
  }
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = body instanceof FormData;
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    // FormData sets its own multipart boundary header; JSON needs ours.
    headers: body === undefined || isForm ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `Something went wrong (${response.status}).`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") message = data.detail;
    } catch {
      // Not JSON: keep the generic message.
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
  postForm: <T>(path: string, form: FormData) => request<T>("POST", path, form),
};
