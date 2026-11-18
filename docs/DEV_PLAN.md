# Development Plan: the MITRA organisation platform

> **Status: agreed scope, 2026-11-18.** Branch `dev`. This is the shared brief
> for both engineers and both Claude accounts. Claude memory is per account and
> is not shared, so **anything both sides must know lives here or in CLAUDE.md,
> never only in one person's memory.** Update this file in the same PR as any
> change that alters what it says.
>
> This replaces the earlier single-employer version of this plan. The realtime
> engine, the event stream, the Director and the live view from that version are
> all still here; they now sit inside a larger product.

## 1. What we are building

An organisation's end-to-end hiring platform for proctored interviews. One
organisation per deployment. Three kinds of signed-in user, one candidate.

| Who | Sees | Does |
| --- | --- | --- |
| **BU head** (Business, Finance, Software, Data Science, ...) | Their own BU only | Raises job requests; reads results for their candidates; makes the final decision after the technical round; may watch a technical round live |
| **HR** | Every BU's job requests, grouped by BU | Uploads CVs, runs screening, conducts the HR round, books the technical round, sends results to the BU head; may watch a technical round live |
| **Org admin** | Everything, read-only, plus admin sections | System analytics, business analytics, transparency (transcripts, audit log), users and BUs |
| **Candidate** | Their own join page | Joins the HR round and the technical round with a meeting ID and password |

### The journey

```
BU head                      HR                                   MITRA                 BU head
───────                      ──                                   ─────                 ───────
New job request              BU list → job request
  title (required)             Upload CV → Re-upload | Proceed
  JD file (optional)                 │
  chat with the model          Fit analysis: score %, chart, reasoning,
  "what MITRA understood"        10 HR-round questions
  Edit ↺ | Approve ───────►          │
                               score < 50% → [Reject] | Proceed anyway (reason)
                               score ≥ 50% → Not now | Begin HR round
                                     │
                               HR round: meeting ID + password, questions
                               (HR and candidate only; recorded, no AI voice)
                                     │
                               Evaluation updates the rubric and recommends
                               → HR: [Reject] | Proceed to technical
                                     │
                               Book technical: duration → two links ──► Proctored technical
                                 candidate link + live view link         interview
                                                                              │
                               Review result ◄─────────────────────── report + integrity
                               Send to BU head ───────────────────────────────────────► Results
                                                                                        Shortlist |
                                                                                        On hold |
                                                                                        Reject
```

### Candidate status

One field, one state machine, defined in `shared/` and read by every screen.
Only the listed moves are allowed, and every move records who made it, when,
the model's recommendation at that moment, and the reason if one was required.

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
| 4 | Who rejects: **HR** at screening and after the HR round. After the technical round HR can only **send to the BU head**, and the **BU head** decides. A model only recommends |
| 5 | CV screening cut-off **50%**, one setting (org level now, per job later). It changes which buttons are offered; "Proceed anyway" needs a reason |
| 6 | **The HR round is held inside MITRA**, in our own room with the same join page and credentials. No AI participant joins. It is recorded, transcribed after the call, and evaluated by a text model |
| 7 | **Live view and notes** for the technical round, open to HR and to the BU head of that job's BU. Notes only; nobody speaks into the call |
| 8 | **Proctoring goes in its own Integrity section** of the report, with timestamps and clips. It never changes a competency score |
| 9 | **Distance band 50–80 cm**, measured in the browser from iris size, plus a "show both hands" step before the interview starts |
| 10 | Integrity reminders are **on-screen captions only, at most three per kind**, then record silently. Never spoken, never in Mitra's context |
| 11 | **Withdrawal always gets one confirmation**, even after "just end the interview". Any yes, or a second request, ends it |
| 12 | Candidates are in **India**: DPDP Act 2023 consent and retention. No emotion or tone inference anywhere |
| 13 | **No email notifications** in the first version; in-app badges only |
| 14 | Off-call text work uses **one configurable text model** (section 2) |
| 15 | React + Vite + TypeScript for the console and the candidate app |

## 2. Models

