# Development Plan: the MITRA organisation platform

> **Status: agreed scope, 2026-11-18.** Branch `dev`. This is the shared brief
> for both engineers and both Claude accounts. Claude memory is per account and
> is not shared, so **anything both sides must know lives here or in CLAUDE.md,
> never only in one person's memory.** Update this file in the same PR as any
> change that alters what it says.

## 1. What we are building

An organisation's end-to-end hiring platform for proctored interviews. One
organisation per deployment. Three kinds of signed-in user, plus the candidate.

| Who | Sees | Does |
| --- | --- | --- |
| **BU head** (Business, Finance, Software, Data Science, ...) | Their own BU only | Raises job requests; reads results for their candidates and compares them; makes the final decision after the technical round; may watch a technical round live |
| **HR** | Every BU's job requests, grouped by BU | Uploads CVs, runs screening, conducts the HR round, books the technical round, sends results to the BU head; may watch a technical round live |
| **Org admin** | Everything, read-only, plus admin sections | System analytics, business analytics, transparency, users and BUs |
| **Candidate** | Their own join page | Joins the HR round and the technical round with a meeting ID and password |

### The journey

```
BU head                      HR                                   MITRA                 BU head
───────                      ──                                   ─────                 ───────
New job request              BU list → job request
  title (required)             Upload CV → Re-upload | Proceed
  JD file (optional)                 │
  chat with the model          Fit analysis: score %, chart, reasoning,
  "what MITRA understood"        10 HR-round questions with reasons
  Edit ↺ | Approve ───────►          │
                               score < 50% → [Reject] | Proceed anyway (reason)
                               score ≥ 50% → Not now | Begin HR round
                                     │
                               HR round: meeting ID + password, checklist, notes
                               (HR and candidate only; recorded, no AI voice)
                                     │
                               Evaluation updates the rubric and recommends
                               → HR: [Reject] | Proceed to technical
                                     │
                               Book technical: duration → two links ──► Proctored technical
                                 candidate link + live view link         interview, built on
                                                                         JD + CV + HR findings
                                                                              │
                               Review result ◄─────────────────────── report + integrity
                               Send to BU head ───────────────────────────────────────► Results,
                                                                                        comparison:
                                                                                        Shortlist |
                                                                                        On hold |
                                                                                        Reject
```

### Candidate status

One field, one state machine, defined in `shared/` and read by every screen.
Only the listed moves are allowed. Every move records who made it, when, the
model's recommendation at that moment, and the reason if one was required.

```
new → screened → ready_for_hr → hr_booked → hr_done → ready_for_technical
    → technical_booked → technical_done → with_bu_head → shortlisted | on_hold | rejected

side exits:  rejected (stage, by, reason) · parked ("not now") · withdrew · no_show
```

A screened candidate below the cut-off carries a *below threshold* flag and does
not move until HR clicks.

### Decisions (2026-11-18)

These override the matching lines in CLAUDE.md, which says so at its top.

| # | Decision |
| --- | --- |
| 1 | The realtime engine is the main engine; `INTERVIEW_ENGINE=pipeline` is the fallback. Done on `dev` |
| 2 | One organisation per deployment, with `org_id` on every table from day one |
| 3 | One sign-in; the account decides the role, never a button on the welcome screen. BU scoping is enforced in server queries |
| 4 | **HR** rejects at screening and after the HR round. After the technical round HR can only **send to the BU head**, and the **BU head** decides. A model only recommends |
| 5 | CV screening cut-off is **50%**, one setting (org level now, per job later). It changes which buttons are offered; "Proceed anyway" needs a reason |
| 6 | **The HR round is held inside MITRA**, with the same join page and credentials. No AI participant joins; it is recorded, transcribed after the call, and evaluated by a text model |
| 7 | **Live view and notes** for the technical round, for HR and the BU head of that BU. Notes only |
| 8 | **Proctoring goes in its own Integrity section** of the report; never in a competency score |
| 9 | **Distance band 50–80 cm**, measured from iris size, plus a "show both hands" step |
| 10 | Integrity reminders are **on-screen captions only, at most three per kind**, then silent recording |
| 11 | **Withdrawal always gets one confirmation**; any yes, or a second request, ends it |
| 12 | Candidates are in **India**: DPDP Act 2023 consent and retention. No emotion or tone inference |
| 13 | **No email** in the first version; in-app badges only |
| 14 | Off-call text work uses **one configurable text model** (section 3) |
| 15 | React + Vite + TypeScript for the console and the candidate app |
| 16 | **Every feature in the catalogue below is in scope**, including the ones added during planning |
| 17 | **Vertical split.** Each engineer owns whole features (model, backend, screens, tests), so both do deep technical work and UI work, and each tests alone |
| 18 | **Only the realtime model sits between the candidate and Mitra's reply.** No brain model and no per-turn call to any other model (section 3) |
| 19 | **The realtime session's own transcript is the record. Deepgram is removed from the new platform**, subject to the accuracy check in section 8. The HR round uses OpenAI file transcription |
| 20 | **Transcripts are stored raw and cleaned.** The raw transcript is never changed; a text model writes a cleaned version beside it |
| 21 | **The auditor records only.** It never steers Mitra. The coordinator's time notes and the ending logic stay |
| 22 | **Plan-bound questioning:** every question is a plan question or a follow-up on the last answer; at most 3 follow-ups per question |
| 23 | **No captions** on the candidate's screen, neither Mitra's nor their own. On-screen integrity reminders are separate and stay |
| 24 | **Check Intelligence** tab in the admin panel: one interview turn by turn in phase 2; trends across interviews in phase 4 |
| 25 | **UI polish is the last phase.** Live tracking and Check Intelligence are built properly early |

