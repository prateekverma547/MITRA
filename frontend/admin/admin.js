// Admin panel.
//
//   Login
//     └─ #/profiles                     a profile is a role you are hiring for
//          └─ #/profiles/<id>
//               ├─ what it tests        the locked evaluation spec
//               ├─ discussion           collapsed once it is settled
//               └─ candidates           each with where their interview stands
//                    └─ #/candidates/<id>
//                         ├─ the session   at most one open at a time
//                         ├─ the plan
//                         └─ improve the plan
//                              └─ #/interviews/<id>
//
// Navigation is real: every screen has its own URL in the hash, so the back
// button, forward, refresh and bookmarks all behave. Screens are reached with
// ordinary <a href> links rather than click handlers, which is what makes
// middle-click and copy-link-address work too.
//
// Plain JavaScript, no build step. CLAUDE.md specifies React + Vite, which is
// still right once this settles; it talks to the real API, so a rewrite swaps
// the view layer and nothing else.

const app = document.getElementById("app");

// ---------------------------------------------------------------- utilities

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401 && !path.startsWith("/admin/")) {
    renderLogin();
    throw new Error("Signed out.");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} on ${path}`);
  return body;
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const when = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });

const LABELS = {
  not_scheduled: "no session",
  in_progress: "in progress",
  awaiting_clarification: "being defined",
};

function pill(status, label) {
  const good = ["ready", "completed", "sufficient"];
  const bad = ["failed", "insufficient", "expired", "not_started"];
  const cls = good.includes(status) ? "ok" : bad.includes(status) ? "bad" : "warn";
  const text = label || LABELS[status] || String(status).replace(/_/g, " ");
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

function crumbs(parts) {
  return `<div class="crumb">${parts
    .map((p) => (p.href ? `<a href="${p.href}">${esc(p.label)}</a>` : esc(p.label)))
    .join(" › ")}</div>`;
}

function chat(turns) {
  return (turns || [])
    .map((t) => `<div class="msg ${t.role === "assistant" ? "ai" : "me"}">${esc(t.content)}</div>`)
    .join("");
}

/** Name a profile the way the employer will recognise it. */
function profileName(job) {
  return job.title || job.role_title || job.source_filename || "Untitled profile";
}

// -- polling ----------------------------------------------------------------
//
// Several screens re-render themselves while work finishes. Each render takes a
// ticket; a scheduled re-render only fires if its ticket is still the current
// one. Without this, navigating away from a waiting screen leaves its timer
// running, and it redraws itself over whatever you opened next.

let ticket = 0;

function poll(ms) {
  const mine = ticket;
  setTimeout(() => { if (mine === ticket) render(); }, ms);
}

// ------------------------------------------------------------------- router

function route() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [section, id, sub] = raw.split("/");
  return { section: section || "profiles", id, sub };
}

function go(path) {
  location.hash = path;  // fires hashchange -> render
}

// ------------------------------------------------------------------- login

function renderLogin() {
  app.innerHTML = `
    <div class="card" style="max-width:420px;margin:60px auto">
      <h2>Sign in</h2>
      <p class="sub">This panel shows every job description, CV and interview
        transcript. It is not for candidates.</p>
      <input type="password" id="pw" placeholder="Admin password" autofocus />
      <button class="primary" id="in" style="width:100%;margin-top:16px">Sign in</button>
      <div class="err hidden" id="err"></div>
    </div>`;

  const submit = async () => {
    const button = document.getElementById("in");
    button.disabled = true;
    try {
      await api("/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: document.getElementById("pw").value }),
      });
      render();
    } catch (e) {
      button.disabled = false;
      const err = document.getElementById("err");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };
  document.getElementById("in").onclick = submit;
  document.getElementById("pw").onkeydown = (e) => { if (e.key === "Enter") submit(); };
}

// ---------------------------------------------------------------- profiles

async function viewProfiles() {
  const profiles = await api("/jobs");
  app.innerHTML = `
    <div class="spread">
      <div><h2>Profiles</h2><p class="sub">One profile per role you are hiring for.</p></div>
      <div class="row">
        <button onclick="signOut()">Sign out</button>
        <a class="btn primary" href="#/profiles/new">New profile</a>
      </div>
    </div>
    ${profiles.length === 0 ? `<div class="card muted">
      No profiles yet. Create one by uploading a job description.</div>` : ""}
    ${profiles.map((p) => `
      <div class="list-item">
        <div class="spread">
          <a href="#/profiles/${p.job_id}" style="flex:1;text-decoration:none;color:inherit">
            <strong>${esc(profileName(p))}</strong>
            ${p.business_unit ? `<span class="tag">${esc(p.business_unit)}</span>` : ""}
            <div class="small muted">
              ${p.candidate_count} candidate${p.candidate_count === 1 ? "" : "s"}
              ${p.candidate_count ? ` · ${p.interviewed_count} interviewed` : ""}
              ${p.stale_count ? ` · <span class="warntext">${p.stale_count} plan${
                p.stale_count === 1 ? "" : "s"} out of date</span>` : ""}
              · ${when(p.created_at)}
            </div>
          </a>
          <div class="row">
            ${pill(p.spec_status)}
            <button class="danger" onclick="removeProfile('${p.job_id}')">Delete</button>
          </div>
        </div>
      </div>`).join("")}
  `;
}

window.removeProfile = async (jobId) => {
  if (!confirm(
    "Delete this profile and everything under it — CVs, interview plans and " +
    "transcripts?\n\nThis cannot be undone."
  )) return;
  await api(`/jobs/${jobId}`, { method: "DELETE" });
  render();
};

function viewNewProfile() {
  app.innerHTML = `
    ${crumbs([{ label: "Profiles", href: "#/profiles" }, { label: "New profile" }])}
    <div class="card">
      <h2>New profile</h2>
      <p class="sub">Name it, then upload the job description. Mitra reads the JD
        and asks you a few questions to work out what the interview should test.</p>

      <label class="lbl">What are you hiring for?</label>
      <input type="text" id="title" placeholder="Business Analyst" autofocus />

      <label class="lbl">Team or business unit <span class="muted">(optional)</span></label>
      <input type="text" id="bu" placeholder="Payments" />
      <div class="small muted" style="margin-top:5px">Tells two openings for the
        same role apart.</div>

      <label class="lbl">Job description</label>
      <input type="file" id="jd" accept=".pdf,.docx,.txt,.md" />

      <div class="row" style="margin-top:18px">
        <button class="primary" id="up">Create profile</button>
        <span class="muted small" id="status"></span>
      </div>
      <div class="err hidden" id="err"></div>
    </div>`;

  document.getElementById("up").onclick = async () => {
    const file = document.getElementById("jd").files[0];
    const err = document.getElementById("err");
    if (!file) {
      err.textContent = "Choose a job description file first.";
      err.classList.remove("hidden");
      return;
    }
    document.getElementById("up").disabled = true;
    document.getElementById("status").textContent = "Reading the job description…";
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("title", document.getElementById("title").value);
      form.append("business_unit", document.getElementById("bu").value);
      const created = await api("/jobs", { method: "POST", body: form });
      go(`/profiles/${created.job_id}`);
    } catch (e) {
      document.getElementById("up").disabled = false;
      document.getElementById("status").textContent = "";
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };
}

// ----------------------------------------------------------------- profile

async function viewProfile(jobId) {
  const [job, candidates] = await Promise.all([
    api(`/jobs/${jobId}`),
    api(`/jobs/${jobId}/candidates`).catch(() => []),
  ]);
  const ready = job.spec_status === "ready";
  const spec = job.evaluation_spec;
  const revising = !ready && spec;  // reopened: a spec exists but is being changed

  app.innerHTML = `
    ${crumbs([{ label: "Profiles", href: "#/profiles" }, { label: profileName(job) }])}

    <div class="card">
      <div class="spread">
        <div>
          <h2 style="margin:0">${esc(profileName(job))}</h2>
          <div class="small muted" style="margin-top:5px">
            ${job.business_unit ? `<span class="tag">${esc(job.business_unit)}</span>` : ""}
            ${spec?.role_title && spec.role_title !== job.title
              ? esc(spec.role_title) : ""}
            ${job.spec_version > 1 ? ` · revision ${job.spec_version}` : ""}
          </div>
        </div>
        ${pill(job.spec_status)}
      </div>
    </div>

    ${ready ? specCard(spec, jobId) : ""}

    <div class="card">
      <details ${ready ? "" : "open"}>
        <summary class="disclosure">
          <strong>Discussion with Mitra</strong>
          <span class="muted small">${(job.clarification || []).length} messages${
            ready ? " · settled" : ""}</span>
        </summary>
        <div style="margin-top:14px">
          ${revising ? `<div class="note">You are changing what this profile
            tests. When you confirm the new summary, plans for candidates who
            have not been interviewed will be rebuilt.</div>` : ""}
          <div class="chat">${chat(job.clarification)}</div>
          ${ready ? "" : `
            <textarea id="reply" placeholder="Your answer…"></textarea>
            <div class="row" style="margin-top:10px">
              <button class="primary" id="send">Send</button>
              <span class="muted small" id="status"></span>
            </div>
            <div class="err hidden" id="err"></div>`}
        </div>
      </details>
    </div>

    ${ready ? cvsCard(candidates) : ""}
  `;

  if (!ready) {
    document.getElementById("send").onclick = async () => {
      const text = document.getElementById("reply").value.trim();
      if (!text) return;
      document.getElementById("send").disabled = true;
      document.getElementById("status").textContent = "Thinking…";
      try {
        const reply = await api(`/jobs/${jobId}/clarify`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        if (reply.propagation) announcePropagation(reply.propagation);
        render();
      } catch (e) {
        document.getElementById("send").disabled = false;
        document.getElementById("status").textContent = "";
        const err = document.getElementById("err");
        err.textContent = e.message;
        err.classList.remove("hidden");
      }
    };
  } else {
    wireCvUpload(jobId);
  }
}

/** Say plainly which candidates a spec change reached, and which it did not. */
function announcePropagation(p) {
  const lines = [];
  if (p.regenerated.length) lines.push(`${p.regenerated.length} interview plan(s) rebuilt.`);
  if (p.skipped_refined.length) {
    lines.push(
      `${p.skipped_refined.length} plan(s) left alone because you had edited them ` +
      `by hand — they are marked out of date, and you can rebuild them yourself.`
    );
  }
  if (p.skipped_interviewed.length) {
    lines.push(
      `${p.skipped_interviewed.length} candidate(s) already interviewed were not ` +
      `touched. Their plan is the record of what they were actually asked.`
    );
  }
  if (lines.length) alert(lines.join("\n\n"));
}

function specCard(spec, jobId) {
  const total = spec.competencies.reduce((a, c) => a + c.weight, 0) || 1;
  return `
    <div class="card">
      <div class="spread">
        <h3 style="margin:0">What this interview tests</h3>
        <button onclick="reopenSpec('${jobId}')">Change this</button>
      </div>
      <div class="kv" style="margin-top:14px">
        <div>Seniority</div><div>${esc(spec.seniority)}</div>
        <div>Experience</div><div>${esc(spec.experience_expectation)}</div>
        <div>Length</div><div>${spec.duration_minutes} min</div>
      </div>
      <h3>Competencies</h3>
      ${spec.competencies.map((c) => `
        <div style="margin-bottom:12px">
          <div class="spread">
            <strong>${esc(c.name)}</strong>
            <span class="muted small">${(c.weight * 100).toFixed(0)}%</span>
          </div>
          <div class="bar" style="width:${(c.weight / total) * 100}%;margin:5px 0"></div>
          <div class="small muted">${esc(c.description)}</div>
        </div>`).join("")}
      ${spec.red_flags?.length ? `<h3>Dealbreakers</h3>
        <ul class="small muted" style="margin:0;padding-left:19px">
          ${spec.red_flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    </div>`;
}

