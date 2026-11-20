// A new job request: a title, and the job description uploaded, typed or
// pasted (or left out: MITRA can ask instead). Track H.

import { ClipboardPaste, FileUp, MessageSquarePlus, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { JobRequest } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { FileDrop } from "../../shell/ui/FileDrop";
import "./jobs.css";

const MAX_JD = 30000;

const STEPS = [
  { title: "MITRA reads the role", text: "From the title and the job description, if you give one." },
  { title: "A few questions", text: "What matters most, how senior, and what would rule someone out." },
  { title: "What MITRA understood", text: "The competencies and their weights, for you to edit or approve." },
  { title: "HR takes it from there", text: "Approved requests go to HR, who add candidates." },
];

export function NewJob() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<"file" | "text">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("title", title.trim());
    if (mode === "file" && file) form.append("file", file);
    if (mode === "text" && text.trim()) form.append("jd", text.trim());
    try {
      const job = await api.postForm<JobRequest>("/jobs", form);
      navigate(`/jobs/${job.id}`, { replace: true, viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="page page-wide">
      <BackButton fallback="/jobs" label="Job requests" />
      <header className="page-head">
        <div>
          <h1 className="page-title">New job request</h1>
          <p className="page-sub">Name the role and share the job description. MITRA turns it into what the interview will test.</p>
        </div>
      </header>

      <div className="newjob">
        <form className="panel form newjob-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="title">Job title</label>
            <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="AI Engineer, Applied LLM" required autoFocus />
          </div>

          <div className="field">
            <div className="jd-head">
              <span className="label" id="jd-label">
                Job description <span className="muted">(optional)</span>
              </span>
              <div className="segmented" role="group" aria-label="How to give the job description">
                <button type="button" aria-pressed={mode === "file"} onClick={() => setMode("file")}>
                  <FileUp size={14} aria-hidden="true" /> Upload a file
                </button>
                <button type="button" aria-pressed={mode === "text"} onClick={() => setMode("text")}>
                  <ClipboardPaste size={14} aria-hidden="true" /> Type or paste
                </button>
              </div>
            </div>
            {mode === "file" ? (
              <FileDrop file={file} onFile={setFile} labelId="jd-label" />
            ) : (
              <>
                <textarea
                  className="jd-text"
                  aria-labelledby="jd-label"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={MAX_JD}
                  rows={12}
                  placeholder="Paste the job description, or write a few lines: what the role does, what it needs, and what would rule someone out."
                />
                <span className="muted small">
                  {text.length.toLocaleString()} of {MAX_JD.toLocaleString()} characters
                </span>
              </>
            )}
          </div>

          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="actions">
            <Button type="submit" variant="primary" icon={<MessageSquarePlus size={17} />} loading={busy} disabled={!title.trim()}>
              {busy ? "MITRA is reading" : "Start the conversation"}
            </Button>
            <ButtonLink to="/jobs" variant="ghost" icon={<X size={16} />}>
              Cancel
            </ButtonLink>
          </div>
        </form>

        <aside className="newjob-steps" aria-label="What happens next">
          <h2 className="card-title">What happens next</h2>
          <ol>
            {STEPS.map((s) => (
              <li key={s.title}>
                <span className="ns-title">{s.title}</span>
                <span className="ns-text">{s.text}</span>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </div>
  );
}
