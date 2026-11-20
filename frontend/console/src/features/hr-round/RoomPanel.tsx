// The HR round's working notes: the ten questions as a checklist and a notes
// box, saved as HR works (POST /candidates/{id}/hr-round/progress). One save
// loop for both, so ticking a question and typing a note never race.

import { Check, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { HRQuestion } from "../../contracts";

const SAVE_DELAY_MS = 700;

export type SaveState = "saved" | "saving" | "error";

export function useRoundProgress(candidateId: string, initialAsked: string[], initialNotes: string, editable: boolean) {
  const [asked, setAsked] = useState<string[]>(initialAsked);
  const [notes, setNotes] = useState(initialNotes);
  const [saved, setSaved] = useState<SaveState>("saved");
  const first = useRef(true);

  useEffect(() => {
    if (first.current || !editable) {
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
  }, [asked, notes, candidateId, editable]);

  function toggle(id: string) {
    if (!editable) return;
    setAsked((all) => (all.includes(id) ? all.filter((x) => x !== id) : [...all, id]));
  }

  return { asked, notes, setNotes, toggle, saved };
}

export type Progress = ReturnType<typeof useRoundProgress>;

function SavedLabel({ saved, asked, total }: { saved: SaveState; asked: number; total: number }) {
  return (
    <span className="room-saved" aria-live="polite">
      {saved === "saving" ? "Saving" : saved === "error" ? "Not saved, retrying on your next change" : `${asked} of ${total} asked`}
    </span>
  );
}

/** The first question not yet asked, after `from` if given, wrapping round. */
function nextUnasked(questions: HRQuestion[], asked: string[], from = -1): number {
  for (let step = 1; step <= questions.length; step++) {
    const i = (from + step) % questions.length;
    if (!asked.includes(questions[i].id)) return i;
  }
  return -1;
}

/**
 * During the round: the question to ask now, large enough to read while
 * talking, with "Asked, next" to move on, and the whole list below to jump to
 * any of them. Before and after the round: the same list as a checklist.
 */
export function QuestionsPanel({
  questions,
  progress,
  editable,
  live = false,
  onClose,
}: {
  questions: HRQuestion[];
  progress: Progress;
  editable: boolean;
  live?: boolean;
  onClose?: () => void;
}) {
  const [focus, setFocus] = useState(() => Math.max(0, nextUnasked(questions, progress.asked)));
  const allAsked = questions.length > 0 && questions.every((q) => progress.asked.includes(q.id));
  const current = questions[focus];

  function askedNext() {
    if (!current) return;
    const asked = progress.asked.includes(current.id) ? progress.asked : [...progress.asked, current.id];
    if (!progress.asked.includes(current.id)) progress.toggle(current.id);
    const next = nextUnasked(questions, asked, focus);
    if (next >= 0) setFocus(next);
  }

  return (
    <section className="room-panel-body" aria-label="Questions">
      <header className="room-panel-head">
        <h2>Questions</h2>
        <SavedLabel saved={progress.saved} asked={progress.asked.length} total={questions.length} />
        {onClose && (
          <button type="button" className="room-panel-close" onClick={onClose} aria-label="Close questions">
            <X size={16} />
          </button>
        )}
      </header>
      {questions.length === 0 ? (
        <p className="room-muted">No screening questions for this candidate.</p>
      ) : (
        <>
          {live && current && (
            <div className={`prompter${allAsked ? " all" : ""}`} aria-live="polite">
              <div className="prompter-top">
                <span className="prompter-kicker">{allAsked ? "All asked" : progress.asked.includes(current.id) ? "Asked" : "Ask now"}</span>
                <span className="prompter-count">
                  {focus + 1} of {questions.length}
                </span>
              </div>
              <p className="prompter-q">{current.text}</p>
              {current.why && <p className="prompter-why">{current.why}</p>}
              <div className="prompter-actions">
                {editable && !progress.asked.includes(current.id) && (
                  <button type="button" className="prompter-next" onClick={askedNext}>
                    <Check size={16} /> Asked, next
                  </button>
                )}
                {editable && progress.asked.includes(current.id) && !allAsked && (
                  <button type="button" className="prompter-next" onClick={() => setFocus(Math.max(0, nextUnasked(questions, progress.asked, focus)))}>
                    Next unasked
                  </button>
                )}
                <span className="prompter-nav">
                  <button type="button" onClick={() => setFocus((focus - 1 + questions.length) % questions.length)} aria-label="Previous question">
                    <ChevronLeft size={18} />
                  </button>
                  <button type="button" onClick={() => setFocus((focus + 1) % questions.length)} aria-label="Next question">
                    <ChevronRight size={18} />
                  </button>
                </span>
              </div>
            </div>
          )}
          <ol className={`room-checklist${live ? " compact" : ""}`}>
            {questions.map((q, i) => {
              const done = progress.asked.includes(q.id);
              return (
                <li key={q.id} className={live && i === focus ? "focused" : undefined}>
                  <label className={done ? "room-check done" : "room-check"}>
                    <input type="checkbox" checked={done} onChange={() => progress.toggle(q.id)} disabled={!editable} />
                    <span className="room-check-box" aria-hidden="true">
                      <Check size={13} strokeWidth={3} />
                    </span>
                    <span>
                      {live ? (
                        <button type="button" className="room-q-btn" onClick={(e) => (e.preventDefault(), setFocus(i))}>
                          <span className="room-q-num">{i + 1}.</span> {q.text}
                        </button>
                      ) : (
                        <>
                          <span className="room-q">{q.text}</span>
                          <span className="room-why">{q.why}</span>
                        </>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
}

/** Before the round: every question, to read and prepare with. Nothing to tick yet. */
export function QuestionBrief({ questions, name }: { questions: HRQuestion[]; name: string }) {
  if (questions.length === 0) return null;
  return (
    <section className="question-brief" aria-labelledby="brief-title">
      <header>
        <h2 id="brief-title">Your questions for {name}</h2>
        <p className="room-muted">
          {questions.length} questions MITRA wrote from the CV screening. Read them before you start; in the room they sit beside the call,
          one at a time, and you tick each one as you ask it.
        </p>
      </header>
      <ol className="brief-list">
        {questions.map((q, i) => (
          <li key={q.id}>
            <span className="brief-num">{i + 1}</span>
            <span>
              <span className="brief-q">{q.text}</span>
              {q.why && <span className="brief-why">{q.why}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function NotesPanel({ progress, editable, onClose }: { progress: Progress; editable: boolean; onClose?: () => void }) {
  return (
    <section className="room-panel-body" aria-label="Notes">
      <header className="room-panel-head">
        <h2>Notes</h2>
        <span className="room-saved" aria-live="polite">
          {progress.saved === "saving" ? "Saving" : progress.saved === "error" ? "Not saved" : "Saved as you type"}
        </span>
        {onClose && (
          <button type="button" className="room-panel-close" onClick={onClose} aria-label="Close notes">
            <X size={16} />
          </button>
        )}
      </header>
      <textarea
        className="room-notes"
        value={progress.notes}
        onChange={(e) => progress.setNotes(e.target.value)}
        readOnly={!editable}
        aria-label="Your notes"
        placeholder="What stood out, and what the MITRA interview round should probe."
      />
    </section>
  );
}