window.reopenSpec = async (jobId) => {
  if (!confirm(
    "Reopen the discussion to change what this interview tests?\n\n" +
    "When you confirm a new summary, plans are rebuilt for candidates who have " +
    "not been interviewed yet. Candidates you have already interviewed, and " +
    "plans you edited by hand, are left as they are."
  )) return;
  await api(`/jobs/${jobId}/reopen`, { method: "POST" });
  render();
};

function cvsCard(candidates) {
  return `
    <div class="card">
      <h3 style="margin-top:0">Candidates</h3>
      <p class="sub" style="margin-top:6px">Upload a CV. An interview plan is
        built for that person straight away.</p>
      <input type="file" id="cv" accept=".pdf,.docx,.txt,.md" />
      <div class="row" style="margin:14px 0">
        <button class="primary" id="upcv">Upload CV</button>
        <span class="muted small" id="cvstatus"></span>
      </div>
      <div class="err hidden" id="cverr"></div>
      ${candidates.length === 0 ? `<div class="muted small">Nobody yet.</div>` : ""}
      ${candidates.map((c) => `
        <a class="list-item" href="#/candidates/${c.candidate_id}"
           style="display:block;text-decoration:none;color:inherit">
          <div class="spread">
            <div>
              <strong>${esc(c.name || c.source_filename || "Candidate")}</strong>
              ${c.plan_is_stale ? `<span class="tag warn">plan out of date</span>` : ""}
              <div class="small muted">${when(c.created_at)}</div>
              ${c.blueprint_error ? `<div class="small err">${esc(c.blueprint_error)}</div>` : ""}
            </div>
            <div class="row">
              ${c.blueprint_status === "ready"
                ? pill(c.interview_status)
                : pill(c.blueprint_status, `plan ${c.blueprint_status}`)}
            </div>
          </div>
        </a>`).join("")}
    </div>`;
}

