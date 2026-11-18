import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { ROLE_LABELS } from "../contracts";
import { useSession } from "../session";
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
      <aside className="sidebar">
        <div className="brand">
          <img src="/assets/TrasparentLogo.png" alt="" width={28} height={28} />
          <span>MITRA</span>
        </div>
        <nav aria-label="Main">
          {items.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="who">
          <div className="who-name">{me.name}</div>
          <div className="who-role">
            {ROLE_LABELS[me.role]}
            {me.bu_name ? ` · ${me.bu_name}` : ""}
          </div>
          <button type="button" className="link-button" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
