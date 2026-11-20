import { Bot, Clock, ShieldCheck, UserRound } from "lucide-react";
import { useState, type ReactNode } from "react";
import { PreJoinFrame } from "./EnterStep";
import type { RoomFacts } from "./room-api";

interface Item {
  id: string;
  lead: string;
  body: ReactNode;
}

function days(n: number) {
  return `${n} ${n === 1 ? "day" : "days"}`;
}

/**
 * Says what will actually happen. The recording item reads the same switch the
 * recorder does (hiring/room.py), so the page never claims a recording that is
 * not made, nor hides one that is.
 */
function items(f: RoomFacts): Item[] {
  if (f.round === "hr") {
    return [
      {
        id: "person",
        lead: "A conversation with HR.",
        body: "A voice call with a member of the HR team, no camera. No AI takes part in the conversation.",
      },
      {
        id: "recording",
        lead: "Recorded.",
        body: `Your voice is recorded and transcribed as you speak, so HR can follow along. The recording is kept for ${days(f.retention_days)}, then deleted. You can ask for deletion sooner.`,
      },
      {
        id: "evaluated",
        lead: "Read by a person.",
        body: "Afterwards MITRA transcribes the recording and summarises it for HR. A person reads it and makes the decision.",
      },
    ];
  }
  return [
    {
      id: "ai",
      lead: "An AI interviewer.",
      body: `${f.bot_name} asks the questions and writes a report with your own words beside every score. A person reads it and makes the decision.`,
    },
    {
      id: "recording",
      lead: f.recording ? "Recorded." : "Not recorded.",
      body: f.recording
        ? `Your camera, microphone and shared screen are recorded and kept for ${days(f.retention_days)}, then deleted. You can ask for deletion sooner.`
        : "No video or sound is saved. What you say is written down as a transcript.",
    },
    {
      id: "proctoring",
      lead: "Proctored.",
      body: "Your camera stays on and you share your entire screen. We note when this page goes out of view or screen sharing stops. We never judge emotion, tone or personality. Anything noted goes to a person as context and never changes your score.",
    },
  ];
}

export function ConsentStep({ facts, onAgree, onBack }: { facts: RoomFacts; onAgree: (gazeRemindersOff: boolean) => void; onBack: () => void }) {
  const list = items(facts);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [gazeOff, setGazeOff] = useState(false);
  const all = list.every((i) => ticked[i.id]);
  const technical = facts.round === "technical";
  const company = facts.organisation ?? "the company";

  return (
    <PreJoinFrame step="Step 2 of 3">
      <div className="meet-consent">
        <section className="meet-consent-intro" aria-labelledby="consent-title">
          <p className="meet-hello">{facts.first_name ? `Hello ${facts.first_name},` : "Hello,"}</p>
          <h1 id="consent-title" className="meet-title">
            {technical ? "Your interview with MITRA" : "Your conversation with HR"}
          </h1>
          <dl className="meet-facts">
            <div>
              <dt>Role</dt>
              <dd>{facts.role_title}</dd>
            </div>
            {facts.organisation && (
              <div>
                <dt>Company</dt>
                <dd>{facts.organisation}</dd>
              </div>
            )}
            <div>
              <dt>With</dt>
              <dd className="meet-with">
                {technical ? <Bot size={15} /> : <UserRound size={15} />}
                {technical ? `${facts.bot_name}, an AI interviewer` : "A member of the HR team"}
              </dd>
            </div>
            <div>
              <dt>Length</dt>
              <dd className="meet-with">
                <Clock size={15} /> About {facts.duration_minutes} minutes
              </dd>
            </div>
          </dl>
          <p className="meet-muted">
            {technical
              ? "Talk as you would with a person. Take your time; pausing to think is fine. You can ask for a question again, or say you would rather not answer."
              : "It is a conversation, not a test. You only need a microphone and somewhere quiet to talk."}
          </p>
        </section>

        <section className="meet-card meet-consent-card" aria-labelledby="before-title">
          <h2 id="before-title" className="meet-subtitle">
            Before you start
          </h2>
          <fieldset className="meet-ticks">
            <legend className="sr-only">Tick each item to agree to it</legend>
            {list.map((item) => (
              <label key={item.id} className={`meet-tick${ticked[item.id] ? " on" : ""}`}>
                <input
                  type="checkbox"
                  checked={Boolean(ticked[item.id])}
                  onChange={(e) => setTicked((t) => ({ ...t, [item.id]: e.target.checked }))}
                />
                <span>
                  <b>{item.lead}</b> {item.body}
                </span>
              </label>
            ))}
          </fieldset>
          {technical && (
            <label className="meet-switch">
              <input type="checkbox" checked={gazeOff} onChange={(e) => setGazeOff(e.target.checked)} />
              <span>
                <b>Turn off reminders about looking away.</b> For example, if you use a screen reader or look away when you think. This
                is noted for the reviewer so nobody misreads it.
              </span>
            </label>
          )}
          <p className="meet-rights">
            <ShieldCheck size={16} aria-hidden="true" />
            <span>
              Under India's Digital Personal Data Protection Act you can ask {company} to see, correct or delete your data at any time.
              Reply to your invitation to ask.
            </span>
          </p>
          <div className="meet-actions">
            <button type="button" className="meet-quiet" onClick={onBack}>
              Back
            </button>
            <button type="button" className="meet-primary" disabled={!all} onClick={() => onAgree(gazeOff)}>
              {technical ? "Agree and check my devices" : "Agree and check my microphone"}
            </button>
          </div>
          {!all && <p className="meet-hint">Tick every item to continue. Nothing is ticked for you.</p>}
        </section>
      </div>
    </PreJoinFrame>
  );
}
