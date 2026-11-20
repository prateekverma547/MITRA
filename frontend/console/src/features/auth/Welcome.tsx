// The front door, on one screen: what MITRA is, the feature cards, and a Log in
// that opens the three ways in. The way in only changes the sign-in heading;
// the account decides what you see once you are in.

import { Briefcase, Building2, ChevronRight, LogIn, Users, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Role } from "../../contracts";
import { MitraMark } from "../../shell/MitraMark";
import { Footer } from "../../shell/Footer";
import { ThemeToggle } from "../../shell/theme";
import { Features } from "./Features";
import "./welcome.css";

const DOORS: { role: Role; title: string; icon: typeof Users }[] = [
  { role: "hr", title: "Login as HR", icon: Users },
  { role: "bu_head", title: "Login as BU Head", icon: Briefcase },
  { role: "org_admin", title: "Login as Organisation admin", icon: Building2 },
];

export function Welcome() {
  const [choosing, setChoosing] = useState(false);
  const picker = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!choosing) return;
    picker.current?.querySelector<HTMLAnchorElement>("a")?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setChoosing(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [choosing]);

  return (
    <div className="public front">
      <header className="public-top front-top">
        <span className="front-brand">
          <MitraMark size={44} />
          <span className="front-brand-name">MITRA</span>
        </span>
        <span className="front-top-actions">
          <ThemeToggle />
        </span>
      </header>

      <main className="front-main">
        <section className="hero">
          <div className="hero-copy">
            <h1 className="hero-title">
              <span>Meet MITRA,</span>
              <span>
                the AI <em className="hero-word">interviewer</em>
              </span>
            </h1>
            <p className="hero-lede">
              An end-to-end AI hiring platform. MITRA screens every CV, interviews candidates for any role, proctors every
              session and compares candidates side by side, with evidence behind every score, so you hire faster and decide
              with confidence.
            </p>

            <div className={choosing ? "start choosing" : "start"}>
              {!choosing ? (
                <button type="button" className="btn btn-primary start-btn" onClick={() => setChoosing(true)}>
                  <LogIn size={16} aria-hidden="true" /> Log in
                </button>
              ) : (
                <div className="picker" ref={picker} role="group" aria-labelledby="picker-title">
                  <div className="picker-head">
                    <span id="picker-title" className="picker-title">How do you work with MITRA?</span>
                    <button type="button" className="picker-close" onClick={() => setChoosing(false)} aria-label="Close">
                      <X size={16} />
                    </button>
                  </div>
                  <ul className="picker-doors">
                    {DOORS.map((door) => (
                      <li key={door.role}>
                        <Link className="picker-door" to={`/sign-in?as=${door.role}`} viewTransition>
                          <span className="picker-icon">
                            <door.icon size={18} aria-hidden="true" />
                          </span>
                          <span className="picker-name">{door.title}</span>
                          <ChevronRight size={18} className="picker-go" aria-hidden="true" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          <Features />
        </section>
      </main>

      <Footer />
    </div>
  );
}
