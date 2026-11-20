import { Check, RotateCcw, ShieldCheck, WifiOff } from "lucide-react";
import { PreJoinFrame } from "./EnterStep";
import type { RoomFacts } from "./room-api";

/**
 * complete: Mitra closed the interview, or the HR conversation ended.
 * ended:    the candidate chose to stop.
 * dropped:  the connection went; they can come back with the same details.
 */
export type Ending = "complete" | "ended" | "dropped";

export function DoneStep({
  facts,
  ending,
  joinedAt,
  onRejoin,
}: {
  facts: RoomFacts;
  ending: Ending;
  joinedAt: number | null;
  onRejoin: () => void;
}) {
  const technical = facts.round === "technical";
  const minutes = joinedAt ? Math.max(1, Math.round((Date.now() - joinedAt) / 60000)) : null;
  const company = facts.organisation ?? "the company";

  if (ending === "dropped") {
    return (
      <PreJoinFrame>
        <section className="meet-done" aria-labelledby="done-title">
          <span className="meet-done-mark amber" aria-hidden="true">
            <WifiOff size={26} />
          </span>
          <h1 id="done-title" className="meet-title">
            You have left the room
          </h1>
          <p className="meet-lead">
            The connection to the call was lost. Your interview is not over: join again with the same details
            {technical ? ` and ${facts.bot_name} carries on where you left off.` : "."}
          </p>
          <button type="button" className="meet-primary big" onClick={onRejoin}>
            <RotateCcw size={18} /> Rejoin
          </button>
        </section>
      </PreJoinFrame>
    );
  }

  const title =
    ending === "ended"
      ? technical
        ? "You ended the interview."
        : "You left the conversation."
      : technical
        ? "Thank you. Your interview is complete."
        : "Thank you for your time.";

  return (
    <PreJoinFrame>
      <section className="meet-done" aria-labelledby="done-title">
        <span className="meet-done-mark" aria-hidden="true">
          <Check size={28} />
        </span>
        <h1 id="done-title" className="meet-title">
          {title}
        </h1>
        <p className="meet-lead">
          {minutes ? `It lasted about ${minutes} ${minutes === 1 ? "minute" : "minutes"}. ` : ""}You can close this window now.
        </p>
        <div className="meet-card meet-next">
          <h2 className="meet-subtitle">What happens next</h2>
          <ol>
            {technical ? (
              <>
                <li>MITRA writes a report from what you said, with your own words beside every score.</li>
                <li>The hiring team reads it. People make the decision, not MITRA.</li>
              </>
            ) : (
              <>
                <li>MITRA transcribes the conversation and summarises it for HR.</li>
                <li>HR reads it and decides the next step. A person makes the decision.</li>
              </>
            )}
            <li>{company === "the company" ? "The company" : company} contacts you with the outcome.</li>
          </ol>
        </div>
        <p className="meet-rights">
          <ShieldCheck size={16} aria-hidden="true" />
          <span>
            {facts.recording ? `The recording is deleted after ${facts.retention_days} days. ` : ""}You can ask {company} to see, correct or
            delete your data at any time by replying to your invitation.
          </span>
        </p>
      </section>
    </PreJoinFrame>
  );
}