## 2. Feature catalogue

Everything we are building. *Added* marks features proposed during planning and
accepted into scope on 2026-11-18. **Owner** is the track (section 4); **Ph** is
the phase (section 10).

### Organisation and access

| Feature | Owner | Ph |
| --- | --- | --- |
| Welcome page with three doors; one sign-in, role from the account | H | 0 |
| Users, roles, BU membership, `org_id` everywhere, BU scoping in queries | H | 0 |
| Route-guard sweep test for every route and role | H | 0 |
| Versioned database changes (Alembic) | H | 0 |
| User and BU management for the org admin *(added)* | H | 4 |
| In-app badges: "N waiting for you" | H | 2 |

### Job requests (BU head)

| Feature | Owner | Ph |
| --- | --- | --- |
| New job request: title (required), JD file (optional: PDF, Word, text) | H | 1 |
| Chat with the model to describe the role; works with or without a JD | H | 1 |
| "What MITRA understood", with marked assumptions, Edit (back to chat) and Approve | H | 1 |
| Job request list and status; published to HR under the BU | H | 1 |

### Screening (HR)

| Feature | Owner | Ph |
| --- | --- | --- |
| BU → job → candidate lists, status chips, filters ("needs me", "below threshold", "this week") | H | 1 |
| CV upload, Re-upload or Proceed | H | 1 |
| Fit analysis: per-competency 0–4 ratings, CV quotes checked in code, % computed in code from the BU head's weights *(added)*, reasoning, chart | H | 1 |
| Identity hidden from the scorer (name, photo, gender, age, address) *(added)* | H | 1 |
| "No evidence" shown apart from a low score *(added)* | H | 1 |
| 10 HR-round questions, each with a "why we're asking" line *(added)* | H | 1 |
| Threshold gates: Reject or Proceed anyway (reason required) *(added)*; Not now or Begin HR round | H | 1 |

### HR round

| Feature | Owner | Ph |
| --- | --- | --- |
| Booking a room with no bot; candidate credentials; HR's own join from the console | H | 2 |
| HR round screen: credentials to send, question checklist, notes box *(added)* | H | 2 |
| Per-participant recording; OpenAI file transcription of each voice after the call; one transcript in spoken order | H | 2 |
| Evaluation by the text model: soft-skill rubric, verified quotes, recommendation | H | 2 |
| Evaluation review; Reject or Proceed to technical | H | 2 |
| HR findings handed to MITRA's technical plan *(added)* | H produces, I consumes | 3 |

### Candidate app

| Feature | Owner | Ph |
| --- | --- | --- |
| Join (meeting ID + password) for both rounds; the HR round in a lighter mode | I | 1 |
| Consent covering AI, recording, proctoring, live watching, retention; accommodation option *(added)* | I | 1 |
| Device check: camera, mic level, speaker *(added)*, lighting *(added)*, 50–80 cm band, "show both hands" *(added)*, whole-screen share, single monitor *(added)*, supported browser *(added)*. Each item passes or says how to fix it | I | 1 |
| 30-second warm-up that also calibrates gaze *(added)* | I | 1 |
| In-call screen with **no captions** (decision 23): Mitra's presence, timer and progress, on-screen integrity reminders | I | 1 |
| Rejoin after a dropped call; thank-you page with next steps | I | 3 |

### MITRA technical round

| Feature | Owner | Ph |
| --- | --- | --- |
| Blueprint built from JD + spec + CV + HR findings | I | 3 |
| Duration set per booking, default from the job | I | 2 |
| Withdrawal: one confirmation, then a graceful end | I | 1 |
| Plan-bound questioning: numbered question bank, "plan or follow-up only", current area in full, follow-up cap of 3 | I | 1 |
| Transcript record: raw turns ordered by spoken time, Mitra's interrupted replies cut to what was heard | I | 1 |
| Cleaned transcript beside the raw one, written by the text model after the call | I | 2 |
| Transcript accuracy check: 5 recorded sessions against offline transcription | I | 2 |
| Auditor (record only): labels each question as plan, follow-up or off-plan; area coverage, claims, contradictions; feeds Check Intelligence and the report's per-area outcomes | I | 2 |
| Event stream for the live view and analytics | I | 2 |
| Session hand-over past 55 minutes, or a booking cap | I | 5 |

