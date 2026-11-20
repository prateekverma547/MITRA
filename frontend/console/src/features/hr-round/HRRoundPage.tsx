// The HR round, run by a person from inside the console. Track H.
//
// Three moments, one page:
// - before: one "Start the room" creates a private room and the candidate's
//   credentials, and goes straight to the lobby (microphone check, devices, the
//   invitation, "Join now");
// - during: the meeting room (MeetingRoom.tsx), with the questions and notes
//   beside the call;
// - after: MITRA's evaluation of the recording, then HR's decision.
// No AI joins this room (backend/hiring/hr_round.py).

import { Ear, Eye, Video } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { STAGE_LABELS, STAGE_TONES, type HRRoundView } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { useToast } from "../../shell/ui/Toast";
import { AfterRound, HRDecision } from "./AfterRound";
import { InviteCard, MeetingRoom } from "./MeetingRoom";
import { NotesPanel, QuestionBrief, useRoundProgress } from "./RoomPanel";
import "./hr-room.css";

const WORKING = ["waiting_recording", "transcribing", "evaluating"];
const LIVE_POLL_MS = 8000;

/** What is actually happening in a live round, from Daily's presence. */
function liveWords(view: HRRoundView): { label: string; line: string; empty: boolean } {
  const r = view.round!;
  const p = r.presence;
  if (r.mode === "listen") {
    // A phone call: the candidate is never in MITRA's room, so presence says nothing about them.
    return r.status === "scheduled"
      ? { label: "Ready to listen", line: `HR will call ${view.candidate.name} on the phone; MITRA listens and transcribes.`, empty: false }
      : { label: "In progress", line: `HR is on the phone with ${view.candidate.name}; MITRA is listening.`, empty: false };
  }
  if (r.status === "scheduled") return { label: "Waiting to start", line: `The room is open. ${view.candidate.name} joins with the meeting ID and password.`, empty: false };
  if (!p || !p.known) return { label: "In progress", line: `HR is talking with ${view.candidate.name}.`, empty: false };
  if (p.candidate && p.others > 0) return { label: "In progress", line: `HR and ${view.candidate.name} are in the room.`, empty: false };
  if (p.candidate) return { label: "Candidate waiting", line: `${view.candidate.name} is in the room, waiting for HR.`, empty: false };
  if (p.others > 0) return { label: "Candidate left the call", line: `HR is in the room; ${view.candidate.name} has left.`, empty: false };
  return { label: "Nobody in the room", line: "The call has emptied, but the round is still open. End it so MITRA can evaluate the conversation.", empty: true };
}

function Room({ view, onChange }: { view: HRRoundView; onChange: (v: HRRoundView | null) => void }) {
  const toast = useToast();
  const [params] = useSearchParams();
  const r = view.round!;
  const progress = useRoundProgress(view.candidate.id, r.asked, r.notes, r.can_edit_notes && r.can_join);
  // A development-only preview of the in-call layout, with stand-in participants.
  const preview = import.meta.env.DEV && params.get("preview") === "room";

  async function cancel() {
    try {
      onChange(await api.post<HRRoundView>(`/candidates/${view.candidate.id}/hr-round/cancel`, {}));
      toast("Invitation cancelled. You can start a new room at any time.");
    } catch (e) {
      toast((e as Error).message);
    }
  }

  const [ending, setEnding] = useState(false);
  async function endNow() {
    setEnding(true);
    try {
      onChange(await api.post<HRRoundView>(`/candidates/${view.candidate.id}/hr-round/end`));
      toast("HR round ended. MITRA is evaluating the recording.");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setEnding(false);
    }
  }
  const words = r.status === "in_progress" ? liveWords(view) : null;

  return (
    <>
      {words?.empty && r.can_end && (
        <section className="room-status-card attention" role="status">
          <Eye size={22} aria-hidden="true" />
          <div>
            <h2>{words.label}</h2>
            <p className="room-muted">{words.line}</p>
          </div>
          <Button variant="primary" loading={ending} onClick={endNow}>
            End round and evaluate
          </Button>
        </section>
      )}
      <MeetingRoom view={view} progress={progress} onEnded={onChange} onCancel={cancel} preview={preview} />
    </>
  );
}

/** For a BU head or admin while the round is live: they can follow it, not join it. */
function Watching({ view }: { view: HRRoundView }) {
  return (
    <section className="room-status-card">
      <Eye size={22} aria-hidden="true" />
      <div>
        <h2>{liveWords(view).label}</h2>
        <p className="room-muted">
          {liveWords(view).line} MITRA's evaluation appears here when the round ends.
        </p>
      </div>
    </section>
  );
}

/**
 * After the round the prompter has done its job: what was asked is the record,
 * not the full list to ask from. Only the questions HR ticked are kept here.
 */
