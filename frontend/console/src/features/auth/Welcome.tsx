import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { Role } from "../../contracts";
import { Brand } from "../../shell/Brand";
import { Footer } from "../../shell/Footer";

const DOORS: { role: Role; title: string; text: string }[] = [
  { role: "hr", title: "HR", text: "Screen CVs, hold the HR round, book the technical interview." },
  { role: "bu_head", title: "BU head", text: "Raise job requests and decide on the candidates sent to you." },
  { role: "org_admin", title: "Organisation", text: "See every business unit, every interview, and how the system is running." },
];

/**
 * Three ways in, one sign-in. The way in only changes the sign-in heading;
 * the account decides what you see once you are in.
 */
export function Welcome() {
  return (
    <div className="public">
      <header className="public-top">
        <Brand />
      </header>

      <main className="welcome">
        <section className="welcome-lead">
          <h1 className="display">
            <span>People decide.</span>
            <span>MITRA brings them the evidence.</span>
          </h1>
          <p className="lede">
            Job requests, CV screening, the HR round and the AI technical interview, with every score traced back to what
            the candidate actually said.
          </p>

          <nav className="doors" aria-label="Sign in">
            {DOORS.map((door) => (
              <Link key={door.role} className="door" to={`/sign-in?as=${door.role}`} viewTransition>
                <span className="door-title">{door.title}</span>
                <span className="door-text">{door.text}</span>
                <span className="door-go">
                  Sign in <ArrowRight size={15} aria-hidden="true" />
                </span>
              </Link>
            ))}
          </nav>
        </section>

        <aside className="evidence live-border" aria-label="An example of how MITRA shows evidence">
          <p className="evidence-label">How a score appears in a report</p>
          <figure>
            <blockquote>
              We told them the number was chance, and showed them why the labels were the problem.
            </blockquote>
            <figcaption>
              <span>Technical interview, 12:41</span>
              <span>Quoted from the transcript</span>
            </figcaption>
          </figure>
          <div className="evidence-score">
            <span>Evaluation and honest measurement</span>
            <span className="evidence-value">3 of 4</span>
          </div>
          <p className="evidence-note">Example only. A score without a quote is never shown.</p>
        </aside>
      </main>

      <Footer />
    </div>
  );
}
