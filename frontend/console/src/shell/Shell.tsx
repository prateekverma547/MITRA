import { Home as HomeIcon, LogOut, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { ROLE_LABELS } from "../contracts";
import { useSession } from "../session";
import { Brand } from "./Brand";
import { CommandPalette, useCommandPalette } from "./CommandPalette";
import { Footer } from "./Footer";
import { navFor, type NavItem } from "./registry";
import { ThemeToggle } from "./theme";
import { Button } from "./ui/Button";

/** How many people are waiting on the signed-in user. Refreshed on every page change. */
function useBadges() {
  const location = useLocation();
  const [needsYou, setNeedsYou] = useState(0);
  useEffect(() => {
    let live = true;
    api
      .get<{ needs_you: number }>("/badges")
      .then((b) => live && setNeedsYou(b.needs_you))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [location.pathname]);
  return { needs_you: needsYou };
}

export function Shell() {
  const { me, signOut } = useSession();
  const navigate = useNavigate();
  const [leaving, setLeaving] = useState(false);
  const palette = useCommandPalette();
  const badges = useBadges();
  if (!me) return null;
  const mac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);

  const items: NavItem[] = [{ to: "/home", label: "Home", order: 0, roles: [me.role], icon: HomeIcon }, ...navFor(me.role)];
  // Main items first, then each named section in order of its first item.
  const sections: { title: string | null; items: NavItem[] }[] = [];
  for (const item of items) {
    const title = item.section ?? null;
    let group = sections.find((s) => s.title === title);
    if (!group) {
      group = { title, items: [] };
      if (title === null) sections.unshift(group);
      else sections.push(group);
    }
    group.items.push(item);
  }

  async function handleSignOut() {
    setLeaving(true);
    await signOut();
    navigate("/", { replace: true, viewTransition: true });
  }

  return (
    <div className="shell">
      <aside className="rail">
        <Brand />
        <button type="button" className="rail-search" onClick={() => palette.setOpen(true)}>
          <Search size={16} aria-hidden="true" />
          <span>Search</span>
          <kbd>{mac ? "⌘K" : "Ctrl K"}</kbd>
        </button>
        <nav aria-label="Main" className="rail-nav">
          {sections.map((section) => (
            <div key={section.title ?? "main"} className="rail-group">
              {section.title && <span className="rail-section">{section.title}</span>}
              {section.items.map((item) => {
                const Icon = item.icon;
                const count = item.badge ? badges[item.badge] : 0;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/home"}
                    viewTransition
                    className={({ isActive }) => (isActive ? "rail-link active" : "rail-link")}
                  >
                    {Icon && <Icon size={17} aria-hidden="true" />}
                    <span className="rail-label">{item.label}</span>
                    {count > 0 && (
                      <span className="rail-badge" aria-label={`${count} waiting for you`}>
                        {count}
                      </span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="who">
          <span className="who-name">{me.name}</span>
          <span className="who-role">{me.bu_name ? `${ROLE_LABELS[me.role]}, ${me.bu_name}` : ROLE_LABELS[me.role]}</span>
          <ThemeToggle />
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
      <CommandPalette open={palette.open} onClose={() => palette.setOpen(false)} />
    </div>
  );
}