| Role | Env slot | Default | Notes |
| --- | --- | --- | --- |
| Live conversation (technical round) | `REALTIME_MODEL` | `gpt-realtime-2.1` | Unchanged |
| Live judge for the Director | `OPENAI_LLM_MODEL` | `gpt-4.1-mini` | On the call's timeline: never a reasoning model |
| Job understanding + BU head chat | `OPENAI_CLARIFY_MODEL` | `OPENAI_TEXT_MODEL` | New slot; `blueprint/clarify.py` |
| CV fit screening + 10 HR questions | `OPENAI_SCREENING_MODEL` | `OPENAI_TEXT_MODEL` | New |
| HR round evaluation | `OPENAI_HR_EVAL_MODEL` | `OPENAI_TEXT_MODEL` | New |
| Technical blueprint | `OPENAI_BLUEPRINT_MODEL` | `OPENAI_TEXT_MODEL` | Exists; default changes |
| Technical report + log analysis | `OPENAI_FEEDBACK_MODEL` | `OPENAI_TEXT_MODEL` | Exists; default changes |

Each role keeps its own slot so any one can be moved or measured alone, which is
the existing rule in CLAUDE.md. `OPENAI_TEXT_MODEL` sets them all at once.

**GPT-6.2 Sol is the intended text model, but it is not confirmed yet.** On
2026-11-18, OpenAI's API docs list `gpt-6-sol` and point to `gpt-6.1-sol` as the
newer Sol model. `gpt-6.2-sol` appears only in third-party reports. So:

- The code default is `gpt-6.1-sol`, and `OPENAI_TEXT_MODEL=gpt-6.2-sol` switches
  once it appears in the account's model list.
- `scripts/check_models.py` (Track C, phase 0) lists what the key can actually
  call, so this is a check rather than a guess.

**Switching these calls to a reasoning model is a code change, not only a
setting.** Today `clarify.py`, `generate.py`, `refine.py` and `score.py` pass
`temperature`, which reasoning models reject, and use `json_object` rather than
a JSON schema. Phase 0 adds one helper for the request arguments, the way
`bot/brain/drivers.py` already handles it, and moves each call to structured
outputs. Re-run the existing quality checks after the switch: the clarify
attribution test, judge precision, and quote verification.

## 3. The split

Two tracks, divided by **who the code serves**: models and the candidate on one
side, the organisation's people on the other. Each track is development plus
its own research.

### Track C: candidate and intelligence

Everything a model produces and everything the candidate touches.

- **Screening** (`backend/screening/`, new):
  - `analyse_fit(job, cv) -> FitAnalysis`. Identity is removed before scoring.
    The model rates each competency 0–4, and must quote the CV for every rating;
    the quotes are checked against the CV in code. The percentage is computed in
    code from the ratings and the BU head's weights. "No evidence" is a separate
    outcome from a low score.
  - `hr_questions(job, cv, fit) -> HRQuestionSet`: 10 questions, each with a
    one-line reason for asking it.
- **Job understanding** (`backend/blueprint/clarify.py`): the BU head's chat,
  which already works. It moves to the new text model, and the optional JD file
  becomes a starting point rather than a requirement.
- **HR round intelligence**:
  - Transcription after the call, per participant track (section 7).
  - `evaluate_hr_round(transcript, questions, job) -> HRRoundEvaluation`, using
    verified quotes as feedback scoring does today.
- **MITRA technical round** (`backend/bot/`):
  - The HR round's findings go into the blueprint.
  - Withdrawal confirms once.
  - The Director, the Deepgram independent transcript, event emission, and note
    delivery from the earlier plan.
- **Technical report** (`backend/feedback/`): an Integrity section beside the
  competency scores, and log analysis.
- **Candidate app** (`frontend/candidate-app/`, new):
  - join, consent, device check (camera, mic, speaker, lighting, the 50–80 cm
    band, hands, whole-screen share, single monitor, browser), a warm-up, the
    in-call screen with captions, and thank-you;
  - the same app for both rounds.
- **Proctoring capture** (`backend/proctoring/` + the candidate app): the
  browser signals, gaze estimation, the capped reminders, and post-call
  analysis of the recording (section 6).
- **Research:** screening fairness and accuracy on real CVs; evaluating the HR
  round; confusion scenarios on realtime; gaze thresholds from staged sessions.

### Track E: organisation and console

Everything a BU head, HR or an org admin sees or does.

- **Identity and access** (`backend/console/auth.py`):
  - users with roles, BU membership and `org_id`;
  - password sign-in with hashed passwords and httpOnly session cookies;
  - BU scoping in every query;
  - a route sweep test that replaces today's admin-only one.
- **Organisation data** (`backend/console/models.py`): `Organisation`,
  `BusinessUnit`, `User`, job request fields on `Job` (BU, creator, status),
  candidate stage, `Decision`, and the audit log. **Versioned database changes
  with Alembic**: today tables are only ever created, never altered, which
  cannot carry this scope.
