import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ROLE_LABELS, ROLES, type Role } from "../../contracts";
import { useSession } from "../../session";

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
    <div className="center-screen">
      <form className="card signin" onSubmit={submit} noValidate>
        <h1>{doorRole ? `Sign in: ${ROLE_LABELS[doorRole]}` : "Sign in"}</h1>
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
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy || !email || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted small">
          Your account decides what you can see. <Link to="/">Back</Link>
        </p>
      </form>
    </div>
  );
}
