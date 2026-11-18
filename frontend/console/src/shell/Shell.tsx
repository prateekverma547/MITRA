import { Briefcase, Home as HomeIcon, LogOut } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { ROLE_LABELS } from "../contracts";
import { useSession } from "../session";
import { Brand } from "./Brand";
import { Footer } from "./Footer";
import { navFor } from "./registry";
import { Button } from "./ui/Button";

const ICONS: Record<string, typeof HomeIcon> = { "/home": HomeIcon, "/jobs": Briefcase };

export function Shell() {
  const { me, signOut } = useSession();
  const navigate = useNavigate();
  const [leaving, setLeaving] = useState(false);
  if (!me) return null;

  const items = [{ to: "/home", label: "Home", order: 0 }, ...navFor(me.role)];

  async function handleSignOut() {
    setLeaving(true);
    await signOut();
    navigate("/", { replace: true, viewTransition: true });
  }

  return (
    <div className="shell">
      <aside className="rail">
        <Brand />
        <nav aria-label="Main" className="rail-nav">
          {items.map((item) => {
            const Icon = ICONS[item.to];
            return (
              <NavLink
                key={item.to}
                to={item.to}
                viewTransition
                className={({ isActive }) => (isActive ? "rail-link active" : "rail-link")}
              >
                {Icon && <Icon size={17} aria-hidden="true" />}
                {item.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="who">
          <span className="who-name">{me.name}</span>
          <span className="who-role">{me.bu_name ? `${ROLE_LABELS[me.role]}, ${me.bu_name}` : ROLE_LABELS[me.role]}</span>
          <Button variant="ghost" size="sm" icon={<LogOut size={15} />} loading={leaving} onClick={handleSignOut} className="who-out">
            Sign out
          </Button>
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
