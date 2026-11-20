import { useEffect, useState, type FormEvent } from "react";
import { LogIn, UserRound } from "lucide-react";
import { api } from "../../api";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ROLE_LABELS, ROLES, type Role } from "../../contracts";
import { useSession } from "../../session";
import { Brand } from "../../shell/Brand";
import { Footer } from "../../shell/Footer";
import { ThemeToggle } from "../../shell/theme";
import { BackButton } from "../../shell/ui/BackButton";
import { Button } from "../../shell/ui/Button";

interface DemoAccount {
  email: string;
  name: string;
  role: Role;
  bu_name: string | null;
}

/** Demo accounts, offered by the backend on a developer's machine only (never deployed). */
function useDemoAccounts() {
  const [demo, setDemo] = useState<{ accounts: DemoAccount[]; password: string | null }>({ accounts: [], password: null });
  useEffect(() => {
    api
      .get<{ accounts: DemoAccount[]; password: string | null }>("/auth/demo")
      .then(setDemo)
      .catch(() => undefined);
  }, []);
  return demo;
}

function safeNext(next: string | null): string {
  // Only ever go somewhere inside the console.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/home";
}

/** The names on the front door's three ways in (Welcome.tsx). */
const DOOR_LABELS: Record<Role, string> = { hr: "HR", bu_head: "BU Head", org_admin: "Organisation admin" };

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

  const demo = useDemoAccounts();
  const [demoBusy, setDemoBusy] = useState<string | null>(null);

  // Fill the form with the demo account for the way in that was chosen.
  useEffect(() => {
    if (!demo.password || email) return;
    const match = demo.accounts.find((a) => a.role === doorRole) ?? (doorRole ? undefined : demo.accounts[0]);
    if (match) {
      setEmail(match.email);
      setPassword(demo.password);
    }
  }, [demo, doorRole]); // eslint-disable-line react-hooks/exhaustive-deps

  async function go(emailToUse: string, passwordToUse: string) {
    setError(null);
    try {
      await signIn(emailToUse, passwordToUse);
      navigate(safeNext(params.get("next")), { replace: true, viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      setDemoBusy(null);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
      navigate(safeNext(params.get("next")), { replace: true, viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="public">
      <header className="public-top public-top-row">
        <Link to="/" className="brand-link" aria-label="MITRA home" viewTransition>
          <Brand />
        </Link>
        <ThemeToggle />
      </header>

      <main className="signin-page">
        <form className="signin" onSubmit={submit} noValidate>
          <BackButton fallback="/" label="Choose a different way in" />
          <h1>{doorRole ? `Sign in as ${DOOR_LABELS[doorRole]}` : "Sign in"}</h1>
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
          <Button
            type="submit"
            variant="primary"
            className="btn-block"
            icon={<LogIn size={17} />}
            loading={busy}
            disabled={!email || !password}
          >
            {busy ? "Signing in" : "Sign in"}
          </Button>

          {demo.accounts.length > 0 && demo.password && (
            <section className="demo-accounts" aria-labelledby="demo-title">
              <p id="demo-title" className="demo-title">Quick sign-in</p>
              <p className="demo-note">Accounts set up on this machine. One click signs you in.</p>
              <ul>
                {demo.accounts.map((a) => (
                  <li key={a.email}>
                    <button
                      type="button"
                      className={a.role === doorRole ? "demo-account suggested" : "demo-account"}
                      disabled={Boolean(demoBusy) || busy}
                      onClick={() => {
                        setDemoBusy(a.email);
                        go(a.email, demo.password!);
                      }}
                    >
                      <UserRound size={16} aria-hidden="true" />
                      <span className="demo-who">
                        <span className="demo-name">{a.name}</span>
                        <span className="demo-email">{a.email}</span>
                      </span>
                      <span className="demo-role">{demoBusy === a.email ? "Signing in" : ROLE_LABELS[a.role]}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </form>
      </main>

      <Footer />
    </div>
  );
}