### Proctoring

| Feature | Owner | Ph |
| --- | --- | --- |
| In the browser: distance, gaze away from the screen, face missing or second face *(added)*, tab hidden, share stopped or not the whole screen, second monitor *(added)*, full-screen exit and paste *(added)* | I | 1 |
| Reminder captions: max 3 per kind, 2-minute cooldown *(added)*, then record only | I | 1 |
| After the call: screen frames classified by a vision model *(added)*, second voice on the candidate's track *(added)*, gaze patterns re-checked | I | 3 |
| Integrity section: summary, each event with timestamp, clip or thumbnail, and whether a reminder was shown | I | 2 |

### Technical round booking, live view, report

| Feature | Owner | Ph |
| --- | --- | --- |
| Book technical: duration, two links (candidate + live view) | I | 2 |
| Live view for HR and the BU head: video, screen, transcript, plan, Mitra's reading, logs, integrity tiles | I | 2 |
| Notes box and the Arbiter that triages notes | I | 3 |
| Report component: scores, every score opens its quote, every quote jumps to its moment in the recording *(added)*, transcript, Integrity section | I | 2 |

### Results (BU head)

| Feature | Owner | Ph |
| --- | --- | --- |
| Send to BU head (HR) | H | 2 |
| Candidate results: rubric chart, summary, strengths and gaps, JD match; embeds I's report component | H | 3 |
| Side-by-side comparison of all candidates for one job *(added)* | H | 3 |
| Decision: Shortlist, On hold or Reject, with reason *(added)* | H | 3 |

### Org dashboard and admin

| Feature | Owner | Ph |
| --- | --- | --- |
| Org dashboard: every BU's jobs and candidates, read-only | H | 4 |
| System analytics: turn latency (median, p95), timeouts, provider errors, cost per interview and trend, concurrency, failed sessions | I | 4 |
| **Check Intelligence, one interview:** turn by turn, live or after: what was said, reply wait and its parts, plan / follow-up / off-plan label, coverage, time notes, tools, cost | I | 2 |
| **Check Intelligence, across interviews:** reply-time trend, off-plan rate by job and area, overruns, failed sessions, cost | I | 4 |
| Business analytics: jobs by BU, stage conversion, time in stage, completion, no-show and error rates, score spread, "Proceed anyway" outcomes *(added)* | I | 4 |
| Transparency: searchable transcripts, recordings and reports; audit log of every decision beside the model's recommendation *(added)* | H | 4 |
| AI usage register: model and prompt version behind every score *(added)* | H records, I records for its models | 4 |
| Consent records, retention periods, deletion requests *(added)* | H | 4 |

### Engineering

| Feature | Owner | Ph |
| --- | --- | --- |
| Text-model slots, request helper for reasoning models, `check_models.py` *(added)* | I | 0 |
| Shared contracts with fixtures | both | 0 |
| Console shell (layout, nav, role homes, auth guard) | H | 0 |
| Design system `frontend/ui/` | I | 0 |
| CI on every PR into `dev` *(added)* | I | 0 |
| Seed and fixture scripts: `seed_org.py` (H), `seed_candidate.py`, `replay_events.py`, `fake_bot.py` (I) *(added)* | | 0–2 |
| Feature flags, so the old flow keeps running until cutover *(added)* | both | 0 |
| Railway `dev` environment *(added)* | whoever has Railway access | 0 |

## 3. Models and the live interview

| Role | Env slot | Default | Owner |
| --- | --- | --- | --- |
| Live conversation (technical round) | `REALTIME_MODEL` | `gpt-realtime-2.1` | I |
| Candidate transcript, technical round | `REALTIME_TRANSCRIPTION_MODEL` | `gpt-realtime-whisper`, inside the realtime session | I. Accuracy-leaning `delay`; off the reply path |
| Auditor | `OPENAI_AUDITOR_MODEL` | `gpt-4.1-mini` | I. Runs during the call but off the reply path; never a reasoning model |
| HR round transcript | `OPENAI_TRANSCRIBE_MODEL` | OpenAI file transcription (`gpt-4o-transcribe`) | H. After the call, one file per voice |
| Job understanding + BU head chat | `OPENAI_CLARIFY_MODEL` | `OPENAI_TEXT_MODEL` | H |
| CV screening + HR questions | `OPENAI_SCREENING_MODEL` | `OPENAI_TEXT_MODEL` | H |
| HR round evaluation | `OPENAI_HR_EVAL_MODEL` | `OPENAI_TEXT_MODEL` | H |
| Technical blueprint | `OPENAI_BLUEPRINT_MODEL` | `OPENAI_TEXT_MODEL` | I |
| Technical report, log analysis | `OPENAI_FEEDBACK_MODEL` | `OPENAI_TEXT_MODEL` | I |
| Screen-frame classification | `OPENAI_VISION_MODEL` | `OPENAI_TEXT_MODEL` | I |

