import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { JobRequest } from "../../contracts";

const ACCEPT = ".pdf,.docx,.txt,.md";

export function NewJob() {
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function drop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("title", title.trim());
    if (file) form.append("file", file);
    try {
      const job = await api.postForm<JobRequest>("/jobs", form);
      navigate(`/jobs/${job.id}`, { replace: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="page page-narrow">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/jobs">Job requests</Link>
      </nav>
      <header className="page-head">
        <div>
          <h1 className="page-title">New job request</h1>
          <p className="page-sub">
            Give the role a title. A job description helps, but you can also describe the role to MITRA in the chat that
            follows.
          </p>
        </div>
      </header>

      <form className="panel form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="title">Job title</label>
          <input
            id="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
            placeholder="AI Engineer"
            required
            autoFocus
          />
        </div>

        <div className="field">
          <span className="label" id="jd-label">
            Job description <span className="muted">(optional)</span>
          </span>
          <div
            className={over ? "dropzone over" : "dropzone"}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={drop}
          >
            {file ? (
              <p>
                <strong>{file.name}</strong>{" "}
                <button type="button" className="quiet-link" onClick={() => setFile(null)}>
                  Remove
                </button>
              </p>
            ) : (
              <p>
                Drop a PDF, Word or text file here, or{" "}
                <button type="button" className="quiet-link" onClick={() => input.current?.click()}>
                  choose a file
                </button>
              </p>
            )}
            <input
              ref={input}
              id="jd"
              type="file"
              accept={ACCEPT}
              aria-labelledby="jd-label"
              className="visually-hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
        </div>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy || !title.trim()}>
            {busy ? "MITRA is reading…" : "Start the conversation"}
          </button>
          <Link to="/jobs" className="btn">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
