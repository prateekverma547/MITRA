// The front door, on one screen: the promise, what MITRA does one line at a
// time, and a Start that opens the three ways in. The way in only changes the sign-in heading; the account decides
// what you see once you are in.

import { Briefcase, Building2, ChevronRight, LogIn, Play, Users, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Role } from "../../contracts";
import { Brand } from "../../shell/Brand";
import { Footer } from "../../shell/Footer";
import { LiveLines } from "./LiveLines";
import "./welcome.css";

const DOORS: { role: Role; title: string; text: string; icon: typeof Users }[] = [
  { role: "hr", title: "HR", text: "Screen CVs, hold the HR round, book the technical interview.", icon: Users },
  { role: "bu_head", title: "BU head", text: "Raise job requests, read the evidence, make the final decision.", icon: Briefcase },
  { role: "org_admin", title: "Organisation", text: "Every business unit, every interview, and who decided what.", icon: Building2 },
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
        <Brand />
        <Link to="/sign-in" className="btn btn-ghost btn-sm" viewTransition>
          <LogIn size={15} aria-hidden="true" /> Sign in
        </Link>
      </header>

      <main className="front-main">
        <section className="hero">
          <div className="hero-copy">
            <h1 className="hero-title">
              <span>The interview runs itself.</span>
              <span>The decision stays human.</span>
            </h1>
            <p className="hero-lede">
              MITRA screens CVs, holds the technical interview by voice, watches the room for integrity, and hands your
              people a report where every score points to what the candidate said.
            </p>

            <div className={choosing ? "start choosing" : "start"}>
              {!choosing ? (
                <div className="start-actions">
                  <button type="button" className="btn btn-primary start-btn" onClick={() => setChoosing(true)}>
                    <Play size={16} aria-hidden="true" /> Start
                  </button>
                </div>
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
                          <span className="picker-text">
                            <span className="picker-name">{door.title}</span>
                            <span className="picker-note">{door.text}</span>
                          </span>
                          <ChevronRight size={18} className="picker-go" aria-hidden="true" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          <LiveLines />
        </section>

      </main>

      <Footer />
    </div>
  );
}