**The target text model is GPT-6.2 Sol, but it is not listed yet.** On
2026-11-18, OpenAI's API docs list `gpt-6-sol` and point to `gpt-6.1-sol` as the
newer model; `gpt-6.2-sol` appears only in third-party reports.

- The code default is `gpt-6.1-sol`.
- Once the key can call it, setting `OPENAI_TEXT_MODEL=gpt-6.2-sol` moves every
  off-call role at once.
- `scripts/check_models.py` lists what the key can actually call.

Switching to a reasoning model is a code change as well as a setting. Today
`clarify.py`, `generate.py`, `refine.py` and `score.py` pass `temperature`,
which reasoning models reject, and use `json_object` instead of a JSON schema.
Phase 0 adds one helper for request arguments (as `bot/brain/drivers.py`
already does) and moves the calls to structured outputs. After that, re-run
the clarify attribution test, judge precision and quote verification.

### The live interview (decided 2026-11-18)

```
candidate audio ──► OpenAI Realtime (hears, decides, speaks) ──► Mitra's voice
        │                         ▲
        │                         └── coordinator: time notes between turns, ending
        └──► realtime transcriber (same session, in parallel) ──► raw transcript
                                                                     │
                       auditor (record only, off the reply path) ◄──┘
                                   │
                         Check Intelligence, the live view, the report
```

- **Only the realtime model is in the reply path.** Nothing of ours runs
  between the candidate finishing and Mitra speaking, so nothing of ours adds
  latency. OpenAI starts the reply itself (`create_response: true`).
- **The transcriber runs in parallel.** It does not delay Mitra; its text may
  arrive before or after Mitra's reply. Its accuracy setting can lean towards
  accuracy because it is off the reply path.
- **The plan bounds the questions.**
  - It arrives as a numbered question bank per area, with the rule "a plan
    question or a follow-up on the last answer; there is no third kind", and
    a cap of 3 follow-ups per question.
  - Only the current area is shown in full, swapped at each area change. Week
    1 checks that a mid-session instructions update takes effect; if not, a
    time note does the same job.
- **No tool calls in normal turns.** A tool call costs a round trip before
  Mitra can speak. Tools are only for ending the interview.
- **The coordinator** (plain code, no model) sends time notes at area
  boundaries and handles withdrawal and the goodbye. It is the only thing that
  writes into the session during the call, apart from watchers' notes once the
  Arbiter exists.
- **The auditor records only.** After each Mitra turn a small text model
  labels the question (plan question with its ID, follow-up on what, or
  off-plan) and updates coverage, claims and contradictions per area. It never
  sends Mitra anything. If Check Intelligence later shows a real off-plan
  problem, steering is a new decision taken with those numbers.
- **No captions on the candidate's screen.**

### Transcripts: raw and cleaned

1. **The raw transcript is never changed.** Mitra's side is the model's own
   text of what it spoke; the candidate's side is the realtime transcriber's.
2. **Turns are ordered by when they were spoken, not when the text arrived.**
   Each turn stores its audio start and end time, because the candidate's text
   can arrive after Mitra has started replying.
3. **An interrupted Mitra reply is cut to what the candidate actually heard.**
4. **After the call, a text model writes a cleaned version beside the raw
   one:** readable, obvious slips fixed, each cleaned turn linked to its raw
   turn. It never replaces the raw text.
5. **Report quotes are verified against the raw transcript.** A cleaned
   version is never evidence on its own. Where the raw text is unclear, the
   report says so and links to that moment in the recording instead of
   guessing.

### Check Intelligence

A tab in the org admin panel, built by Track I on the same event stream as the
live view.

- **One interview (phase 2), live or after:** each turn's text, the reply wait
  and its parts (end-of-speech detection, model to first sound), the plan /
  follow-up / off-plan label, area and coverage, time notes, tools and cost.
- **Across interviews (phase 4):** reply-time trend, off-plan rate by job and
  area, follow-ups per question, overruns, failed sessions, cost per interview.
- **It cannot show the realtime model's private reasoning; OpenAI does not
  expose it.** Any "why it asked this" text is the auditor's reading after the
  fact and is labelled that way on screen.

## 4. The split: two vertical tracks

Each engineer owns **whole features, from the model to the screen**. Both do
deep technical work, both build UI, and each can run and test their own
features end to end without the other.

The line follows the journey:

- **Track H, Hiring flow**, owns everything **around** the AI interview: the
  organisation, job requests, screening, the HR round, and the decision.
- **Track I, Interview**, owns the **AI interview itself** and what measures
  it: the candidate app, MITRA, proctoring, the live view, the report, and
  system and business analytics.

### Track H: Hiring flow