function wireCvUpload(jobId) {
  const button = document.getElementById("upcv");
  if (!button) return;
  button.onclick = async () => {
    const file = document.getElementById("cv").files[0];
    if (!file) return;
    button.disabled = true;
    document.getElementById("cvstatus").textContent = "Reading the CV and building the plan…";
    try {
      const form = new FormData();
      form.append("file", file);
      await api(`/jobs/${jobId}/candidates`, { method: "POST", body: form });
      poll(1500);
    } catch (e) {
      button.disabled = false;
      document.getElementById("cvstatus").textContent = "";
      const err = document.getElementById("cverr");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };
}

// --------------------------------------------------- candidate + the plan

async function viewCandidate(candidateId) {
  const [candidate, interviews] = await Promise.all([
    api(`/candidates/${candidateId}`),
    api(`/candidates/${candidateId}/interviews`),
  ]);

  const backToProfile = `#/profiles/${candidate.job_id}`;

  if (candidate.blueprint_status !== "ready") {
    app.innerHTML = `
      ${crumbs([
        { label: "Profiles", href: "#/profiles" },
        { label: "Profile", href: backToProfile },
        { label: "Candidate" },
      ])}
      <div class="card">
        <h2>Building the interview plan…</h2>
        <p class="sub">${pill(candidate.blueprint_status)}
          ${candidate.error ? esc(candidate.error) : "Usually under a minute."}</p>
      </div>`;
    if (candidate.blueprint_status !== "failed") poll(2500);
    return;
  }

  const bp = candidate.blueprint;
  // At most one session is open at a time; anything else is history.
  const open = interviews.find((i) => ["scheduled", "in_progress"].includes(i.status));
  const past = interviews.filter((i) => i !== open);

  app.innerHTML = `
    ${crumbs([
      { label: "Profiles", href: "#/profiles" },
      { label: bp.evaluation_spec.role_title, href: backToProfile },
      { label: bp.candidate_name || "Candidate" },
    ])}

    <div class="card">
      <h2 style="margin:0">${esc(bp.candidate_name || "Candidate")}</h2>
      <p class="sub" style="margin:10px 0 0">${esc(bp.candidate_summary || "")}</p>
    </div>

    ${candidate.plan_is_stale ? `<div class="card note">
      <strong>This plan is out of date.</strong>
      It was built before you changed what this profile tests. It was kept
      because you had edited it by hand — rebuilding it would have discarded
      your edits. Ask for the change again below, or delete and re-upload the CV
      to start from the current spec.
    </div>` : ""}

    <div class="card">
      <div class="spread">
        <h3 style="margin:0">Session</h3>
        ${open ? "" : `<button class="primary" id="start">Start interview</button>`}
      </div>
      ${open ? `
        <div class="cred" style="margin-top:14px">
          <div class="small muted" style="margin-bottom:6px">Send the candidate:</div>
          <div><strong>${location.origin}/join</strong></div>
          <div style="margin-top:8px">
            Meeting ID <strong class="mono">${esc(open.meeting_id)}</strong> ·
            Password <strong class="mono">${esc(open.password || "")}</strong>
          </div>
        </div>
        <div class="row" style="margin-top:14px">
          <a class="btn" href="#/interviews/${open.interview_id}">Open session</a>
          ${open.status === "scheduled"
            ? `<button class="danger" onclick="cancelSession('${open.interview_id}')">Cancel</button>`
            : ""}
          ${pill(open.status)}
        </div>
        <p class="small muted" style="margin-bottom:0">
          One session at a time. Start another once this one has finished.</p>
      ` : `<p class="sub" style="margin:10px 0 0">No session yet. Starting one
        creates the link, meeting ID and password to send the candidate.</p>`}
      <div class="err hidden" id="starterr"></div>
    </div>

    ${past.length ? `<div class="card">
      <h3 style="margin-top:0">Earlier sessions</h3>
      ${past.map((i) => `
        <a class="list-item" href="#/interviews/${i.interview_id}"
           style="display:block;text-decoration:none;color:inherit">
          <div class="spread">
            <div><strong class="mono">${esc(i.meeting_id)}</strong>
              <div class="small muted">${when(i.created_at)}</div></div>
            ${pill(i.status)}
          </div>
        </a>`).join("")}
    </div>` : ""}

    <div class="card">
      <h3 style="margin-top:0">The plan for this candidate</h3>
      ${bp.claims_to_verify?.length ? `
        <p class="small muted" style="margin:10px 0 4px">Claims it will test:</p>
        <ul class="small" style="margin:0;padding-left:19px">
          ${bp.claims_to_verify.map((c) => `<li>${esc(c.claim)}</li>`).join("")}</ul>` : ""}
      <div class="turn" style="margin-top:14px">
        <span class="who">${bp.opening_minutes} min</span> · Opening and warm-up</div>
      ${bp.competency_plans.map((p) => `
        <div class="turn">
          <span class="who">${p.time_budget_minutes} min · ${esc(p.name)}</span>
          <div class="small muted" style="margin:4px 0">${esc(p.target_depth)}</div>
          <details><summary class="small muted" style="cursor:pointer">
            ${p.seed_questions.length} questions</summary>
            <ul class="small" style="margin:8px 0 0;padding-left:19px">
              ${p.seed_questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>
          </details>
        </div>`).join("")}
      <div class="turn"><span class="who">${bp.closing_minutes} min</span> · Closing</div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Improve the plan</h3>
      <p class="sub" style="margin-top:6px">Tell Mitra what to change — "spend less
        time on mentoring", "push harder on the pricing claim", "that question is unfair".</p>
      <div class="chat">${chat(candidate.refinements)}</div>
      <textarea id="refine" placeholder="What should change?"></textarea>
      <div class="row" style="margin-top:10px">
        <button class="primary" id="dorefine">Update plan</button>
        <span class="muted small" id="refstatus"></span>
      </div>
      <div class="err hidden" id="referr"></div>
    </div>
  `;

  const startButton = document.getElementById("start");
  if (startButton) {
    startButton.onclick = async () => {
      startButton.disabled = true;
      try {
        const started = await api(`/candidates/${candidateId}/interviews`, { method: "POST" });
        go(`/interviews/${started.interview_id}`);
      } catch (e) {
        startButton.disabled = false;
        const err = document.getElementById("starterr");
        err.textContent = e.message;
        err.classList.remove("hidden");
      }
    };
  }

  document.getElementById("dorefine").onclick = async () => {
    const text = document.getElementById("refine").value.trim();
    if (!text) return;
    document.getElementById("dorefine").disabled = true;
    document.getElementById("refstatus").textContent = "Rewriting the plan…";
    try {
      await api(`/candidates/${candidateId}/refine`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      render();
    } catch (e) {
      document.getElementById("dorefine").disabled = false;
      document.getElementById("refstatus").textContent = "";
      const err = document.getElementById("referr");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };
}

window.cancelSession = async (interviewId) => {
  if (!confirm(
    "Cancel this session?\n\nThe meeting ID and password stop working. You can " +
    "start a new session afterwards."
  )) return;
  await api(`/interviews/${interviewId}`, { method: "DELETE" });
  render();
};

// --------------------------------------------------------------- interview

async function viewInterview(interviewId) {
  const iv = await api(`/interviews/${interviewId}`);
  const metrics = iv.session_metrics || {};
  const latency = metrics.latency_summary || {};
  const done = ["completed", "failed"].includes(iv.status);

  app.innerHTML = `
    ${crumbs([
      { label: "Profiles", href: "#/profiles" },
      { label: "Candidate", href: `#/candidates/${iv.candidate_id}` },
      { label: "Session" },
    ])}

    <div class="card">
      <div class="spread"><h2 style="margin:0">Interview session</h2>${pill(iv.status)}</div>
      <div class="cred" style="margin-top:14px">
        <div class="small muted" style="margin-bottom:6px">Send the candidate:</div>
        <div><strong>${location.origin}/join</strong></div>
        <div style="margin-top:8px">
          Meeting ID <strong class="mono">${esc(iv.meeting_id)}</strong> ·
          Password <strong class="mono">${esc(iv.password)}</strong>
        </div>
      </div>
      ${iv.failure_reason ? `<div class="err">${esc(iv.failure_reason)}</div>` : ""}
    </div>

    ${done ? reportCard(iv) : ""}
    ${done ? brainCard(iv, metrics, latency) : `<div class="card muted">
      Waiting for the candidate to join. Mitra starts when they do — this page
      refreshes on its own.</div>`}
  `;

  const retry = document.getElementById("rescore");
  if (retry) {
    retry.onclick = async () => {
      // Only warn when there is a report to lose. Scores can shift between
      // runs on the same transcript, and someone may already have acted on the
      // one on screen.
      if (iv.feedback_report && !confirm(
        "Rebuild this report from the transcript?\n\n" +
        "It is scored again from the same conversation, so wording and scores " +
        "may come out slightly differently. The current report is replaced."
      )) return;
      retry.disabled = true;
      await api(`/interviews/${interviewId}/feedback`, { method: "POST" });
      render();
    };
  }

  // Poll while the interview is live, and while scoring is still running.
  if (!done) poll(5000);
  else if (["pending", "generating"].includes(iv.feedback_status)) poll(4000);
}

// -- the report -------------------------------------------------------------
//
// Scores and the evidence behind them come first; the written assessment sits
// at the bottom. A reader who only looks at the top should see what the
// transcript actually supports, not a paragraph telling them what to think.

const SIGNAL_TEXT = {
  strong_evidence_for: "Strong evidence gathered",
  some_evidence_for: "Some evidence gathered",
  mixed: "Mixed evidence",
  limited_evidence: "Limited evidence gathered",
  insufficient_signal: "Not enough signal to judge",
};

function reportCard(iv) {
  if (iv.feedback_status === "failed") {
    return `<div class="card note">
      <div class="spread">
        <div>
          <strong>Scoring did not complete.</strong>
          <div class="small muted" style="margin-top:4px">${esc(iv.feedback_error || "")}</div>
        </div>
        <button id="rescore">Try again</button>
      </div>
      <p class="small muted" style="margin-bottom:0">The transcript is saved and
        unaffected — only the report needs rebuilding.</p>
    </div>`;
  }
  if (!iv.feedback_report) {
    return `<div class="card muted">
      Scoring the transcript… this page updates itself.
      <span class="small">(${esc(iv.feedback_status)})</span></div>`;
  }

  const r = iv.feedback_report;
  const scored = r.competency_scores.filter((s) => s.score !== null);
  const average = scored.length
    ? scored.reduce((a, s) => a + s.score, 0) / scored.length
    : null;
  const rebuilding = ["pending", "generating"].includes(iv.feedback_status);

  return `
    <div class="card">
      <div class="spread">
        <div>
          <h3 style="margin:0">Assessment</h3>
          <div class="small muted" style="margin-top:4px">
            ${esc(r.role_title)} · ${scored.length} of ${r.competency_scores.length}
            competencies evidenced
          </div>
        </div>
        <div style="text-align:right">
          <div class="score-big">${average === null ? "—" : average.toFixed(1)}<span
            class="score-max">/5</span></div>
          <div class="small muted">${esc(SIGNAL_TEXT[r.recommendation] || r.recommendation)}</div>
        </div>
      </div>
      <p class="small muted" style="margin:14px 0 0">
        Evidence for a person to weigh — not a hiring decision. Every score below
        is backed by quotes you can check against the transcript.</p>
      <div class="spread" style="margin-top:16px;align-items:center">
        <span class="small muted">
          ${rebuilding
            ? "Recomputing from the transcript…"
            : iv.feedback_generated_at
              ? `Generated ${when(iv.feedback_generated_at)}`
              : "Generated from the saved transcript"}
        </span>
        <button id="rescore" ${rebuilding ? "disabled" : ""}>Regenerate</button>
      </div>
    </div>

    ${r.competency_scores.map(competencyCard).join("")}

    ${r.red_flags_observed?.length ? `<div class="card">
      <h3 style="margin-top:0">Dealbreakers observed</h3>
      ${r.red_flags_observed.map((f) => `
        <div class="turn">
          <strong>${esc(f.description)}</strong>
          ${f.evidence.map(quoteBlock).join("")}
        </div>`).join("")}
    </div>` : ""}

    ${r.contradictions?.length ? `<div class="card">
      <h3 style="margin-top:0">Inconsistencies noted</h3>
      <p class="small muted" style="margin-top:0">Recorded for you to weigh.
        People misremember; this is not an accusation.</p>
      ${r.contradictions.map((c) => `
        <div class="turn small">
          <div class="muted">Earlier: ${esc(c.earlier_claim)}</div>
          <div class="muted">Later: ${esc(c.later_statement)}</div>
          ${c.probed ? `<div class="small">Mitra asked about this during the interview.</div>` : ""}
        </div>`).join("")}
    </div>` : ""}

    ${r.coverage_gaps?.length ? `<div class="card note">
      <strong>What this report cannot tell you</strong>
      <ul class="small" style="margin:8px 0 0;padding-left:19px">
        ${r.coverage_gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>
    </div>` : ""}

    <div class="card">
      <h3 style="margin-top:0">Written summary</h3>
      <p style="margin-bottom:0;white-space:pre-wrap">${esc(r.summary)}</p>
    </div>`;
}

function competencyCard(s) {
  const pct = s.score === null ? 0 : (s.score / 5) * 100;
  return `
    <div class="card">
      <div class="spread">
        <div>
          <strong>${esc(s.name)}</strong>
          <div class="small muted" style="margin-top:3px">${esc(s.rationale)}</div>
        </div>
        <div style="text-align:right;min-width:74px">
          ${s.score === null
            ? `<span class="pill warn">no signal</span>`
            : `<div class="score-mid">${s.score.toFixed(1)}<span class="score-max">/5</span></div>`}
        </div>
      </div>
      ${s.score === null ? "" : `
        <div class="meter"><div class="meter-fill" style="width:${pct}%"></div></div>`}
      ${s.evidence?.length ? `
        <details style="margin-top:12px">
          <summary class="small muted" style="cursor:pointer">
            ${s.evidence.length} quote${s.evidence.length === 1 ? "" : "s"} from the transcript</summary>
          ${s.evidence.map(quoteBlock).join("")}
        </details>` : ""}
    </div>`;
}

function quoteBlock(q) {
  return `<blockquote class="quote">
    “${esc(q.text)}”
    <span class="small muted">— ${Math.floor(q.at_seconds / 60)}:${
      String(Math.round(q.at_seconds % 60)).padStart(2, "0")}, turn ${q.turn_index}</span>
  </blockquote>`;
}

function brainCard(iv, metrics, latency) {
  const turns = iv.transcript?.turns || [];
  const outcomes = (iv.section_outcomes || []).filter((o) => o.turns_spent > 0);
  const events = metrics.brain_events || [];

  return `
    <div class="card">
      <h3 style="margin-top:0">How it went</h3>
      <div class="kv">
        <div>Length</div><div>${((iv.transcript?.duration_seconds || 0) / 60).toFixed(1)} min</div>
        <div>Turns</div><div>${turns.length}</div>
        <div>Median response</div>
        <div>${latency.ttfa_median_ms ? (latency.ttfa_median_ms / 1000).toFixed(2) + "s" : "—"}</div>
        <div>Longest pause tolerated</div><div>${latency.longest_tolerated_pause_s ?? "—"}s</div>
        <div>Model</div><div class="mono small">${esc(metrics.llm_model || "—")}</div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">What Mitra decided</h3>
      ${outcomes.map((o) => `
        <div class="turn">
          <div class="spread"><strong>${esc(o.section_id)}</strong>${pill(o.coverage)}</div>
          <div class="small muted">
            ${o.turns_spent} turns · ${Math.round(o.seconds_spent)}s of ${Math.round(o.budget_seconds)}s
            ${o.declined_turns ? ` · ${o.declined_turns} declined` : ""}
          </div>
          ${o.shortfall_reason ? `<div class="small err">${esc(o.shortfall_reason)}</div>` : ""}
          ${o.key_claims?.length ? `<ul class="small" style="margin:7px 0 0;padding-left:19px">
            ${o.key_claims.map((c) => `<li>${esc(c.text)}</li>`).join("")}</ul>` : ""}
          ${(o.contradictions || []).map((c) => `
            <div class="small" style="margin-top:7px">
              <span class="pill warn">inconsistency</span>
              <div class="muted">Earlier: ${esc(c.earlier_claim)}</div>
              <div class="muted">Later: ${esc(c.later_statement)}</div>
            </div>`).join("")}
        </div>`).join("")}
    </div>

    ${events.length ? `<div class="card">
      <h3 style="margin-top:0">Decisions timeline</h3>
      <pre>${esc(events.map((e) =>
        `${String(Math.round(e.at_seconds)).padStart(4)}s  ${e.kind.padEnd(16)} ${
          JSON.stringify(Object.fromEntries(
            Object.entries(e).filter(([k]) => !["kind", "at_seconds"].includes(k))))}`
      ).join("\n"))}</pre></div>` : ""}

    <div class="card">
      <h3 style="margin-top:0">Transcript</h3>
      ${turns.map((t) => `
        <div class="turn">
          <span class="who ${t.speaker === "interviewer" ? "int" : ""}">
            ${t.speaker === "interviewer" ? "Mitra" : "Candidate"}</span>
          <span class="muted small">${Math.round(t.at_seconds)}s</span>
          <div>${esc(t.text)}</div>
        </div>`).join("")}
    </div>`;
}

// ------------------------------------------------------------------ render

async function render() {
  ticket += 1;  // cancels any re-render the previous screen had scheduled
  const { section, id } = route();
  try {
    const { signed_in } = await api("/admin/session");
    if (!signed_in) return renderLogin();

    if (section === "profiles" && id === "new") viewNewProfile();
    else if (section === "profiles" && id) await viewProfile(id);
    else if (section === "profiles") await viewProfiles();
    else if (section === "candidates" && id) await viewCandidate(id);
    else if (section === "interviews" && id) await viewInterview(id);
    else go("/profiles");
  } catch (e) {
    if (e.message === "Signed out.") return;
    app.innerHTML = `<div class="card"><h2>Something went wrong</h2>
      <p class="sub">${esc(e.message)}</p>
      <a class="btn" href="#/profiles">Back to profiles</a></div>`;
  }
}

window.signOut = async () => {
  await api("/admin/logout", { method: "POST" });
  go("/profiles");
  renderLogin();
};

window.addEventListener("hashchange", render);
render();
