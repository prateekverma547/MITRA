// One job request: the conversation with MITRA, and once it is finished, what
// MITRA understood, with Edit and Approve. Everything the BU head may do comes
// from the server (`can_chat`, `can_edit`, `can_approve`), so HR and admins
// see the same page read-only.

import { CheckCheck, GitCompareArrows, PencilLine, SendHorizontal, Trash2, Undo2, Users, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import "./jobs.css";
import { requestPhase, type JobRequest, type Understanding } from "../../contracts";
import { BackButton } from "../../shell/ui/BackButton";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { useToast } from "../../shell/ui/Toast";

function Conversation({ job, onUpdate }: { job: JobRequest; onUpdate: (job: JobRequest) => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const firstCount = useRef(job.chat.length);
  const endRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [job.chat.length, busy]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    // Show their reply at once, while MITRA thinks about it.
    setPending(text);
    setMessage("");
    try {
      onUpdate(await api.post<JobRequest>(`/jobs/${job.id}/chat`, { message: text }));
    } catch (e) {
      setError((e as Error).message);
      setMessage(text);
    } finally {
      setPending(null);
      setBusy(false);
    }
  }

  return (
    <section className="conversation" aria-label="Conversation with MITRA">
      <ol className="transcript">
        {job.chat.map((turn, i) => (
          <li key={i} className={`line line-${turn.from}${i >= firstCount.current ? " line-enter" : ""}`}>
            <span className="line-who">{turn.from === "mitra" ? "MITRA" : "You"}</span>
            <p>{turn.text}</p>
          </li>
        ))}
        {pending && (
          <li className="line line-you line-enter">
            <span className="line-who">You</span>
            <p>{pending}</p>
          </li>
        )}
        {busy && (
          <li className="line line-mitra line-enter" aria-live="polite">
            <span className="line-who">MITRA</span>
            <span className="dots" aria-label="MITRA is thinking">
              <span />
              <span />
              <span />
            </span>
          </li>
        )}
        <li ref={endRef} aria-hidden="true" className="transcript-end" />
      </ol>
      {job.can_chat && (
        <form className="composer" onSubmit={send}>
          <label htmlFor="reply" className="visually-hidden">
            Your reply to MITRA
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
            placeholder="Reply to MITRA"
            disabled={busy}
          />
          <div className="composer-foot">
            <span className="muted small">Enter sends. Shift and Enter starts a new line.</span>
            <Button type="submit" variant="primary" icon={<SendHorizontal size={16} />} loading={busy} disabled={!message.trim()}>
              Send
            </Button>
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </section>
  );
}

function Brief({ u, inferred, live }: { u: Understanding; inferred: string[]; live: boolean }) {
  const sum = u.competencies.reduce((s, c) => s + c.weight, 0) || 1;
  return (
    <article className={live ? "brief live-border" : "brief"} aria-labelledby="understood">
      <h2 id="understood" className="brief-title">
        What MITRA understood
      </h2>
      <dl className="brief-facts">
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
          <dt>Interview</dt>
          <dd>{u.duration_minutes} minutes</dd>
        </div>
      </dl>

      {inferred.length > 0 && (
        <div className="assumed" role="note">
          <p className="assumed-head">You did not tell MITRA these. It assumed them.</p>
          <ul>
            {inferred.map((item, i) => (
              <li key={i}>
                <mark>{item}</mark>
              </li>
            ))}
          </ul>
          <p className="muted small">If any is wrong, choose Edit and say so.</p>
        </div>
      )}

      <h3 className="brief-sub">What the interview will test</h3>
      <ol className="weights">
        {u.competencies.map((c) => {
          const share = Math.round((c.weight / sum) * 100);
          return (
            <li key={c.id}>
              <div className="weight-head">
                <span className="weight-name">{c.name}</span>
                <span className="weight-value">{share}%</span>
              </div>
              <div className="weight-track" aria-hidden="true">
                <span style={{ width: `${share}%` }} />
              </div>
              <p className="weight-desc">{c.description}</p>
            </li>
          );
        })}
      </ol>

      {u.red_flags.length > 0 && (
        <>
          <h3 className="brief-sub">Dealbreakers</h3>
          <ul className="dealbreakers">
            {u.red_flags.map((flag, i) => (
              <li key={i}>{flag}</li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

export function JobDetail() {
  const { jobId } = useParams();
  const [job, setJob] = useState<JobRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"edit" | "approve" | "keep" | null>(null);
  const toast = useToast();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState<"ask" | "busy" | null>(null);

  async function remove() {
    if (!job) return;
    setDeleting("busy");
    setError(null);
    try {
      await api.del(`/jobs/${job.id}`);
      toast(`Deleted “${job.title}”`, "urgent");
      navigate("/jobs", { replace: true, viewTransition: true });
    } catch (e) {
      setError((e as Error).message);
      setDeleting(null);
    }
  }

  useEffect(() => {
    api
      .get<JobRequest>(`/jobs/${jobId}`)
      .then(setJob)
      .catch((e: Error) => setError(e.message));
  }, [jobId]);

  // When MITRA finishes, the summary and its buttons replace the chat at the
  // top of the page: bring them into view instead of leaving you scrolled
  // down at the end of a conversation that is no longer there.
  const wasDescribing = useRef<boolean | null>(null);
  useEffect(() => {
    if (!job) return;
    const now = job.spec_status !== "ready";
    if (wasDescribing.current === true && !now) window.scrollTo({ top: 0, behavior: "smooth" });
    wasDescribing.current = now;
  }, [job]);

  async function act(path: "edit" | "approve" | "keep") {
    if (!job) return;
    setBusy(path);
    setError(null);
    try {
      setJob(await api.post<JobRequest>(`/jobs/${job.id}/${path}`));
      setConfirming(false);
      toast(
        path === "approve"
          ? `Sent “${job.title}” to HR`
          : path === "keep"
            ? "Kept the summary as it was"
            : "Reopened the conversation with MITRA. Leave any time with “Keep it as it was”.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const crumbs = <BackButton fallback="/jobs" label="Job requests" />;

  if (error && !job) {
    return (
      <div className="page">
        {crumbs}
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!job) return <PageSkeleton rows={3} />;

  const phase = requestPhase(job);
  const describing = job.spec_status !== "ready";
  const source = job.source_filename ? `From ${job.source_filename}` : "No job description uploaded";

  return (
    <div className="page page-wide">
      {crumbs}
      <header className="page-head">
        <div>
          <h1 className="page-title">{job.title}</h1>
          <p className="page-sub">
            {job.bu_name}
            {job.created_by ? `, started by ${job.created_by}` : ""}. {source}.
          </p>
        </div>
        <div className="job-head-side">
          <span className={`mark mark-lg tone-${phase.tone}`}>{phase.label}</span>
          {job.can_delete &&
            (deleting ? (
              <span className="delete-confirm" role="group" aria-label="Delete this request">
                <span>Delete “{job.title}”? This cannot be undone.</span>
                <Button size="sm" variant="danger" icon={<Trash2 size={15} />} loading={deleting === "busy"} onClick={remove}>
                  Delete
                </Button>
                <Button size="sm" variant="ghost" disabled={deleting === "busy"} onClick={() => setDeleting(null)}>
                  Keep it
                </Button>
              </span>
            ) : (
              <Button size="sm" variant="ghost" icon={<Trash2 size={15} />} onClick={() => setDeleting("ask")}>
                Delete this request
              </Button>
            ))}
        </div>
      </header>

      {error && <p className="error">{error}</p>}
      {job.request_status === "open" && describing && (
        <p className="notice notice-attention">
          You are revising a request HR already has. When MITRA summarises it again, candidates who have not been
          interviewed get an updated plan. Anyone already interviewed keeps theirs.
        </p>
      )}

      {describing ? (
        <div className="describe">
          <Conversation job={job} onUpdate={setJob} />
          <aside className="describe-aside">
            <h2 className="aside-title">What happens next</h2>
            <p>MITRA asks one question at a time. When it has enough, it summarises the role for you to confirm.</p>
            <p>Anything it assumes rather than hears from you is marked, so you can correct it before HR sees it.</p>
            {job.can_keep && (
              <div className="keep-box">
                <p>Changed your mind? Leave the chat and keep the summary you agreed before. Nothing changes.</p>
                <Button icon={<Undo2 size={16} />} loading={busy === "keep"} onClick={() => act("keep")}>
                  Keep it as it was
                </Button>
              </div>
            )}
          </aside>
        </div>
      ) : (
        <div className="understood">
          {job.understanding && <Brief u={job.understanding} inferred={job.inferred} live={job.can_approve} />}
          <aside className="job-side">
            <section className="card-viz">
              <h2 className="card-title">Where it stands</h2>
              <p className="side-text">
                {job.request_status === "open"
                  ? `With HR. ${job.candidates === 0 ? "No candidates yet." : `${job.candidates} ${job.candidates === 1 ? "candidate" : "candidates"} so far.`}`
                  : job.can_approve
                    ? "MITRA has summarised the role. Check it, then send it to HR."
                    : "Being described with MITRA."}
              </p>
              {job.request_status === "open" && (
                <div className="actions">
                  <ButtonLink size="sm" variant="primary" to={`/jobs/${job.id}/candidates`} icon={<Users size={15} />}>
                    {job.candidates === 0 ? "Open candidates" : `See ${job.candidates} ${job.candidates === 1 ? "candidate" : "candidates"}`}
                  </ButtonLink>
                  {job.candidates > 1 && (
                    <ButtonLink size="sm" variant="ghost" to={`/jobs/${job.id}/compare`} icon={<GitCompareArrows size={15} />}>
                      Compare them
                    </ButtonLink>
                  )}
                </div>
              )}
              {(job.can_edit || job.can_approve) && (
                <div className="actions">
                  {confirming ? (
                    <>
                      <span className="decision-q">Send “{job.title}” to HR?</span>
                      <Button variant="primary" icon={<CheckCheck size={17} />} loading={busy === "approve"} onClick={() => act("approve")}>
                        Send to HR
                      </Button>
                      <Button variant="ghost" icon={<X size={16} />} disabled={busy !== null} onClick={() => setConfirming(false)}>
                        Not yet
                      </Button>
                    </>
                  ) : (
                    <>
                      {job.can_approve && (
                        <Button variant="primary" icon={<CheckCheck size={17} />} disabled={busy !== null} onClick={() => setConfirming(true)}>
                          Approve and send to HR
                        </Button>
                      )}
                      {job.can_edit && (
                        <Button icon={<PencilLine size={16} />} loading={busy === "edit"} onClick={() => act("edit")}>
                          Edit with MITRA
                        </Button>
                      )}
                    </>
                  )}
                </div>
              )}
            </section>
            <details className="conversation-record">
              <summary>The conversation with MITRA ({job.chat.length} messages)</summary>
              <Conversation job={job} onUpdate={setJob} />
            </details>
          </aside>
        </div>
      )}

      {describing && (job.can_edit || job.can_approve) && (
        <div className="decision-bar">
          {confirming ? (
            <>
              <span className="decision-q">Send “{job.title}” to HR?</span>
              <Button variant="primary" icon={<CheckCheck size={17} />} loading={busy === "approve"} onClick={() => act("approve")}>
                Send to HR
              </Button>
              <Button variant="ghost" icon={<X size={16} />} disabled={busy !== null} onClick={() => setConfirming(false)}>
                Not yet
              </Button>
            </>
          ) : (
            <>
              {job.can_approve && (
                <Button variant="primary" icon={<CheckCheck size={17} />} disabled={busy !== null} onClick={() => setConfirming(true)}>
                  Approve and send to HR
                </Button>
              )}
              {job.can_edit && (
                <Button icon={<PencilLine size={16} />} loading={busy === "edit"} onClick={() => act("edit")}>
                  Edit with MITRA
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