| Layer | Work |
| --- | --- |
| **Models** | Job understanding chat on the text model; CV screening (`analyse_fit`: identity hidden, 0–4 ratings, CV quotes checked in code, % computed in code); `hr_questions`; `evaluate_hr_round` |
| **Backend** | Auth, users, roles, BUs, scoping, Alembic; the stage machine, `Decision` and the audit log; job request API; screening as a background task at upload; HR round rooms without a bot, HR join, per-track recording, OpenAI file transcription after the call; Send to BU head; consent records, retention, AI usage register |
| **UI** | Welcome and sign-in; console shell and role homes; BU head job request, chat, understanding, Edit and Approve; HR home with BU, job and candidate lists and filters; CV upload; fit analysis with charts and gates; HR round screen with checklist and notes; evaluation review; BU head results, comparison, decision; org dashboard; transparency; user and BU management; badges |
| **Directories** | `backend/org/`, `backend/hiring/`, `backend/blueprint/clarify.py` + `documents.py`, `frontend/console/src/features/{auth,jobs,screening,hr-round,results,org,transparency,admin-users}/`, `frontend/console/src/shell/`, `backend/tests/{org,hiring}/` |

### Track I: Interview

| Layer | Work |
| --- | --- |
| **Models** | MITRA realtime (plan-bound questioning, withdrawal confirmation, HR findings into the blueprint); the transcript record, raw and cleaned; the auditor (record only); the Arbiter for watchers' notes; report scoring with the Integrity section; screen-frame vision; second-voice check; log analysis; text-model slots and `check_models.py` |
| **Backend** | Technical booking and two links; the event stream, relay and store; live view hidden join; proctoring event intake and after-call analysis jobs; recording to durable storage; analytics aggregation over telemetry, stages and decisions |
| **In the browser** | MediaPipe face and iris landmarks for distance and gaze; tab, focus, share, monitor, full-screen and paste signals; capped reminder captions |
| **UI** | Candidate app (join, consent, device check, warm-up, in-call without captions, rejoin, thank-you); live view; Check Intelligence; report component with evidence jumps and Integrity; system and business analytics dashboards; the design system |
| **Directories** | `backend/bot/`, `backend/blueprint/generate.py` + `refine.py`, `backend/feedback/`, `backend/proctoring/`, `backend/live/`, `backend/analytics/`, `frontend/candidate-app/`, `frontend/ui/`, `frontend/console/src/features/{live,report,analytics}/`, `backend/tests/{bot,proctoring,live,analytics}/` |

### Why this split is the faster one

- **No waiting on the other person.** In a model/UI split, every screen waits
  for a model and every model waits for a screen. Here one person carries a
  feature from the prompt to the button. The only hand-offs are the seams in
  section 5, and each has a fixture from day one.
- **Both get hard technical work.** H takes auth and scoping, the stage
  machine, screening quality and fairness, and HR round recording and
  transcription. I takes realtime voice, the transcript record, the auditor and
  Check Intelligence, in-browser vision, and event streaming.
- **Both get UI.** H builds the console screens people use every day. I builds
  the candidate app, live view, report and dashboards.
- **Each tests alone** (section 8).

### Shared, change-controlled

These are touched by one PR at a time and reviewed by both engineers:

- `backend/shared/` (contracts, events, stage machine)
- `backend/app/main.py`, `backend/app/db.py`, `backend/app/interviews.py`,
  `backend/app/meeting.py`
- `backend/migrations/`
- `backend/pyproject.toml` + `uv.lock`
- `backend/Dockerfile`
- consent copy
- `CLAUDE.md`, this file, `docs/TECH_SPEC.md`

## 5. The seams between the tracks

These are written together in phase 0 in `backend/shared/` (Python) and
`frontend/ui/contracts/` (TypeScript types generated from it). Each comes with a
fixture, and each side builds against the fixture until the real thing lands.

| Seam | Provided by | Used by | Fixture until then |
| --- | --- | --- | --- |
| `current_user()`, role guard, `bu_scope()` | H | I's routes (live view, report, analytics) | Phase 0 deliverable, so no fixture needed |
| `CandidateStage` + `move(candidate, to, by, reason)` | H | I moves `technical_booked`, `technical_done` | Phase 0 deliverable |
| `HRRoundEvaluation` | H | I's blueprint | A JSON fixture per sample CV |
| `book_technical(candidate, duration) -> TwoLinks` | I | H's HR screen ("Book technical") | Stub returning fake links |
| `FeedbackReport` + `IntegrityReport` and `<InterviewReport>` | I | H's BU head results page embeds it | A fixture report; the component renders it |
| `InterviewEvent` stream | I | I's own live view and analytics | `replay_events.py` |
| `Decision` table | H | I's business analytics | Seeded decisions |
| `JoinResponse.round` (`hr` or `technical`) | H sets it at booking | I's candidate app picks its mode | Both values in fixtures |

Rules for every seam:

- **Additive only.** A new optional field is a normal PR. Renaming or removing
  a field bumps `schema_version`, and both engineers must agree.
