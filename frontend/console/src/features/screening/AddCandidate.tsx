import { Upload, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CandidateDetail } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { FileDrop } from "../../shell/ui/FileDrop";

export function AddCandidate() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("name", name.trim());
    form.append("file", file);
    try {
      const candidate = await api.postForm<CandidateDetail>(`/jobs/${jobId}/candidates`, form);
      navigate(`/candidates/${candidate.id}`, { replace: true, viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="page page-narrow">
      <BackButton fallback={`/jobs/${jobId}/candidates`} label="Candidates" />
      <header className="page-head">
        <div>
          <h1 className="page-title">Add a candidate</h1>
          <p className="page-sub">
            Upload their CV. You will see what MITRA read before anything is screened, so you can replace it if the file was
            wrong.
          </p>
        </div>
      </header>

      <form className="panel form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="name">Candidate's name</label>
          <input id="name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required autoFocus />
          <span className="hint">Used to find them in your lists. It is removed from the CV before screening.</span>
        </div>
        <div className="field">
          <span className="label" id="cv-label">
            CV
          </span>
          <FileDrop file={file} onFile={setFile} labelId="cv-label" />
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <Button type="submit" variant="primary" icon={<Upload size={17} />} loading={busy} disabled={!name.trim() || !file}>
            {busy ? "Reading the CV" : "Upload CV"}
          </Button>
          <ButtonLink to={`/jobs/${jobId}/candidates`} variant="ghost" icon={<X size={16} />}>
            Cancel
          </ButtonLink>
        </div>
      </form>
    </div>
  );
}
