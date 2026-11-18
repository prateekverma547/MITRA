import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { ROLE_LABELS } from "../contracts";
import { useSession } from "../session";
import { Brand } from "./Brand";
import { Footer } from "./Footer";
import { navFor } from "./registry";

export function Shell() {
  const { me, signOut } = useSession();
  const navigate = useNavigate();
  if (!me) return null;

  const items = [{ to: "/home", label: "Home", order: 0 }, ...navFor(me.role)];

  async function handleSignOut() {
    await signOut();
    navigate("/", { replace: true });
  }

  return (
    <div className="shell">
      <aside className="rail">
        <Brand />
        <nav aria-label="Main" className="rail-nav">
          {items.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "rail-link active" : "rail-link")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="who">
          <span className="who-name">{me.name}</span>
          <span className="who-role">{me.bu_name ? `${ROLE_LABELS[me.role]}, ${me.bu_name}` : ROLE_LABELS[me.role]}</span>
          <button type="button" className="quiet-link" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </aside>
      <div className="main">
        <main className="content">
          <Outlet />
        </main>
        <Footer />
      </div>
    </div>
  );
}