- **Every model claim about a person carries its evidence.** Quotes are checked
  in code against the CV or transcript; anything unverifiable is dropped, and a
  score left with no evidence becomes "insufficient evidence".
- **Emission never blocks the call.** `emit()` is non-blocking, with a bounded
  queue that drops on overflow and counts the drops; a test asserts this.

## 6. How merge conflicts are avoided

| Rule | Why |
| --- | --- |
| **Directories are owned** (section 4). Each feature folder belongs to one track | Each side's files are disjoint |
| **Routes register themselves.** Backend: phase 0 adds one `include_router` per package (`org`, `hiring`, `live`, `proctoring`, `analytics`, `candidate`), then nobody edits `main.py` for routes. Console: each feature folder exports `routes.tsx` and `nav.ts`, collected with `import.meta.glob`, so adding a screen never touches a shared file | Removes the two most likely collision points |
| **The console shell is H's; the design system is I's.** A new component goes in its own file in `frontend/ui/`, in a small PR the other engineer reviews | Two people editing one component file collide |
| **One migration chain.** Each track's models live in its own `models.py` on the shared `Base`. Alembic revisions are generated only on a branch freshly rebased on `dev`, one migration PR at a time, merged alone | Two parallel migrations make two heads |
| **Contracts change first, alone.** A `shared/` PR merges before the feature that needs it | The seam never moves inside a large diff |
| **No shared append-only files.** `changes/<date>-<slug>.md` instead of `CHANGELOG.md` | The one file both would touch daily |
| **Dependencies in their own PR.** Resolve a `uv.lock` conflict by running `uv lock`; resolve an npm lockfile conflict by reinstalling | Lockfile conflicts stay mechanical |
| **Two frontend apps, two lockfiles.** `frontend/ui/` is imported by path | One workspace lock would conflict on every install |
| **Tests live with their track** (directories in section 4) | New test files never collide |
| **Short branches, daily rebase.** `h/<topic>` and `i/<topic>`, one to three days | Drift turns small conflicts into large ones |

Files that can still collide:

| File | Owner | How the other side changes it |
| --- | --- | --- |
| `app/interviews.py` | shared | H adds the `round` field and skips spawning a bot for `hr`; I owns technical booking and spawning |
| `app/meeting.py` (Daily rooms, tokens) | shared | Both call it; changes in a small PR |
| `bot/presence.py` | I | Ignores the live view's hidden join; agreed in phase 0 |
| `blueprint/` | split by file | `clarify.py` and `documents.py` are H's; `generate.py` and `refine.py` are I's |
| Consent copy | I | Changed in the PR of the feature it describes. H reviews the HR round wording |
| Old `frontend/admin/` (H), old `frontend/candidate/` (I) | | Frozen except for fixes until cutover |

## 7. Proctoring

The principle is to measure behaviour, never emotion. Evidence goes to a person
in its own report section. Reminders are captions the candidate sees; Mitra
never knows about them.

### During the technical round (in the candidate's browser)

| Signal | How | Reminder caption (max 3 per kind, 2-minute cooldown) |
| --- | --- | --- |
| Distance outside 50–80 cm | MediaPipe Face Landmarker: iris diameter (~11.7 mm) in pixels → cm | "Please sit back a little" / "Please come a little closer" |
| Looking away from the screen | Iris and head pose against the warm-up baseline; sustained or repeated only | "Please keep your eyes on the screen" |
| Face missing or a second face | Face count | "We can't see you clearly" |
| Interview tab hidden or unfocused | `visibilitychange`, `blur` | "Please return to the interview tab" |
| Share stopped or not the whole screen | Track `ended`; `displaySurface !== "monitor"` | Technical prompt, uncapped |
| Second monitor attached | `screen.isExtended` (Chrome, Edge) | Checked at device check; logged if it changes |
| Left full screen; paste | `fullscreenchange`; `paste` | Logged only |

Gaze and distance run in the candidate's browser, so they cost the server
nothing. Events stream to the server timestamped against the interview clock,
each marked `nudged` or `recorded_only`. Once a kind has had its three
reminders, the browser keeps recording that kind silently.

### Device check (blocking, before the interview starts)

The check covers camera, mic level, speaker, lighting, the distance band, "show
both hands", whole-screen share, a single monitor, and a supported desktop
browser. Each item passes or says how to fix it. An **accommodation option** at
consent turns off gaze reminders; it is recorded and shown on the report.

### After the call (from the recording)

Scheduled the moment the recording is ready, the same pattern as feedback
scoring:

- Screen frames go to the vision model, which labels other apps, chat or AI
  assistant windows, and notes.
- The candidate's own audio track is checked for a second voice.
- Gaze patterns are re-checked against the browser's events.
- The Integrity section gets a summary line, then each event with its
  timestamp, clip or thumbnail, and whether a reminder was shown.

### Limits

