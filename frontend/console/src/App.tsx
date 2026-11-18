import { HashRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import type { Role } from "./contracts";
import { SessionProvider, useSession } from "./session";
import { Home } from "./shell/Home";
import { Shell } from "./shell/Shell";
import { featureRoutes } from "./shell/registry";

// Hash routing: every screen has its own URL, so back, forward, refresh and
// bookmarks work, and FastAPI only ever has to serve one index.html.

function Loading() {
  return (
    <div className="loading-screen" role="status">
      <span className="muted">Loading…</span>
    </div>
  );
}

/** Signed-in routes. Signed out, you go to sign-in and come back afterwards. */
function RequireSession() {
  const { me, loading } = useSession();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!me) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/sign-in?next=${next}`} replace />;
  }
  return <Outlet />;
}

/** Public routes jump straight home when you are already signed in. */
function PublicOnly({ children }: { children: ReactNode }) {
  const { me, loading } = useSession();
  if (loading) return <Loading />;
  if (me) return <Navigate to="/home" replace />;
  return <>{children}</>;
}

function RoleGate({ roles, children }: { roles?: Role[]; children: ReactNode }) {
  const { me } = useSession();
  if (roles && me && !roles.includes(me.role)) {
    return (
      <div className="page">
        <h1 className="page-title">Not available to your role</h1>
        <p className="page-sub">This screen belongs to another role. Your home has everything yours can do.</p>
      </div>
    );
  }
  return <>{children}</>;
}

export function App() {
  return (
    <HashRouter>
      <SessionProvider>
        <Routes>
          {featureRoutes
            .filter((r) => r.public)
            .map((r) => (
              <Route key={r.path} path={r.path} element={<PublicOnly>{r.element}</PublicOnly>} />
            ))}
          <Route element={<RequireSession />}>
            <Route element={<Shell />}>
              <Route path="/home" element={<Home />} />
              {featureRoutes
                .filter((r) => !r.public)
                .map((r) => (
                  <Route key={r.path} path={r.path} element={<RoleGate roles={r.roles}>{r.element}</RoleGate>} />
                ))}
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </SessionProvider>
    </HashRouter>
  );
}
