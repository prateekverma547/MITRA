// Add a candidate: name and CV, then (on the same page) Proceed to screening or
// Re-upload. Proceeding opens the candidate's page with screening under way.

import { FileCheck2, ScanSearch, Upload, X } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CandidateDetail } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { FileDrop } from "../../shell/ui/FileDrop";
import { useToast } from "../../shell/ui/Toast";
import "./candidate.css";

export function ScreeningSteps() {
  return (
    <ol className="cv-ready-steps" aria-label="What screening does">
      <li>
        <span>1</span>
        <div>
          <strong>Hides who they are</strong>
          <p>Name, contact details, photo and institutions are removed before MITRA reads it.</p>
        </div>
      </li>
      <li>
        <span>2</span>
        <div>
          <strong>Reads it against the job</strong>
          <p>Each part of what the BU head asked for is rated from what the CV shows, with the lines it rests on.</p>
        </div>
      </li>
      <li>
        <span>3</span>
        <div>
          <strong>Writes the HR questions</strong>
          <p>Ten questions for the HR call, personal to this candidate's path.</p>
        </div>
      </li>
    </ol>
  );
}

export function AddCandidate() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [made, setMade] = useState<CandidateDetail | null>(null);
  const [busy, setBusy] = useState<"upload" | "replace" | "proceed" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const replaceInput = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !file) return;
    setBusy("upload");
    setError(null);
    const form = new FormData();
    form.append("name", name.trim());
    form.append("file", file);
    try {
      setMade(await api.postForm<CandidateDetail>(`/jobs/${jobId}/candidates`, form));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function replace(next: File) {
    if (!made) return;
    setBusy("replace");
    setError(null);
    const form = new FormData();
    form.append("file", next);
    try {
      setMade(await api.postForm<CandidateDetail>(`/candidates/${made.id}/cv`, form));
      toast(`Replaced the CV with ${next.name}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function proceed() {
    if (!made) return;
    setBusy("proceed");
    setError(null);
    try {
      await api.post<CandidateDetail>(`/candidates/${made.id}/proceed`);
      navigate(`/candidates/${made.id}`, { replace: true, viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  const first = made ? made.name.split(/\s+/)[0] || made.name : "";

  return (
    <div className="page page-wide">
      <BackButton fallback={`/jobs/${jobId}/candidates`} label="Candidates" />
      <header className="page-head">
        <div>
          <h1 className="page-title">Add a candidate</h1>
          <p className="page-sub">Upload their CV, then send it to screening. Nothing is screened until you choose Proceed.</p>
        </div>
      </header>

      <div className="add-cand">
        {made ? (
          <section className="add-cand-card done" aria-live="polite">
            <span className="cv-ready-icon" aria-hidden="true">
              <FileCheck2 size={40} strokeWidth={1.6} />
            </span>
            <h2 className="cv-ready-title">{first}'s résumé is uploaded</h2>
            <p className="cv-ready-file">{made.source_filename ?? "CV"}</p>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <div className="cv-ready-actions">
              <Button variant="primary" icon={<ScanSearch size={18} />} loading={busy === "proceed"} disabled={busy !== null} onClick={proceed}>
                Proceed to screening
              </Button>
              <Button icon={<Upload size={17} />} loading={busy === "replace"} disabled={busy !== null} onClick={() => replaceInput.current?.click()}>
                Re-upload
              </Button>
              <input
                ref={replaceInput}
                type="file"
                accept=".pdf,.docx,.txt,.md"
                className="visually-hidden"
                aria-label="Replacement CV"
                onChange={(e) => e.target.files?.[0] && replace(e.target.files[0])}
              />
            </div>
          </section>
        ) : (
          <form className="add-cand-card" onSubmit={submit}>
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
              <Button type="submit" variant="primary" icon={<Upload size={17} />} loading={busy === "upload"} disabled={!name.trim() || !file}>
                {busy === "upload" ? "Uploading" : "Upload CV"}
              </Button>
              <ButtonLink to={`/jobs/${jobId}/candidates`} variant="ghost" icon={<X size={16} />}>
                Cancel
              </ButtonLink>
            </div>
          </form>
        )}
        <aside className="add-cand-side">
          <h2 className="section-title">What screening does</h2>
          <ScreeningSteps />
        </aside>
      </div>
    </div>
  );
}