- A phone on the desk is invisible to all of this, and so are overlay copilots
  built to hide from screen share.
- Signals from the conversation itself come later, as evidence only.
- Glasses, lighting, webcams and neurodivergent traits produce false signals,
  which is why no event is ever an accusation.

### Pass bar (staged sessions)

Each of these must be caught at the right time:

- a phone glance held for 10 seconds;
- leaning in to 30 cm;
- a chat window on the shared screen;
- switching tabs;
- a second person speaking.

A normal session with ordinary thinking glances must produce at most one
reminder.

## 8. Testing

### The baseline, before anyone branches

Both engineers run `dev` as it is and hold one real interview each. This is the
original code both tracks compare against, and it proves each machine's setup.
The steps are in the README; in short:

```bash
git switch dev && cd backend && uv sync && uv run pytest -q   # 782 pass
uv run python scripts/realtime_smoke.py
uv run uvicorn app.main:app --port 8000 --reload               # /admin, then the candidate link
```

Each engineer uses **their own API keys**, so spending is attributable and one
rate limit never stalls the other.

### The old flow stays runnable throughout

- The new apps live at `/console` and `/interview`, beside `/admin` and `/join`.
- New behaviour sits behind flags that default off: `CONSOLE_ENABLED`,
  `LIVE_VIEW_ENABLED`, `PROCTORING_ENABLED`, `AUDITOR_ENABLED`.
- Until the cutover in phase 3, the original interview runs end to end on any
  branch.

### Track H tests alone

| Need | How |
| --- | --- |
| Roles and scoping | `scripts/seed_org.py`: one admin, one HR, two BU heads in different BUs, sample jobs. The route sweep checks every role against every route |
| Job chat and screening quality | `scripts/screen_cv.py <jd> <cv>` prints the `FitAnalysis` and every failed quote. A labelled set of JD/CV pairs is scored offline. A fairness check runs the same CV with names and genders swapped and must produce the same score |
| A real HR round | Two browsers: HR signed in to the console, the candidate in a private window on the old `/join` page (no bot joins `hr` rooms) until I's app has its HR mode |
| HR evaluation without holding a round | A two-speaker transcript fixture through `evaluate_hr_round()` |
| Results pages before I's report exists | The fixture `FeedbackReport` + `IntegrityReport`, rendered by `<InterviewReport>` from phase 0 |
| Booking before I's booking exists | The `book_technical` stub |

### Track I tests alone

| Need | How |
| --- | --- |
| A candidate ready for the technical round, without H's flow | `scripts/seed_candidate.py`: a candidate at `ready_for_technical` with a sample blueprint and an `HRRoundEvaluation` fixture, plus join credentials |
| Real interviews | The candidate app against a local server; the old `/admin` booking or the seed script |
| Proctoring | The staged sessions in section 7, recorded once and replayed against the analysis jobs |
| The live view without a call | `scripts/replay_events.py` through the real relay; `scripts/fake_bot.py` for notes |
| The report component | A dev route `/console/#/dev/report/<fixture>` rendering every fixture report |
| Analytics | Seeded telemetry and decisions |
| Behaviour | Text-mode scripted candidates against the realtime model: off-plan rate, follow-up cap, withdrawal confirmation, stacking, fabrication |
| Transcript accuracy | 5 recorded sessions with Indian-English speakers: realtime transcript against an offline transcription of the same recording. Report quotes depend on this |
| Reply time | Every live turn records the wait and its parts (end-of-speech detection, model to first sound); read in Check Intelligence |

### Together

- **Railway `dev` environment**, deployed from the `dev` branch with its own
  Postgres: the integrated build on a real URL.
- **CI** on every PR into `dev`: `uv run pytest -q`, plus the typecheck and
  build for each frontend.
- **Integration checkpoint** at the end of each phase, with both engineers on
  one live run.

## 9. Where the tracks affect each other

| Interaction | Risk | Handled by |
| --- | --- | --- |
| Seam shapes | A screen shows a field that was renamed | Additive-only rule; a contract test both suites run |
| Stage machine | A move the other screen does not expect | One definition; every move goes through `move()`, which checks it |
| Database | Two migration heads | One chain, migration PRs merged alone |
| The report inside H's results page | Layout or data mismatch | `<InterviewReport>` takes only the contract types; fixtures render from phase 0 |
| Live view hidden join | Presence counts the watcher as a participant | Token `user_id` prefix plus hidden presence; presence ignores it |
| `hr` rooms | A bot spawns into the HR round | `round` field checked in the join path; a test for each round |
| Event volume | A slow relay stalls the call | Non-blocking emit, asserted in a test |
| CPU | Video analysis next to live bots | Gaze runs in the browser; recording analysis runs after the call |

## 10. Phases

```
main (production) ◄── stg (staging, auto-deploys) ◄── dev (integration) ◄── h/*  i/*
```

