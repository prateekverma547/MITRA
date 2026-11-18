// One job request: the conversation with MITRA, and once it is finished, what
// MITRA understood, with Edit and Approve. Everything the BU head may do comes
// from the server (`can_chat`, `can_edit`, `can_approve`), so HR and admins
// see the same page read-only.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import { requestPhase, type JobRequest, type Understanding } from "../../contracts";

function Chat({ job, onUpdate }: { job: JobRequest; onUpdate: (job: JobRequest) => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [job.chat.length]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.post<JobRequest>(`/jobs/${job.id}/chat`, { message: text }));
      setMessage("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card chat" aria-label="Conversation with MITRA">
      <ol className="turns">
        {job.chat.map((turn, i) => (
          <li key={i} className={`turn turn-${turn.from}`}>
            <span className="turn-who">{turn.from === "mitra" ? "MITRA" : "You"}</span>
            <p>{turn.text}</p>
          </li>
        ))}
        {busy && (
          <li className="turn turn-mitra" aria-live="polite">
            <span className="turn-who">MITRA</span>
            <p className="muted">Thinking…</p>
          </li>
        )}
      </ol>
      <div ref={endRef} />
      {job.can_chat && (
        <form className="reply" onSubmit={send}>
          <label htmlFor="reply" className="visually-hidden">
            Your reply
          </label>
          <textarea
            id="reply"
            rows={3}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Reply to MITRA. Enter sends, Shift+Enter for a new line."
            disabled={busy}
          />
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="primary" disabled={busy || !message.trim()}>
            Send
          </button>
        </form>
      )}
    </section>
  );
}

function UnderstandingCard({ u, inferred }: { u: Understanding; inferred: string[] }) {
  const total = u.competencies.reduce((sum, c) => sum + c.weight, 0) || 1;
  return (
    <section className="card understanding" aria-labelledby="understood">
      <h2 id="understood">What MITRA understood</h2>
      <dl className="facts">
        <div>
          <dt>Role</dt>
          <dd>{u.role_title}</dd>
        </div>
        <div>
          <dt>Seniority</dt>
          <dd>{u.seniority}</dd>
        </div>
        <div>
          <dt>Experience</dt>
          <dd>{u.experience_expectation}</dd>
        </div>
        <div>
          <dt>Interview length</dt>
          <dd>{u.duration_minutes} minutes</dd>
        </div>
      </dl>

      {inferred.length > 0 && (
        <div className="assumed" role="note">
          <strong>MITRA assumed these. You did not say them.</strong>
          <ul>
            {inferred.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
          <p className="small">If any is wrong, choose Edit and tell MITRA.</p>
        </div>
      )}

      <h3>What the interview will test</h3>
      <ul className="competencies">
        {u.competencies.map((c) => {
          const share = Math.round((c.weight / total) * 100);
          return (
            <li key={c.id}>
              <div className="competency-head">
                <span className="competency-name">{c.name}</span>
                <span className="competency-weight">{share}%</span>
              </div>
              <div className="weight-bar" aria-hidden="true">
                <span style={{ width: `${share}%` }} />
              </div>
              <p className="muted small">{c.description}</p>
            </li>
          );
        })}
      </ul>

      {u.red_flags.length > 0 && (
        <>
          <h3>Dealbreakers</h3>
          <ul className="flags">
            {u.red_flags.map((flag, i) => (
              <li key={i}>{flag}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function JobDetail() {
  const { jobId } = useParams();
  const [job, setJob] = useState<JobRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<JobRequest>(`/jobs/${jobId}`)
      .then(setJob)
      .catch((e: Error) => setError(e.message));
  }, [jobId]);

  async function act(path: "edit" | "approve") {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await api.post<JobRequest>(`/jobs/${job.id}/${path}`));
      setConfirming(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !job) {
    return (
      <section className="page">
        <p className="muted small">
          <Link to="/jobs">Job requests</Link> /
        </p>
        <p className="error">{error}</p>
      </section>
    );
  }
  if (!job) return <section className="page muted">Loading…</section>;

  const phase = requestPhase(job);
  const describing = job.spec_status !== "ready";

  return (
    <section className="page">
      <header className="page-header">
        <p className="muted small">
          <Link to="/jobs">Job requests</Link> /
        </p>
        <div className="row">
          <div>
            <h1>{job.title}</h1>
            <p className="muted">
              {job.bu_name} business unit.{job.created_by ? ` Started by ${job.created_by}.` : ""}{" "}
              {job.source_filename ? `Job description: ${job.source_filename}.` : "No job description uploaded."}
            </p>
          </div>
          <span className={`chip tone-${phase.tone}`}>{phase.label}</span>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      {job.request_status === "open" && !describing && (
        <p className="banner good">This request is with HR. They can now add candidates to it.</p>
      )}
      {job.request_status === "open" && describing && (
        <p className="banner attention">
          You are revising a request HR already has. When MITRA summarises it again, candidates who have not been
          interviewed get an updated plan; anyone already interviewed keeps theirs.
        </p>
      )}

      <div className={describing ? "split" : "split understood"}>
        {job.understanding && <UnderstandingCard u={job.understanding} inferred={job.inferred} />}
        {(describing || job.chat.length > 0) && (
          <div className="chat-column">
            {!describing && <h2 className="small-heading">The conversation</h2>}
            <Chat job={job} onUpdate={setJob} />
          </div>
        )}
      </div>

      {(job.can_edit || job.can_approve) && (
        <div className="actions sticky-actions">
          {job.can_approve && !confirming && (
            <button type="button" className="primary" disabled={busy} onClick={() => setConfirming(true)}>
              Approve and send to HR
            </button>
          )}
          {confirming && (
            <span className="confirm">
              Send “{job.title}” to HR?{" "}
              <button type="button" className="primary" disabled={busy} onClick={() => act("approve")}>
                Yes, send it
              </button>{" "}
              <button type="button" className="button" disabled={busy} onClick={() => setConfirming(false)}>
                Not yet
              </button>
            </span>
          )}
          {job.can_edit && !confirming && (
            <button type="button" className="button" disabled={busy} onClick={() => act("edit")}>
              Edit
            </button>
          )}
        </div>
      )}
    </section>
  );
}
