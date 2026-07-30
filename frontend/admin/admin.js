// Admin panel.
//
//   Login
//     └─ Profiles                     a profile is a role you are hiring for
//          └─ Profile
//               ├─ Discuss with AI    defines what the interview tests
//               ├─ CVs                each generates an interview plan
//               │    └─ Discuss       refine that candidate's plan
//               └─ Start interview    creates the session, ID and password
//
// Plain JavaScript, no build step. CLAUDE.md specifies React + Vite, which is
// still right once this settles; it talks to the real API, so a rewrite swaps
// the view layer and nothing else.

const app = document.getElementById("app");
const state = { view: "profiles", jobId: null, candidateId: null, interviewId: null };

// ---------------------------------------------------------------- utilities

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401 && !path.startsWith("/admin/")) {
    go("login");
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

function pill(status) {
  const good = ["ready", "completed", "sufficient"];
  const bad = ["failed", "insufficient", "expired", "not_started"];
  const cls = good.includes(status) ? "ok" : bad.includes(status) ? "bad" : "warn";
  return `<span class="pill ${cls}">${esc(status).replace(/_/g, " ")}</span>`;
}

function go(view, ids = {}) {
  Object.assign(state, ids, { view });
  render();
}

function crumbs(parts) {
  return `<div class="crumb">${parts
    .map((p) => (p.go ? `<a onclick="${p.go}">${esc(p.label)}</a>` : esc(p.label)))
    .join(" › ")}</div>`;
}

function chat(turns) {
  return (turns || [])
    .map((t) => `<div class="msg ${t.role === "assistant" ? "ai" : "me"}">${esc(t.content)}</div>`)
    .join("");
}

// ------------------------------------------------------------------- login

function viewLogin() {
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
      go("profiles");
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
        <button class="primary" onclick="go('newProfile')">New profile</button>
      </div>
    </div>
    ${profiles.length === 0 ? `<div class="card muted">
      No profiles yet. Create one by uploading a job description.</div>` : ""}
    ${profiles.map((p) => `
      <div class="list-item">
        <div class="spread">
          <div onclick="go('profile', {jobId: '${p.job_id}'})" style="flex:1;cursor:pointer">
            <strong>${esc(p.role_title || p.source_filename || "Untitled")}</strong>
            <div class="small muted">${when(p.created_at)}</div>
          </div>
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
    ${crumbs([{ label: "Profiles", go: "go('profiles')" }, { label: "New profile" }])}
    <div class="card">
      <h2>Upload the job description</h2>
      <p class="sub">PDF, DOCX or text. Mitra reads it, then asks you a few
        questions to work out what the interview should actually test.</p>
      <input type="file" id="jd" accept=".pdf,.docx,.txt,.md" />
      <div class="row" style="margin-top:16px">
        <button class="primary" id="up">Create profile</button>
        <span class="muted small" id="status"></span>
      </div>
      <div class="err hidden" id="err"></div>
    </div>`;

  document.getElementById("up").onclick = async () => {
    const file = document.getElementById("jd").files[0];
    if (!file) return;
    document.getElementById("up").disabled = true;
    document.getElementById("status").textContent = "Reading the job description…";
    try {
      const form = new FormData();
      form.append("file", file);
      const created = await api("/jobs", { method: "POST", body: form });
      go("profile", { jobId: created.job_id });
    } catch (e) {
      document.getElementById("up").disabled = false;
      document.getElementById("status").textContent = "";
      const err = document.getElementById("err");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };
}

// ----------------------------------------------------------------- profile

async function viewProfile() {
  const [job, candidates] = await Promise.all([
    api(`/jobs/${state.jobId}`),
    api(`/jobs/${state.jobId}/candidates`),
  ]);
  const ready = job.spec_status === "ready";
  const spec = job.evaluation_spec;

  app.innerHTML = `
    ${crumbs([
      { label: "Profiles", go: "go('profiles')" },
      { label: spec?.role_title || "New profile" },
    ])}

    <div class="card">
      <div class="spread">
        <h2 style="margin:0">${esc(spec?.role_title || "Defining this profile")}</h2>
        ${pill(job.spec_status)}
      </div>
      <p class="sub" style="margin:8px 0 0">
        ${ready
          ? "Locked in. This is what every interview for this profile will test."
          : "Answer Mitra's questions so it knows what you are actually looking for."}
      </p>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Discussion</h3>
      <div class="chat">${chat(job.clarification)}</div>
      ${ready ? `<p class="small muted">This discussion is complete.</p>` : `
        <textarea id="reply" placeholder="Your answer…"></textarea>
        <div class="row" style="margin-top:10px">
          <button class="primary" id="send">Send</button>
          <span class="muted small" id="status"></span>
        </div>
        <div class="err hidden" id="err"></div>`}
    </div>

    ${ready ? specCard(spec) : ""}
    ${ready ? cvsCard(candidates) : ""}
  `;

  if (!ready) {
    document.getElementById("send").onclick = async () => {
      const text = document.getElementById("reply").value.trim();
      if (!text) return;
      document.getElementById("send").disabled = true;
      document.getElementById("status").textContent = "Thinking…";
      try {
        await api(`/jobs/${state.jobId}/clarify`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        render();
      } catch (e) {
        document.getElementById("send").disabled = false;
        const err = document.getElementById("err");
        err.textContent = e.message;
        err.classList.remove("hidden");
      }
    };
  } else {
    wireCvUpload();
  }
}

function specCard(spec) {
  const total = spec.competencies.reduce((a, c) => a + c.weight, 0) || 1;
  return `
    <div class="card">
      <h3 style="margin-top:0">What this interview tests</h3>
      <div class="kv">
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
      ${candidates.map((c) => `
        <div class="list-item" onclick="go('candidate', {candidateId: '${c.candidate_id}'})">
          <div class="spread">
            <div>
              <strong>${esc(c.name || c.source_filename || "Candidate")}</strong>
              <div class="small muted">${when(c.created_at)}</div>
              ${c.blueprint_error ? `<div class="small err">${esc(c.blueprint_error)}</div>` : ""}
            </div>
            ${pill(c.blueprint_status)}
          </div>
        </div>`).join("")}
    </div>`;
}

function wireCvUpload() {
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
      await api(`/jobs/${state.jobId}/candidates`, { method: "POST", body: form });
      setTimeout(render, 1500);
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

async function viewCandidate() {
  const [candidate, interviews] = await Promise.all([
    api(`/candidates/${state.candidateId}`),
    api(`/candidates/${state.candidateId}/interviews`),
  ]);

  if (candidate.blueprint_status !== "ready") {
    app.innerHTML = `
      ${crumbs([{ label: "Profiles", go: "go('profiles')" }, { label: "Candidate" }])}
      <div class="card">
        <h2>Building the interview plan…</h2>
        <p class="sub">${pill(candidate.blueprint_status)}
          ${candidate.error ? esc(candidate.error) : "Usually under a minute."}</p>
      </div>`;
    if (candidate.blueprint_status !== "failed") setTimeout(render, 2500);
    return;
  }

  const bp = candidate.blueprint;
  app.innerHTML = `
    ${crumbs([
      { label: "Profiles", go: "go('profiles')" },
      { label: bp.evaluation_spec.role_title, go: `go('profile', {jobId: '${candidate.job_id}'})` },
      { label: bp.candidate_name || "Candidate" },
    ])}

    <div class="card">
      <div class="spread">
        <h2 style="margin:0">${esc(bp.candidate_name || "Candidate")}</h2>
        <button class="primary" id="start">Start interview</button>
      </div>
      <p class="sub" style="margin:10px 0 0">${esc(bp.candidate_summary || "")}</p>
      <div class="err hidden" id="starterr"></div>
    </div>

    ${interviews.length ? `<div class="card">
      <h3 style="margin-top:0">Sessions</h3>
      ${interviews.map((i) => `
        <div class="list-item" onclick="go('interview', {interviewId: '${i.interview_id}'})">
          <div class="spread">
            <div><strong class="mono">${esc(i.meeting_id)}</strong>
              <div class="small muted">${when(i.created_at)}</div></div>
            ${pill(i.status)}
          </div>
        </div>`).join("")}
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

  document.getElementById("start").onclick = async () => {
    document.getElementById("start").disabled = true;
    try {
      const started = await api(`/candidates/${state.candidateId}/interviews`, { method: "POST" });
      go("interview", { interviewId: started.interview_id });
    } catch (e) {
      document.getElementById("start").disabled = false;
      const err = document.getElementById("starterr");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };

  document.getElementById("dorefine").onclick = async () => {
    const text = document.getElementById("refine").value.trim();
    if (!text) return;
    document.getElementById("dorefine").disabled = true;
    document.getElementById("refstatus").textContent = "Rewriting the plan…";
    try {
      await api(`/candidates/${state.candidateId}/refine`, {
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

// --------------------------------------------------------------- interview

async function viewInterview() {
  const iv = await api(`/interviews/${state.interviewId}`);
  const metrics = iv.session_metrics || {};
  const latency = metrics.latency_summary || {};
  const done = ["completed", "failed"].includes(iv.status);

  app.innerHTML = `
    ${crumbs([
      { label: "Profiles", go: "go('profiles')" },
      { label: "Candidate", go: `go('candidate', {candidateId: '${iv.candidate_id}'})` },
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

    ${done ? brainCard(iv, metrics, latency) : `<div class="card muted">
      Waiting for the candidate to join. Mitra starts when they do — this page
      refreshes on its own.</div>`}
  `;

  if (!done) setTimeout(render, 5000);
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

// ------------------------------------------------------------------ router

async function render() {
  try {
    if (state.view !== "login") {
      const { signed_in } = await api("/admin/session");
      if (!signed_in) return viewLogin();
    }
    if (state.view === "login") viewLogin();
    else if (state.view === "profiles") await viewProfiles();
    else if (state.view === "newProfile") viewNewProfile();
    else if (state.view === "profile") await viewProfile();
    else if (state.view === "candidate") await viewCandidate();
    else if (state.view === "interview") await viewInterview();
  } catch (e) {
    if (e.message === "Signed out.") return;
    app.innerHTML = `<div class="card"><h2>Something went wrong</h2>
      <p class="sub">${esc(e.message)}</p>
      <button onclick="go('profiles')">Back to profiles</button></div>`;
  }
}

window.go = go;
window.signOut = async () => { await api("/admin/logout", { method: "POST" }); go("login"); };
render();