Feature PRs merge into `dev` when CI is green and the other engineer has looked
at anything in the shared list. Each phase ends with a live checkpoint on the
`dev` environment. After a checkpoint passes, `dev` is promoted to `stg` and
then `main`, **only when no interview is live.**

| Phase | Track H | Track I | Checkpoint |
| --- | --- | --- | --- |
| **0. Foundation** (week 1) | Auth, users, roles, BUs, scoping, route sweep; Alembic; stage machine and `move()`; console shell with self-registering routes; `seed_org.py` | Contracts and fixtures, written together; text-model slots, request helper, `check_models.py`; design system and `<InterviewReport>` on fixtures; candidate-app scaffold; CI; `seed_candidate.py` | Three seeded accounts sign in to role-scoped homes; the fixture report renders; CI green |
| **1. Front doors** (weeks 2–3) | Job request, chat, understanding, Approve; HR lists; CV upload; screening with identity hidden and checked quotes; fit screen, charts, gates; 10 questions with reasons | Candidate app: join, consent with accommodation, the full device check, warm-up, no captions; proctoring capture in the browser and capped reminders; withdrawal confirmation; plan-bound questioning; raw transcript in spoken order | A BU head raises a job and HR sees an evidenced fit score. Separately, a candidate passes the device check and a staged session raises the right reminders |
| **2. Rounds** (weeks 3–5) | HR round rooms, HR join, per-track recording, transcription, evaluation, review, decisions, audit log; Send to BU head; badges | Technical booking with two links; event stream and relay; live view v1; auditor (record only); Check Intelligence for one interview; cleaned transcript; transcript accuracy check; Integrity section; report component with evidence jumps; duration per booking | A real HR round is evaluated and moves the candidate on. Separately, a real technical round is watched live and its report opens with working evidence links |
| **3. Joined up** (weeks 5–7) | BU head results embedding the report; comparison; decision; HR findings handed to I | HR findings into the blueprint; Arbiter and watchers' notes; after-call screen and voice analysis; rejoin; thank-you | **One candidate from CV to a BU head decision on the new apps, proctored, with no API calls by hand.** Cutover from `/admin` and `/join` |
| **4. Org view** (weeks 7–8) | Org dashboard; transparency and audit log; AI usage register; consent records, retention, deletion; user and BU management | Check Intelligence across interviews; system and business analytics dashboards; log analysis | An org admin can answer "what happened, what did it cost, who decided" for any candidate |
| **5. Production and polish** (weeks 9–10) | Full UI polish of the console; accessibility; realistic org data | UI polish of the candidate app, live view and dashboards; session hand-over past 55 minutes; bot workers separate from the web process; load at the concurrency cap | Pilot with one real BU on the deployed URL |

Weeks are a rough guide. The phases overlap: either engineer can start the next
phase's work while the other closes the checkpoint.

## 11. Working with two Claude accounts

- **Shared knowledge lives in the repo:** CLAUDE.md for rules, this file for
  the plan. A decision made in one chat is unknown to the other's Claude until
  it is written here.
- **Each engineer keeps a personal `CLAUDE.local.md`** at the repo root
  (gitignored). Track H's:

  ```markdown
  # Track H (hiring flow). Read docs/DEV_PLAN.md first.
  - I own backend/org/, backend/hiring/, blueprint/clarify.py and documents.py,
    frontend/console/src/shell/ and the console feature folders auth, jobs,
    screening, hr-round, results, org, transparency, admin-users, and
    backend/tests/org/ and backend/tests/hiring/.
  - Do not edit backend/bot/, backend/feedback/, backend/proctoring/,
    backend/live/, backend/analytics/, frontend/candidate-app/ or frontend/ui/.
    Need a change there? Write it up for the other engineer.
  - Use I's seams through their fixtures until the real ones land:
    book_technical stub, <InterviewReport> with fixture reports.
  - backend/shared/, migrations and the shared list in DEV_PLAN.md section 4 are
    their own PR, reviewed by the other engineer, merged before the feature.
  - Branch from dev as h/<topic>. Rebase on dev before starting work.
  - Add changes/<date>-<slug>.md instead of editing CHANGELOG.md.
  ```

  Track I's is the mirror image.
- **Start each session the same way:** `git fetch && git rebase origin/dev`,
  read this file, run the suite.
- **Cross-track requests** go in an issue or a PR note, never as a cross-track
  edit.

## 12. Open items

1. **Assign tracks.** Decide who takes H and who takes I, with GitHub
   usernames; then add `CODEOWNERS` and each engineer's `CLAUDE.local.md`.
2. **Push and branch.** After the live test, push `dev` and create
   `h/foundation` and `i/foundation`.
3. **Railway `dev` environment.** Create it.
4. **Text model.** Confirm `gpt-6.2-sol` is callable; until then the default is
   `gpt-6.1-sol`.
5. **Retention.** Set retention periods for recordings, transcripts and
   thumbnails, and get a legal read of the consent copy before the first real
   candidate.