- **Console** (`frontend/console/`, new), one app with role-based home screens:
  - **Welcome and sign-in.**
  - **BU head:** job requests and their status, a new request with the chat
    UI, "what MITRA understood" with Edit and Approve, candidate results, a
    comparison across candidates, and the detailed report.
  - **HR:** BUs → job requests → candidates with status chips and filters; CV
    upload with Re-upload or Proceed; the fit analysis screen and its gates; the
    HR round screen (credentials to send, the question checklist, notes);
    reviewing the HR round evaluation; booking the technical round with its two
    links; Send to BU head.
  - **Org dashboard and admin:** the system, business and transparency
    sections, and user and BU management.
- **Live view** (`backend/live/`, from the earlier plan): event relay and store,
  the Arbiter for notes, the hidden join, and the screen itself, open to HR and
  the BU head.
- **Analytics** (`backend/console/analytics.py`): aggregates over interview
  telemetry, decisions and stages.
- **Proctoring review UI:** the Integrity section on the report, with event
  timeline, clips and thumbnails.
- **Design system** (`frontend/ui/`): tokens and components used by both apps.
  E builds it; C uses it.
- **Research:** what each role needs on its home screen; Arbiter quality; how
  the comparison and evidence views read to a real BU head.

### Shared, change-controlled

Touched by one PR at a time, reviewed by both engineers:

`backend/shared/` (contracts, events, stage machine), `backend/app/main.py`,
`backend/app/db.py`, `backend/app/interviews.py` (booking is E's, joining and
spawning are C's), `backend/migrations/`, `backend/pyproject.toml` + `uv.lock`,
`backend/Dockerfile`, `frontend/ui/`, consent copy, `CLAUDE.md`, this file,
`docs/TECH_SPEC.md`.

## 4. The seam: contracts

All in `backend/shared/`, written together in phase 0 with a fixture for each,
before anyone builds on them. E builds every screen against the fixtures; C
replaces fixtures with real output. Neither waits for the other.

| Contract | Written by | Read by |
| --- | --- | --- |
| `CandidateStage` + allowed moves | E | C (to know what to trigger), every screen |
| `FitAnalysis`: per-competency rating, CV quotes, computed %, assessed share of weight, reasoning, "no evidence" items | C | E: fit screen, HR home, comparison |
| `HRQuestionSet` | C | E: HR round screen |
| `HRRoundEvaluation`: soft-skill rubric, verified quotes, recommendation | C | E; C's blueprint |
| `FeedbackReport` + `IntegrityReport` | C | E: BU head report |
| `InterviewEvent` stream (transcript, plan, Director, notes, health, proctoring, logs) | C | E: live view, system analytics |
| `EmployerNote` → `NoteInstruction` | E | C delivers into the live session |
| `Decision`: who, stage, outcome, reason, model recommendation | E | Org analytics, audit log |

Rules for every contract:

- **Additive only.** A new optional field is a normal PR. Renaming or removing
  a field bumps `schema_version`, and both engineers must agree.
- **Every model-produced claim about a person carries its evidence.** Quotes
  are verified in code against the source (CV or transcript). Anything
  unverifiable is dropped, and a score left with no evidence becomes
  "insufficient evidence". This is the existing quote rule, extended to
  screening and the HR round.
- **Emission never blocks the call.** `emit()` is non-blocking, with a bounded
  queue that drops on overflow and counts the drops; a test asserts this.

## 5. How merge conflicts are avoided

| Rule | Why it prevents conflicts |
| --- | --- |
| **New code goes in new directories.** C: `screening/`, `proctoring/`, `frontend/candidate-app/`, plus its existing `bot/`, `blueprint/`, `feedback/`. E: `console/`, `live/`, `frontend/console/`, `frontend/ui/` | Each side's files are disjoint |
| **Routes register once.** Phase 0 adds `include_router` lines for `console`, `live`, `screening` and `candidate` to `main.py`, each with a stub. After that nobody edits `main.py` to add a route | `main.py` is the most likely collision point |
| **C exposes functions; E wires them.** C writes `analyse_fit()`, `hr_questions()`, `evaluate_hr_round()`. E calls them from routes and background tasks. C never edits a console route; E never edits a prompt | Removes the reason to cross over |
| **Tables live with their track; migrations are one chain.** Models sit in each track's `models.py` on the shared `Base`. Alembic revisions are generated only on a branch freshly rebased on `dev`, one migration PR at a time, merged alone | Two parallel migrations make two heads, which cannot be merged mechanically |
| **Contracts change first, alone.** A `shared/` change is its own PR, merged before the feature that needs it | The seam never moves inside a large diff |
| **No shared append-only files.** Each PR adds `changes/<date>-<slug>.md` instead of editing `CHANGELOG.md` | The one file both would touch daily |
| **Dependencies in their own PR.** On rebase, resolve a `uv.lock` conflict by running `uv lock`, never by hand | Lockfile conflicts stay mechanical |
| **Separate frontend apps, separate lockfiles.** `frontend/ui/` is imported by path, not published | One workspace lock would conflict on every install |
| **Tests live with their track.** `tests/console/`, `tests/live/`, `tests/screening/`, `tests/candidate/`, `tests/proctoring/` | New test files never collide |
| **Short branches, daily rebase.** `c/<topic>` and `e/<topic>`, one to three days | Drift turns small conflicts into large ones |

Files that can still collide:

| File | Owner | How the other side changes it |
| --- | --- | --- |
| `app/interviews.py` | shared | Booking, two links and HR's own join token are E's; candidate join and bot spawn are C's |
| `app/db.py` | shared | Phase 0 makes it import each track's model module; after that, rarely touched |
| `bot/presence.py` | C | E needs it to ignore the live view's hidden join. Agreed in phase 0 |
| `bot/realtime_coordinator.py` | C | E never edits it; notes arrive through the inbox |
| Consent copy | C | Changed only in the PR of the feature it describes; E reviews |
| `frontend/ui/` | E | C asks for a component, or adds one in a small PR |
| Old `frontend/admin/` (E), old `frontend/candidate/` (C) | | Frozen except for fixes until cutover |

## 6. Proctoring

The principle is to measure behaviour, never emotion. Evidence goes to a person
in its own report section. Reminders are captions the candidate sees; Mitra
never knows about them.

### During the technical round (in the candidate's browser)

| Signal | How | Reminder caption (max 3 per kind, 2 min cooldown) |
| --- | --- | --- |
| Distance outside 50–80 cm | MediaPipe Face Landmarker: iris diameter (~11.7 mm) in pixels → cm, with a per-session check against a known face width | "Please sit back a little" / "Please come a little closer" |
| Looking away from the screen | Iris and head pose relative to a baseline set during the warm-up; only sustained or repeated look-aways count | "Please keep your eyes on the screen" |
| Face missing or a second face | Face count | "We can't see you clearly" |
| Interview tab hidden or unfocused | `visibilitychange`, `blur` | "Please return to the interview tab" |
| Screen share stopped or not the whole screen | Track `ended`; `displaySurface !== "monitor"` | Technical prompt, uncapped |
| Second monitor attached | `screen.isExtended` (Chrome, Edge) | Checked at device check; logged if it changes |
| Left full screen; paste | `fullscreenchange`; `paste` | Logged only |

- Gaze and distance run **in the candidate's browser**, which costs the
  server nothing.
- Events stream to the server timestamped against the interview clock, and
  each counts as `nudged` or `recorded_only`.
- After the third reminder of a kind, the browser keeps recording that kind
  silently.

### Device check (before consent is complete, blocking)

The check covers camera, mic level, speaker, lighting, distance band, "show both
hands", whole-screen share, a single monitor, and a supported desktop browser.
Each check either passes or says how to fix it. An **accommodation option** at
consent turns off gaze reminders; it is recorded and shown on the report.

### After the call (from the recording)

Analysis is scheduled the moment the recording is ready, the same pattern as
feedback scoring:

- **Screen frames** go to a vision model, which labels other apps, chat or AI
  assistant windows, and notes.
- **The candidate's own audio track** is checked for a second voice.
- **Gaze patterns** are re-checked against the browser's live events.
- **The Integrity section** gets a summary line, then each event with its
  timestamp, clip or thumbnail, and whether a reminder was shown.

### Limits

- A phone on the desk and overlay copilots built to hide from screen share are
  invisible to all of this.
- Conversation-based signals (answers that sound read out, pauses before
  polished answers) come later, as evidence only.
- Glasses, lighting, webcams and neurodivergent traits produce false signals,
  which is why no event is ever an accusation and nothing reaches a score.

### Pass bar (staged sessions)

These must each be caught at the right time:

- a phone glance held for 10 seconds;
- leaning in to 30 cm;
- a chat window on the shared screen;
- switching tabs;
- a second person speaking.

A normal session with ordinary thinking glances must produce at most one
reminder.

## 7. The HR round

- **Booking** creates a room with candidate credentials, exactly as today, plus
  an HR join from the console (a signed-in, owner-level token). No bot is
  spawned.
- **Recording** uses Daily raw-tracks, one file per participant. This makes
  speaker attribution exact without diarisation.
  - Recording is required for the HR round regardless of `RECORDING_ENABLED`,
    and the consent notice says so.
  - Verify the current Daily recording and transcription API before building.
    Daily's own Deepgram-backed transcription is the alternative if per-track
    files are awkward.
- **After the call:** each track is transcribed with Deepgram's pre-recorded
  API and merged into one `Transcript` with real timestamps. Then
  `evaluate_hr_round()` runs, the rubric updates, and the candidate moves to
  `hr_done`.
- **During the call,** HR's screen shows the credentials, the 10 questions as a
  checklist HR ticks, and a notes box. A live transcript there is a later
  option (Daily live transcription); it is not needed for the evaluation.
- **No proctoring** in the HR round beyond the recording.

## 8. Testing live, before and during the split

### Baseline, before anyone branches

Both engineers run `dev` as it is and hold one real interview each. This is the
original code both tracks compare against.

```bash
git switch dev && cd backend && uv sync && uv run pytest -q   # 782 pass
uv run python scripts/realtime_smoke.py
uv run uvicorn app.main:app --port 8000 --reload               # /admin, then the candidate link
```

Each engineer uses **their own API keys**, so spending is attributable and one
rate limit never stalls the other.

### The old flow stays runnable throughout

- The new apps live at **new paths**, `/console` and `/interview`, beside
  `/admin` and `/join`.
- New behaviour sits behind flags that default off: `CONSOLE_ENABLED`,
  `LIVE_VIEW_ENABLED`, `PROCTORING_ENABLED`, `DEEPGRAM_RECORD_TRANSCRIPT`.
- On any branch, the original interview runs end to end until the cutover in
  phase 3.

### Track C

| Need | How |
| --- | --- |
| Screening quality | `scripts/screen_cv.py <jd> <cv>` prints the `FitAnalysis` and every quote that failed verification; a labelled set of JD/CV pairs scored offline |
| HR round evaluation without holding one | A recorded or scripted two-speaker transcript fixture through `evaluate_hr_round()` |
| A real voice call | `/admin` and the candidate link, or `scripts/dev_interview.py` |
| What the bot emits | `JsonlSink`: `sessions/<id>.events.jsonl`, `scripts/watch_events.py` |
| Proctoring | The candidate app against a local server, with the staged sessions in section 6 |
| Notes before the Arbiter exists | `scripts/send_note.py <session> "text"` |

### Track E

| Need | How |
| --- | --- |
| Every screen before C's models exist | The phase 0 fixtures for each contract |
| Roles and scoping | Seeded org: one admin, one HR, two BU heads in different BUs (`scripts/seed_org.py`) |
| The live view with no calls | `scripts/replay_events.py <file.jsonl>` through the real relay; `scripts/fake_bot.py` for the note round trip |
| A real interview in the console | `dev`'s real bot, from the E branch rebased daily |

### Together

- **A Railway `dev` environment** deployed from the `dev` branch, with its own
  Postgres, so both tracks see the integrated build on a real URL. *Someone
  with Railway access has to create it.*
- **CI on every PR into `dev`** (GitHub Actions, `uv run pytest -q`, plus each
  frontend's typecheck and build).

## 9. Where the tracks affect each other

| Interaction | Risk | Handled by |
| --- | --- | --- |
| Contract shapes | A screen shows a field that was renamed | Additive-only rule; a contract test both suites run |
| Stage machine | C triggers a move E's screen does not expect | One definition in `shared/`; every move goes through one function that checks it |
| Database | Two migration heads | One chain, migration PRs merged alone |
| Live view hidden join | Presence counts the watcher as a second participant | Token `user_id` prefix plus hidden presence; C's presence ignores it |
| Event volume | A slow relay stalls the call | Non-blocking emit, asserted in a test |
| Notes | A badly worded note derails Mitra | E triages, C wraps the final wording |
| Consent copy | Says more or less than the product does | Changed in the same PR as the feature |
| CPU | Video analysis next to live bots | Gaze runs in the browser; recording analysis runs after the call |

## 10. Phases

```
main (production) ◄── stg (staging, auto-deploys) ◄── dev (integration) ◄── c/*  e/*
```

Feature PRs merge into `dev` when CI is green and the other engineer has looked
at anything in the shared list. Each phase closes with a live checkpoint on the
`dev` environment with both engineers present. After a checkpoint passes, `dev`
is promoted to `stg`, then to `main`, **only when no interview is live.**

| Phase | Track C | Track E | Checkpoint |
| --- | --- | --- | --- |
| **0. Foundation** (week 1, together) | Contracts and fixtures; the text-model slots and request helper; `check_models.py`; screening prototype on sample CVs | Users, roles and sign-in; org, BU and stage tables; Alembic; router stubs; design tokens; CI; seed script | Three seeded accounts sign in and each sees an empty home scoped to its role; CI green |
| **1. Job to screening** (weeks 2–3) | `analyse_fit`, `hr_questions`; job chat on the text model with optional JD | BU head job request, chat UI, "what MITRA understood", approve; HR BU and job lists, CV upload, fit screen, threshold gates, decisions and audit log | A BU head raises a job; HR uploads a CV and sees a real, evidenced fit score, and rejects or proceeds |
| **2. HR round** (weeks 3–5) | Per-track transcription, `evaluate_hr_round`, HR findings into the blueprint | HR round booking and screen, HR join, evaluation review, the HR round decision | A real HR round is held in MITRA, transcribed, evaluated, and the candidate moves on or is rejected |
| **3. Technical round** (weeks 5–7) | Candidate app with device check and warm-up; proctoring capture and capped reminders; withdrawal confirmation; Integrity section | Technical booking with two links; live view for HR and the BU head; Send to BU head; BU head results, comparison and decision; cutover from `/admin` and `/join` | One candidate goes from CV to a BU head decision on the new apps, proctored, with no API calls made by hand |
| **4. Org view and depth** (weeks 7–8) | Post-call screen and voice analysis; Deepgram independent transcript; Director | Org dashboard: system, business, transparency; user and BU management; Arbiter and notes | An org admin can answer "what happened, what did it cost, who decided" for any candidate |
| **5. Production** | Session hand-over past 55 minutes; accessibility; rejoin | Bot workers separate from the web process; Railway config migration | Pilot with one real BU on the deployed URL |

Weeks are a rough guide for two engineers working in parallel.

## 11. Working with two Claude accounts

- **Shared knowledge lives in the repo:** CLAUDE.md for rules, this file for
  the plan. A decision made in one engineer's chat is unknown to the other's
  Claude until it is written here.
- **Each engineer keeps a personal `CLAUDE.local.md`** at the repo root
  (gitignored):

  ```markdown
  # Track E (organisation and console). Read docs/DEV_PLAN.md first.
  - I own backend/console/, backend/live/, frontend/console/, frontend/ui/,
    backend/tests/console/ and backend/tests/live/.
  - Do not edit backend/bot/, backend/blueprint/, backend/screening/,
    backend/feedback/, backend/proctoring/ or frontend/candidate-app/.
    Need a change there? Write it up for the other engineer instead.
  - Call Track C's functions (analyse_fit, hr_questions, evaluate_hr_round);
    never edit a prompt.
  - backend/shared/, migrations and the shared list in DEV_PLAN.md section 3
    are their own PR, reviewed by the other engineer, merged before the feature.
  - Branch from dev as e/<topic>. Rebase on dev before starting work.
  - Add changes/<date>-<slug>.md instead of editing CHANGELOG.md.
  ```

  Track C's is the mirror image.
- **Start each session the same way:** `git fetch && git rebase origin/dev`,
  read this file, then run the suite.
- **Cross-track requests** go in an issue or a PR note, never as a cross-track
  edit.

## 12. Open items

1. **Assign the tracks.** Who takes C and who takes E, with their GitHub
   usernames. Then write `CODEOWNERS` and each engineer's `CLAUDE.local.md`.
2. **Push `dev`** and create `c/foundation` and `e/foundation`, after the live
   test of `dev` passes.
3. **Create the Railway `dev` environment.**
4. **Confirm the text model.** Confirm `gpt-6.2-sol` is callable with the
   project's key; until then the default is `gpt-6.1-sol`.
5. **Retention periods** for recordings, transcripts and screen thumbnails
   under the DPDP Act, and a legal read of the consent copy before the first
   real candidate.
6. **Interviews over 55 minutes:** cap the booking, or build the session
   hand-over (phase 5).
