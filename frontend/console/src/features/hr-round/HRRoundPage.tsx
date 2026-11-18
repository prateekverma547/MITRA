// The HR round, run by a person from inside the console. Track H.
//
// Before the call: the credentials to send the candidate, and the questions.
// During it: the call on the left (Daily, embedded, with HR as the room's
// owner), and on the right the ten questions as a checklist and a notes box,
// both saved as HR works. No bot is in this room (backend/hiring/hr_round.py).

import DailyIframe, { type DailyCall } from "@daily-co/daily-js";
import { Check, ClipboardCopy, Loader2, PhoneOff, Undo2, Video, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS, STAGE_TONES, type HRQuestion, type HRRoundView } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button } from "../../shell/ui/Button";
import { CopyField, copyText } from "../../shell/ui/CopyField";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { useToast } from "../../shell/ui/Toast";

const SAVE_DELAY_MS = 700;

function invitation(view: HRRoundView): string {
  const r = view.round!;
  return [
    `Hello ${view.candidate.name},`,
    "",
    `Your HR conversation for ${view.job.title} is ready. Join here: ${r.join_url}`,
    `Meeting ID: ${r.meeting_id}`,
    `Password: ${r.password}`,
    "",
    "It is a conversation with a member of our HR team, and it is recorded.",
  ].join("\n");
}

// ---- the call ------------------------------------------------------------------------------------

