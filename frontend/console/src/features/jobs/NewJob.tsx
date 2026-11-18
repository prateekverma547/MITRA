import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { JobRequest } from "../../contracts";

const ACCEPT = ".pdf,.docx,.txt,.md";

export function NewJob() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    <section className="page narrow">
      <header className="page-header">
        <p className="muted small">
          <Link to="/jobs">Job requests</Link> /
        </p>
        <h1>New job request</h1>
        <p className="muted">
          Give the role a title. A job description helps, but you can also describe the role to MITRA in the chat that
          follows.
        </p>
      </header>

      <form className="card form" onSubmit={submit}>
        <label htmlFor="title">Job title</label>
        <input
          id="title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
          placeholder="e.g. AI Engineer"
          required
          autoFocus
        />

        <label htmlFor="jd">
          Job description <span className="muted">(optional: PDF, Word or text)</span>
        </label>
        <input id="jd" type="file" accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        {file && (
          <p className="muted small">
            {file.name}{" "}
            <button type="button" className="link-button" onClick={() => setFile(null)}>
              Remove
            </button>
          </p>
        )}

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <button type="submit" className="primary" disabled={busy || !title.trim()}>
            {busy ? "MITRA is reading…" : "Start"}
          </button>
          <Link to="/jobs" className="button">
            Cancel
          </Link>
        </div>
      </form>
    </section>
  );
}
