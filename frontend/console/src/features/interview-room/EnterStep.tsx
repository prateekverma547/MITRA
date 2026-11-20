import { KeyRound, Loader2 } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Brand } from "../../shell/Brand";
import { Footer } from "../../shell/Footer";
import { lookup, RoomError, type Credentials, type RoomFacts } from "./room-api";

/** The light frame every step before the call shares. */
export function PreJoinFrame({ step, children }: { step?: string; children: ReactNode }) {
  return (
    <div className="meet meet-light">
      <header className="meet-top">
        <Brand size={26} />
        {step && <span className="meet-step">{step}</span>}
      </header>
      <main className="meet-main">{children}</main>
      <Footer />
    </div>
  );
}

/** 428193756 → 428-193-756, as the candidate types. */
function formatMeetingId(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 9);
  return digits.replace(/(\d{3})(?=\d)/g, "$1-");
}

const CLOSED: Record<"finished" | "closed", { title: string; body: string }> = {
  finished: {
    title: "This interview has already taken place.",
    body: "There is nothing more to do. The company will contact you about the next step.",
  },
  closed: {
    title: "This invitation is no longer open.",
    body: "It expired or was cancelled. Reply to the message you were sent and ask for a new one.",
  },
};

export function EnterStep({ initial, onFound }: { initial: Credentials; onFound: (c: Credentials, f: RoomFacts) => void }) {
  const [meetingId, setMeetingId] = useState(formatMeetingId(initial.meetingId));
  const [password, setPassword] = useState(initial.password);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ title: string; body: string } | null>(null);

  const ready = meetingId.replace(/\D/g, "").length === 9 && password.trim() !== "";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setProblem(null);
    const c = { meetingId, password: password.trim() };
    try {
      const facts = await lookup(c);
      if (facts.status === "open") onFound(c, facts);
      else setProblem(CLOSED[facts.status]);
    } catch (err) {
      const status = err instanceof RoomError ? err.status : 0;
      setProblem(
        status === 401
          ? { title: "That meeting ID and password do not match.", body: "Check both against your invitation. The password is not case-sensitive." }
          : { title: "We could not check your details.", body: (err as Error).message },
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <PreJoinFrame step="Step 1 of 3">
      <section className="meet-enter" aria-labelledby="enter-title">
        <div className="meet-enter-copy">
          <h1 id="enter-title" className="meet-title">
            Join your interview
          </h1>
          <p className="meet-lead">
            Enter the meeting ID and password from your invitation. Use a laptop or desktop with Chrome or Edge, somewhere quiet
            where you can talk.
          </p>
        </div>

        <form className="meet-card meet-enter-form" onSubmit={submit} noValidate>
          <label className="meet-field">
            <span>Meeting ID</span>
            <input
              inputMode="numeric"
              autoComplete="off"
              placeholder="000-000-000"
              value={meetingId}
              onChange={(e) => setMeetingId(formatMeetingId(e.target.value))}
              aria-invalid={problem ? true : undefined}
              className="meet-input mono"
              autoFocus={!meetingId}
            />
          </label>
          <label className="meet-field">
            <span>Password</span>
            <input
              type="password"
              autoComplete="off"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={problem ? true : undefined}
              className="meet-input"
              autoFocus={Boolean(meetingId)}
            />
          </label>

          {problem && (
            <div className="meet-alert" role="alert">
              <strong>{problem.title}</strong>
              <span>{problem.body}</span>
            </div>
          )}

          <button type="submit" className="meet-primary" disabled={!ready || busy}>
            {busy ? <Loader2 size={18} className="meet-spin" /> : <KeyRound size={18} />}
            {busy ? "Checking" : "Continue"}
          </button>
        </form>
      </section>
    </PreJoinFrame>
  );
}