function AskedSummary({ view }: { view: HRRoundView }) {
  const asked = new Set(view.round!.asked);
  const done = view.questions.filter((q) => asked.has(q.id));
  return (
    <section className="room-panel-body asked-summary" aria-label="Questions asked">
      <header className="room-panel-head">
        <h2>Questions asked</h2>
        <span className="muted">
          {done.length} of {view.questions.length}
        </span>
      </header>
      {done.length === 0 ? (
        <p className="room-muted">None were ticked during the round. The transcript is the record of what was asked.</p>
      ) : (
        <ol className="asked-list">
          {done.map((q) => (
            <li key={q.id}>{q.text}</li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Completed({ view, onChange }: { view: HRRoundView; onChange: (v: HRRoundView) => void }) {
  const r = view.round!;
  const progress = useRoundProgress(view.candidate.id, r.asked, r.notes, r.can_edit_notes);
  return (
    <>
      <div className="after-grid">
        <AfterRound view={view} onChange={onChange} />
        <aside className="after-side">
          <div className="after-side-card">
            <NotesPanel progress={progress} editable={r.can_edit_notes} />
          </div>
          <div className="after-side-card">
            <AskedSummary view={view} />
          </div>
        </aside>
      </div>
      <HRDecision view={view} />
    </>
  );
}

export function HRRoundPage() {
  const { candidateId } = useParams();
  const toast = useToast();
  const [view, setView] = useState<HRRoundView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<"room" | "listen" | null>(null);

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

  // While MITRA works on the recording, check back every few seconds.
  const working = WORKING.includes(view?.round?.eval_status ?? "");
  useEffect(() => {
    if (!working) return;
    const timer = window.setTimeout(load, 5000);
    return () => window.clearTimeout(timer);
  }, [view, working, load]);

  // While the round is live, keep who-is-in-the-room current.
  const liveRound = view?.round?.status === "in_progress" || view?.round?.status === "scheduled";
  useEffect(() => {
    if (!liveRound) return;
    const timer = window.setTimeout(load, LIVE_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [view, liveRound, load]);

  async function start(mode: "room" | "listen") {
    setStarting(mode);
    setError(null);
    try {
      setView(await api.post<HRRoundView>(`/candidates/${candidateId}/hr-round`, { mode }));
      toast(mode === "listen" ? "Ready. Put your phone on speaker, then start listening." : "Room ready. Send the invitation, then join.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(null);
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
    <div className="page page-wide hr-round-page">
      <BackButton fallback={`/candidates/${view.candidate.id}`} label={view.candidate.name} />
      <header className="page-head room-page-head">
        <div>
          <h1 className="page-title">HR round with {view.candidate.name}</h1>
          <p className="page-sub">
            {view.job.title}
            {view.job.bu_name ? `, ${view.job.bu_name}` : ""}. A recorded voice call, transcribed live; MITRA evaluates it when the round ends.
          </p>
        </div>
        {r?.status === "in_progress" ? (
          <span className={liveWords(view).label === "In progress" ? "mark mark-lg live-dot" : "mark mark-lg tone-attention"}>{liveWords(view).label}</span>
        ) : (
          <span className={`mark mark-lg tone-${STAGE_TONES[stage]}`}>{STAGE_LABELS[stage]}</span>
        )}
      </header>

      {error && <p className="error">{error}</p>}

      {!live && view.can_book && (
        <section className="room-start-choice" aria-label="How the HR round takes place">
          <div className="room-start">
            <div className="room-start-art" aria-hidden="true">
              <Video size={30} />
            </div>
            <div className="room-start-copy">
              <h2>In MITRA's room</h2>
              <p className="room-muted">
                MITRA opens a private, recorded voice room and gives you a meeting ID and password to send {view.candidate.name}. You join from
                here; they join from their link.
              </p>
            </div>
            <Button variant="primary" icon={<Video size={17} />} loading={starting === "room"} disabled={starting !== null} onClick={() => start("room")}>
              Start the room
            </Button>
          </div>
          <div className="room-start">
            <div className="room-start-art" aria-hidden="true">
              <Ear size={30} />
            </div>
            <div className="room-start-copy">
              <h2>On your phone, MITRA listens</h2>
              <p className="room-muted">
                You call {view.candidate.name} from your own phone, on speaker, near this laptop. MITRA listens through your microphone,
                transcribes the call and shows you the questions, as in the room.
              </p>
            </div>
            <Button icon={<Ear size={17} />} loading={starting === "listen"} disabled={starting !== null} onClick={() => start("listen")}>
              Listen to a phone call
            </Button>
          </div>
        </section>
      )}
      {!live && view.can_book && <QuestionBrief questions={view.questions} name={view.candidate.name} />}

      {live && r!.can_join && <Room view={view} onChange={(v) => (v ? setView(v) : load())} />}

      {live && !r!.can_join && (
        <>
          <Watching view={view} />
          {r!.meeting_id && r!.password && <InviteCard view={view} />}
        </>
      )}

      {r && r.status === "completed" && <Completed view={view} onChange={setView} />}
    </div>
  );
}
