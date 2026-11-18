import { Link } from "react-router-dom";
import type { Role } from "../../contracts";

const DOORS: { role: Role; title: string; text: string }[] = [
  { role: "hr", title: "HR", text: "Screen CVs, run the HR round, book the technical round." },
  { role: "bu_head", title: "BU head", text: "Raise job requests and decide on your candidates." },
  { role: "org_admin", title: "Organisation", text: "Every BU, every interview, and how the system is doing." },
];

/**
 * Three doors, one sign-in. The door only changes the heading on the sign-in
 * screen; the account decides what you see once you are in.
 */
export function Welcome() {
  return (
    <div className="welcome">
      <header className="welcome-header">
        <img src="/assets/TrasparentLogo.png" alt="" width={56} height={56} />
        <h1>MITRA</h1>
        <p className="muted">AI-conducted interviews, reviewed by people.</p>
      </header>
      <ul className="doors">
        {DOORS.map((door) => (
          <li key={door.role}>
            <Link className="door" to={`/sign-in?as=${door.role}`}>
              <span className="door-title">{door.title}</span>
              <span className="door-text">{door.text}</span>
              <span className="door-go">Sign in as {door.title}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
