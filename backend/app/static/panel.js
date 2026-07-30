// Employer panel: JD -> clarification -> CV -> blueprint -> interview -> observe.
//
// Deliberately plain JavaScript with no build step. CLAUDE.md specifies React +
// Vite for the frontend, and that is still the right call once this stabilises;
// this exists to make the pipeline visible today without adding a Node stage to
// the Docker build. The API it speaks to is the real one, so a React rewrite
// swaps the view layer and nothing else.

const app = document.getElementById("app");
const state = { view: "jobs", jobId: null, candidateId: null, interviewId: null };

// ---------------------------------------------------------------- utilities

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} on ${path}`);
  return body;
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function pill(status) {
  const good = ["ready", "completed", "sufficient"];
  const bad = ["failed", "insufficient", "expired", "not_started"];
  const cls = good.includes(status) ? "ok" : bad.includes(status) ? "bad" : "warn";
  return `<span class="pill ${cls}">${esc(status)}</span>`;
}

function go(view, ids = {}) {
  Object.assign(state, ids, { view });
  render();
}

function crumbs(parts) {
  return `<div class="crumb">${parts
    .map((p) => (p.go ? `<a onclick='${p.go}'>${esc(p.label)}</a>` : esc(p.label)))
    .join(" › ")}</div>`;
}

// ---------------------------------------------------------------- 1. jobs

async function viewJobs() {
  const jobs = await api("/jobs");
  app.innerHTML = `
    <div class="spread">
      <div><h2>Roles</h2><p class="sub">Upload a job description to begin.</p></div>
      <button class="primary" onclick="go('newJob')">New role</button>
    </div>
    ${jobs.length === 0 ? `<div class="card muted">No roles yet.</div>` : ""}
    ${jobs.map((j) => `
      <div class="list-item" onclick="go('job', {jobId: '${j.job_id}'})">
        <div class="spread">
          <div>
            <strong>${esc(j.role_title || j.source_filename || "Untitled role")}</strong>
            <div class="small muted mono">${esc(j.job_id)}</div>
          </div>
          ${pill(j.spec_status)}
        </div>
      </div>`).join("")}
  `;
}

function viewNewJob() {
  app.innerHTML = `
    ${crumbs([{ label: "Roles", go: "go('jobs')" }, { label: "New role" }])}
    <div class="card">
      <h2>Upload a job description</h2>
      <p class="sub">PDF, DOCX or text. ${"{{BOT_NAME}}"} will then ask you a few questions
        to work out what the interview should actually test.</p>
      <input type="file" id="jd" accept=".pdf,.docx,.txt,.md" />
      <div class="row" style="margin-top:16px">
        <button class="primary" id="up">Upload and start</button>
        <span class="muted small" id="status"></span>
      </div>
      <div class="err hidden" id="err"></div>
    </div>`;

  document.getElementById("up").onclick = async () => {
    const file = document.getElementById("jd").files[0];
    if (!file) return;
    const button = document.getElementById("up");
    button.disabled = true;
    document.getElementById("status").textContent = "Reading the JD and thinking…";
    try {
      const form = new FormData();
      form.append("file", file);
      const created = await api("/jobs", { method: "POST", body: form });
      go("job", { jobId: created.job_id });
    } catch (e) {
      button.disabled = false;
      document.getElementById("status").textContent = "";
      const err = document.getElementById("err");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };
}

// ------------------------------------------------- 2. clarification + spec

async function viewJob() {
  const [job, candidates] = await Promise.all([
    api(`/jobs/${state.jobId}`),
    api(`/jobs/${state.jobId}/candidates`),
  ]);

  const chat = job.clarification.map((t) =>
    `<div class="msg ${t.role === "employer" ? "me" : "ai"}">${esc(t.content)}</div>`).join("");

  const ready = job.spec_status === "ready";
  const spec = job.evaluation_spec;

  app.innerHTML = `
    ${crumbs([{ label: "Roles", go: "go('jobs')" }, { label: spec?.role_title || "Role" }])}
    <div class="card">
      <div class="spread">
        <h2>${esc(spec?.role_title || "Defining the interview")}</h2>${pill(job.spec_status)}
      </div>
      <div class="chat" style="margin-top:18px">${chat}</div>
      ${ready ? "" : `
        <textarea id="reply" placeholder="Your answer…"></textarea>
        <div class="row" style="margin-top:10px">
          <button class="primary" id="send">Send</button>
          <span class="muted small" id="status"></span>
        </div>
        <div class="err hidden" id="err"></div>`}
    </div>
    ${ready ? specCard(spec) : ""}
    ${ready ? candidatesCard(candidates) : ""}
  `;

  if (!ready) {
    document.getElementById("send").onclick = async () => {
      const text = document.getElementById("reply").value.trim();
      if (!text) return;
      const button = document.getElementById("send");
      button.disabled = true;
      document.getElementById("status").textContent = "Thinking…";
      try {
        await api(`/jobs/${state.jobId}/clarify`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        render();
      } catch (e) {
        button.disabled = false;
        const err = document.getElementById("err");
        err.textContent = e.message;
        err.classList.remove("hidden");
      }
    };
  }
}

function specCard(spec) {
  const total = spec.competencies.reduce((a, c) => a + c.weight, 0) || 1;
  return `
    <div class="card">
      <h3 style="margin-top:0">What the interview will evaluate</h3>
      <div class="kv">
        <div>Seniority</div><div>${esc(spec.seniority)}</div>
        <div>Experience</div><div>${esc(spec.experience_expectation)}</div>
        <div>Length</div><div>${spec.duration_minutes} min (+${spec.overrun_grace_minutes} grace)</div>
      </div>
      <h3>Competencies</h3>
      ${spec.competencies.map((c) => `
        <div style="margin-bottom:12px">
          <div class="spread">
            <strong>${esc(c.name)}</strong>
            <span class="muted small">${(c.weight * 100).toFixed(0)}%</span>
          </div>
          <div class="bar" style="width:${(c.weight / total) * 100}%; margin:5px 0"></div>
          <div class="small muted">${esc(c.description)}</div>
        </div>`).join("")}
      ${spec.red_flags?.length ? `<h3>Dealbreakers</h3>
        <ul class="small muted" style="margin:0; padding-left:19px">
          ${spec.red_flags.map((f) => `<li>${esc(f)}</li>`).join("")}
        </ul>` : ""}
    </div>`;
}

function candidatesCard(candidates) {
  return `
    <div class="card">
      <div class="spread"><h3 style="margin:0">Candidates</h3></div>
      <p class="sub" style="margin-top:6px">Upload a CV. A candidate-specific interview
        plan is generated straight away — you do not wait for it at interview time.</p>
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
              <div class="small muted mono">${esc(c.candidate_id)}</div>
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
    document.getElementById("cvstatus").textContent = "Parsing CV and building the plan…";
    try {
      const form = new FormData();
      form.append("file", file);
      await api(`/jobs/${state.jobId}/candidates`, { method: "POST", body: form });
      // Generation runs in the background; polling shows it flip to ready.
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

// ------------------------------------------------- 3. blueprint + booking

async function viewCandidate() {
  const [candidate, interviews] = await Promise.all([
    api(`/candidates/${state.candidateId}`),
    api(`/candidates/${state.candidateId}/interviews`),
  ]);
  const bp = candidate.blueprint;

  if (candidate.blueprint_status !== "ready") {
    app.innerHTML = `
      ${crumbs([{ label: "Roles", go: "go('jobs')" }, { label: "Candidate" }])}
      <div class="card">
        <h2>Building the interview plan…</h2>
        <p class="sub">${pill(candidate.blueprint_status)}
          ${candidate.error ? esc(candidate.error) : "This usually takes under a minute."}</p>
      </div>`;
    if (candidate.blueprint_status !== "failed") setTimeout(render, 2500);
    return;
  }

  app.innerHTML = `
    ${crumbs([
      { label: "Roles", go: "go('jobs')" },
      { label: bp.evaluation_spec.role_title, go: `go('job', {jobId: '${candidate.job_id}'})` },
      { label: bp.candidate_name || "Candidate" },
    ])}
    <div class="card">
      <div class="spread">
        <h2>${esc(bp.candidate_name || "Candidate")}</h2>
        <button class="primary" id="book">Book interview</button>
      </div>
      <p class="sub">${esc(bp.candidate_summary || "")}</p>
      ${bp.claims_to_verify?.length ? `
        <h3>Claims this interview will test</h3>
        <ul class="small" style="margin:0; padding-left:19px">
          ${bp.claims_to_verify.map((c) => `<li>${esc(c.claim)}</li>`).join("")}
        </ul>` : ""}
      <div class="err hidden" id="bookerr"></div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">The plan ${"{{BOT_NAME}}"} will follow</h3>
      <div class="turn"><span class="who">${bp.opening_minutes} min</span> · Opening and warm-up</div>
      ${bp.competency_plans.map((p) => `
        <div class="turn">
          <div class="spread">
            <span class="who">${p.time_budget_minutes} min · ${esc(p.name)}</span>
          </div>
          <div class="small muted" style="margin:4px 0">${esc(p.target_depth)}</div>
          <details><summary class="small muted" style="cursor:pointer">
            ${p.seed_questions.length} questions</summary>
            <ul class="small" style="margin:8px 0 0; padding-left:19px">
              ${p.seed_questions.map((q) => `<li>${esc(q)}</li>`).join("")}
            </ul>
          </details>
        </div>`).join("")}
      <div class="turn"><span class="who">${bp.closing_minutes} min</span> · Closing</div>
    </div>

    ${interviews.length ? `<div class="card">
      <h3 style="margin-top:0">Interviews</h3>
      ${interviews.map((i) => `
        <div class="list-item" onclick="go('interview', {interviewId: '${i.interview_id}'})">
          <div class="spread">
            <div><strong class="mono">${esc(i.meeting_id)}</strong>
              <div class="small muted">${new Date(i.created_at).toLocaleString()}</div></div>
            ${pill(i.status)}
          </div>
        </div>`).join("")}
    </div>` : ""}
  `;

  document.getElementById("book").onclick = async () => {
    const button = document.getElementById("book");
    button.disabled = true;
    try {
      const booked = await api(`/candidates/${state.candidateId}/interviews`, { method: "POST" });
      go("interview", { interviewId: booked.interview_id });
    } catch (e) {
      button.disabled = false;
      const err = document.getElementById("bookerr");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  };
}

// ------------------------------------------------- 4. the interview itself

async function viewInterview() {
  const iv = await api(`/interviews/${state.interviewId}`);
  const metrics = iv.session_metrics || {};
  const latency = metrics.latency_summary || {};
  const done = ["completed", "failed"].includes(iv.status);

  app.innerHTML = `
    ${crumbs([
      { label: "Roles", go: "go('jobs')" },
      { label: "Candidate", go: `go('candidate', {candidateId: '${iv.candidate_id}'})` },
      { label: "Interview" },
    ])}

    <div class="card">
      <div class="spread"><h2>Interview</h2>${pill(iv.status)}</div>
      <div class="cred" style="margin-top:14px">
        Send the candidate to <strong>${location.origin}/join</strong><br />
        Meeting ID <strong class="mono">${esc(iv.meeting_id)}</strong> ·
        Password <strong class="mono">${esc(iv.password)}</strong>
      </div>
      ${iv.failure_reason ? `<div class="err">${esc(iv.failure_reason)}</div>` : ""}
    </div>

    ${done ? brainCard(iv, metrics, latency) : `
      <div class="card muted">
        Waiting for the candidate to join. This page refreshes on its own.
      </div>`}
  `;

  if (!done) setTimeout(render, 5000);
}

function brainCard(iv, metrics, latency) {
  const turns = iv.transcript?.turns || [];
  const outcomes = (iv.section_outcomes || []).filter((o) => o.turns_spent > 0);
  const events = metrics.brain_events || [];

  return `
    <div class="card">
      <h3 style="margin-top:0">How the interview went</h3>
      <div class="kv">
        <div>Length</div><div>${((iv.transcript?.duration_seconds || 0) / 60).toFixed(1)} min</div>
        <div>Turns</div><div>${turns.length}</div>
        <div>Median response</div>
        <div>${latency.ttfa_median_ms ? (latency.ttfa_median_ms / 1000).toFixed(2) + "s" : "—"}</div>
        <div>Longest tolerated pause</div>
        <div>${latency.longest_tolerated_pause_s ?? "—"}s</div>
        <div>Model</div><div class="mono small">${esc(metrics.llm_model || "—")}</div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">What the brain decided</h3>
      <p class="sub">Coverage per topic, what it extracted, and where it fell short.</p>
      ${outcomes.map((o) => `
        <div class="turn">
          <div class="spread">
            <strong>${esc(o.section_id)}</strong>${pill(o.coverage)}
          </div>
          <div class="small muted">
            ${o.turns_spent} turns · ${Math.round(o.seconds_spent)}s of ${Math.round(o.budget_seconds)}s
            ${o.declined_turns ? ` · ${o.declined_turns} declined` : ""}
          </div>
          ${o.shortfall_reason ? `<div class="small err">${esc(o.shortfall_reason)}</div>` : ""}
          ${o.key_claims?.length ? `<ul class="small" style="margin:7px 0 0; padding-left:19px">
            ${o.key_claims.map((c) => `<li>${esc(c.text)}</li>`).join("")}</ul>` : ""}
          ${o.contradictions?.length ? o.contradictions.map((c) => `
            <div class="small" style="margin-top:7px">
              <span class="pill warn">inconsistency</span>
              <div class="muted">Earlier: ${esc(c.earlier_claim)}</div>
              <div class="muted">Later: ${esc(c.later_statement)}</div>
            </div>`).join("") : ""}
        </div>`).join("")}
    </div>

    ${events.length ? `<div class="card">
      <h3 style="margin-top:0">Brain events</h3>
      <pre>${esc(events.map((e) =>
        `${String(Math.round(e.at_seconds)).padStart(4)}s  ${e.kind.padEnd(16)} ${
          JSON.stringify(Object.fromEntries(
            Object.entries(e).filter(([k]) => !["kind", "at_seconds"].includes(k))))}`
      ).join("\n"))}</pre>
    </div>` : ""}

    <div class="card">
      <h3 style="margin-top:0">Transcript</h3>
      ${turns.map((t) => `
        <div class="turn">
          <span class="who ${t.speaker === "interviewer" ? "int" : ""}">
            ${t.speaker === "interviewer" ? "{{BOT_NAME}}" : "Candidate"}
          </span>
          <span class="muted small">${Math.round(t.at_seconds)}s</span>
          <div>${esc(t.text)}</div>
        </div>`).join("")}
    </div>`;
}

// ---------------------------------------------------------------- router

async function render() {
  try {
    if (state.view === "jobs") await viewJobs();
    else if (state.view === "newJob") viewNewJob();
    else if (state.view === "job") { await viewJob(); wireCvUpload(); }
    else if (state.view === "candidate") await viewCandidate();
    else if (state.view === "interview") await viewInterview();
  } catch (e) {
    app.innerHTML = `<div class="card"><h2>Something went wrong</h2>
      <p class="sub">${esc(e.message)}</p>
      <button onclick="go('jobs')">Back to roles</button></div>`;
  }
}

window.go = go;
render();