function Call({ candidateId, onLeft }: { candidateId: string; onLeft: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const call = useRef<DailyCall | null>(null);
  const [state, setState] = useState<"idle" | "joining" | "in" | "left">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(
    () => () => {
      call.current?.destroy();
      call.current = null;
    },
    [],
  );

  async function join() {
    if (!host.current) return;
    setState("joining");
    setError(null);
    try {
      const { room_url, token } = await api.post<{ room_url: string; token: string }>(`/candidates/${candidateId}/hr-round/join`);
      const frame = DailyIframe.createFrame(host.current, {
        showLeaveButton: true,
        iframeStyle: { width: "100%", height: "100%", border: "0" },
      });
      call.current = frame;
      frame.on("left-meeting", () => {
        frame.destroy();
        call.current = null;
        setState("left");
        onLeft();
      });
      await frame.join({ url: room_url, token });
      setState("in");
    } catch (e) {
      setError((e as Error).message || "Could not join the call.");
      call.current?.destroy();
      call.current = null;
      setState("idle");
    }
  }

  return (
    <div className="call">
      <div ref={host} className="call-frame" />
      {state !== "in" && (
        <div className="call-idle">
          <div>
            <Video size={34} aria-hidden="true" />
            <h2 className="section-title call-title">{state === "left" ? "You left the call" : "Join when you are ready"}</h2>
            <p>
              {state === "left"
                ? "You can rejoin while the round is open. End the round when you have finished."
                : "You join as the host. The candidate joins with the meeting ID and password you sent them."}
            </p>
            {error && <p className="error">{error}</p>}
            <Button
              variant="primary"
              icon={state === "joining" ? <Loader2 size={17} className="spin" /> : <Video size={17} />}
              disabled={state === "joining"}
              onClick={join}
            >
              {state === "left" ? "Rejoin the call" : state === "joining" ? "Joining" : "Join the HR round"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- checklist and notes ----------------------------------------------------------------------------

function SidePanel({
  candidateId,
  questions,
  initialAsked,
  initialNotes,
  editable,
}: {
  candidateId: string;
  questions: HRQuestion[];
  initialAsked: string[];
  initialNotes: string;
  editable: boolean;
}) {
  const [asked, setAsked] = useState<string[]>(initialAsked);
  const [notes, setNotes] = useState(initialNotes);
  const [saved, setSaved] = useState<"saved" | "saving" | "error">("saved");
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setSaved("saving");
    const timer = window.setTimeout(async () => {
      try {
        await api.post(`/candidates/${candidateId}/hr-round/progress`, { asked, notes });
        setSaved("saved");
      } catch {
        setSaved("error");
      }
    }, SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [asked, notes, candidateId]);

  function toggle(id: string) {
    setAsked((all) => (all.includes(id) ? all.filter((x) => x !== id) : [...all, id]));
  }

  return (
    <aside className="side-panel">
      <div className="panel-head">
        <h2 className="section-title">Questions</h2>
        <span className="saved" aria-live="polite">
          {saved === "saving" ? "Saving" : saved === "error" ? "Not saved, retrying on your next change" : `${asked.length} of ${questions.length} asked`}
        </span>
      </div>
      {questions.length === 0 ? (
        <p className="muted">No screening questions for this candidate.</p>
      ) : (
        <ol className="checklist">
          {questions.map((q) => (
            <li key={q.id}>
              <label className="check">
                <input type="checkbox" checked={asked.includes(q.id)} onChange={() => toggle(q.id)} disabled={!editable} />
                <span className="check-box" aria-hidden="true">
                  <Check size={14} strokeWidth={3} />
                </span>
                <span className="check-text">
                  {q.text}
                  <span className="check-why">{q.why}</span>
                </span>
              </label>
            </li>
          ))}
        </ol>
      )}
      <div className="notes">
        <label htmlFor="notes" className="section-title notes-title">
          Your notes
        </label>
        <textarea
          id="notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          readOnly={!editable}
          placeholder="What stood out, what to follow up in the technical round. Saved as you type."
        />
      </div>
    </aside>
  );
}

// ---- the page ------------------------------------------------------------------------------------------

export function HRRoundPage() {
  const { candidateId } = useParams();
  const toast = useToast();
  const [view, setView] = useState<HRRoundView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"book" | "end" | "cancel" | null>(null);
  const [ending, setEnding] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    try {
      setView(await api.get<HRRoundView>(`/candidates/${candidateId}/hr-round`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [candidateId]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(kind: "book" | "end" | "cancel") {
    setBusy(kind);
    setError(null);
    try {
      const path = kind === "book" ? "" : `/${kind}`;
      const body = kind === "cancel" ? { reason } : undefined;
      setView(await api.post<HRRoundView>(`/candidates/${candidateId}/hr-round${path}`, body));
      toast(kind === "book" ? "HR round booked" : kind === "end" ? "HR round ended" : "HR round cancelled");
      setEnding(false);
      setCancelling(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (error && !view) {
    return (
      <div className="page">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!view) return <PageSkeleton />;

  const r = view.round;
  const live = r && (r.status === "scheduled" || r.status === "in_progress");
  const stage = view.candidate.stage;

  return (
    <div className="page page-wide">
      <BackButton fallback={`/candidates/${view.candidate.id}`} label={view.candidate.name} />
      <header className="page-head">
        <div>
          <h1 className="page-title">HR round</h1>
          <p className="page-sub">
            {view.candidate.name}, for {view.job.title}. A conversation between HR and the candidate; no AI joins it.
          </p>
        </div>
        {r?.status === "in_progress" ? (
          <span className="mark mark-lg live-dot">In progress</span>
        ) : (
          <span className={`mark mark-lg tone-${STAGE_TONES[stage]}`}>{STAGE_LABELS[stage]}</span>
        )}
      </header>

      {error && <p className="error">{error}</p>}

      {!r && (
        <section className="hr-entry live-border">
          <div>
            <h2 className="section-title">Not booked yet</h2>
            <p className="muted">Booking creates a private room and the meeting ID and password to send the candidate.</p>
          </div>
          {view.can_book && (
            <Button variant="primary" loading={busy === "book"} onClick={() => act("book")}>
              Book the HR round
            </Button>
          )}
        </section>
      )}

      {r && r.meeting_id && live && (
        <section className="invite">
          <div className="credentials">
            <CopyField label="Meeting ID" value={r.meeting_id} />
            <CopyField label="Password" value={r.password ?? ""} />
            <CopyField label="Join page" value={r.join_url ?? ""} small />
          </div>
          <Button
            icon={<ClipboardCopy size={16} />}
            onClick={async () => {
              if (await copyText(invitation(view))) toast("Copied a message you can send to the candidate");
            }}
          >
            Copy an invitation to send
          </Button>
        </section>
      )}

      {r && live && (
        <div className="hr-grid">
          {r.can_join ? (
            <Call candidateId={view.candidate.id} onLeft={load} />
          ) : (
            <div className="call">
              <div className="call-idle">
                <p>Only HR joins this call from the console.</p>
              </div>
            </div>
          )}
          <SidePanel
            candidateId={view.candidate.id}
            questions={view.questions}
            initialAsked={r.asked}
            initialNotes={r.notes}
            editable={r.can_edit_notes && r.can_join}
          />
        </div>
      )}

      {r && r.status === "completed" && (
        <div className="hr-grid">
          <section className="hr-entry">
            <div>
              <h2 className="section-title">The HR round has ended</h2>
              <p className="muted">
                {r.asked.length} of {view.questions.length} questions asked. The transcript and MITRA's evaluation of it come
                next; you will decide on Reject or Proceed to technical from there.
              </p>
            </div>
          </section>
          <SidePanel
            candidateId={view.candidate.id}
            questions={view.questions}
            initialAsked={r.asked}
            initialNotes={r.notes}
            editable={r.can_edit_notes}
          />
        </div>
      )}

      {r && (r.can_end || r.can_cancel) && (
        <div className="decision-bar decision-stack">
          {cancelling ? (
            <form
              className="reason"
              onSubmit={(e) => {
                e.preventDefault();
                act("cancel");
              }}
            >
              <label htmlFor="cancel-reason">Why is the round cancelled? The candidate can be booked again.</label>
              <textarea id="cancel-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
              <div className="actions">
                <Button type="submit" variant="danger" icon={<XCircle size={17} />} loading={busy === "cancel"} disabled={!reason.trim()}>
                  Cancel the round
                </Button>
                <Button variant="ghost" icon={<Undo2 size={16} />} onClick={() => setCancelling(false)}>
                  Keep it
                </Button>
              </div>
            </form>
          ) : ending ? (
            <div className="actions">
              <span className="decision-q">End the HR round for both of you?</span>
              <Button variant="danger" icon={<PhoneOff size={17} />} loading={busy === "end"} onClick={() => act("end")}>
                End the round
              </Button>
              <Button variant="ghost" icon={<Undo2 size={16} />} onClick={() => setEnding(false)}>
                Not yet
              </Button>
            </div>
          ) : (
            <div className="actions">
              {r.can_end && (
                <Button variant="primary" icon={<PhoneOff size={17} />} onClick={() => setEnding(true)}>
                  End the round
                </Button>
              )}
              {r.can_cancel && (
                <Button variant="ghost" icon={<XCircle size={16} />} onClick={() => setCancelling(true)}>
                  Cancel the booking
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
