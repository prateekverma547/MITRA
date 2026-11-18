import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ROLE_LABELS, ROLES, type Role } from "../../contracts";
import { useSession } from "../../session";
import { Brand } from "../../shell/Brand";
import { Footer } from "../../shell/Footer";

function safeNext(next: string | null): string {
  // Only ever go somewhere inside the console.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/home";
}

export function SignIn() {
  const { signIn } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const door = params.get("as");
  const doorRole = ROLES.includes(door as Role) ? (door as Role) : null;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
      navigate(safeNext(params.get("next")), { replace: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="public">
      <header className="public-top">
        <Link to="/" className="brand-link" aria-label="MITRA home">
          <Brand />
        </Link>
      </header>

      <main className="signin-page">
        <form className="signin" onSubmit={submit} noValidate>
          <h1>{doorRole ? `Sign in as ${ROLE_LABELS[doorRole]}` : "Sign in"}</h1>
          <p className="muted">Use your work email. Your account decides what you can see.</p>

          <div className="field">
            <label htmlFor="email">Work email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy || !email || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <Link to="/" className="quiet-link">
            Choose a different way in
          </Link>
        </form>
      </main>

      <Footer />
    </div>
  );
}
