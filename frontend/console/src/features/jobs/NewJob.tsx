import { MessageSquarePlus, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { JobRequest } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { FileDrop } from "../../shell/ui/FileDrop";

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
      navigate(`/jobs/${job.id}`, { replace: true, viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="page page-narrow">
      <BackButton fallback="/jobs" label="Job requests" />
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
          <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="AI Engineer" required autoFocus />
        </div>
        <div className="field">
          <span className="label" id="jd-label">
            Job description <span className="muted">(optional)</span>
          </span>
          <FileDrop file={file} onFile={setFile} labelId="jd-label" />
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
    </div>
  );
}
