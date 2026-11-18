import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CandidateDetail } from "../../contracts";

const ACCEPT = ".pdf,.docx,.txt,.md";

export function AddCandidate() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
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
    if (!name.trim() || !file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("name", name.trim());
    form.append("file", file);
    try {
      const candidate = await api.postForm<CandidateDetail>(`/jobs/${jobId}/candidates`, form);
      navigate(`/candidates/${candidate.id}`, { replace: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="page page-narrow">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to={`/jobs/${jobId}/candidates`}>Candidates</Link>
      </nav>
      <header className="page-head">
        <div>
          <h1 className="page-title">Add a candidate</h1>
          <p className="page-sub">
            Upload their CV. You will see what MITRA read before anything is screened, so you can replace it if the file
            was wrong.
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
              type="file"
              accept={ACCEPT}
              aria-labelledby="cv-label"
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
          <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || !file}>
            {busy ? "Reading the CV…" : "Upload CV"}
          </button>
          <Link to={`/jobs/${jobId}/candidates`} className="btn">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
