# Development Plan: two tracks, one product

> **Status: agreed direction, 2026-11-18.** Branch `dev`. This file is the
> shared brief for both engineers and both Claude accounts. Claude memory is per
> account and is not shared, so **anything both sides must know lives here or in
> CLAUDE.md, never only in one person's memory.** Update this file in the same
> PR as any change that alters what it says.

## 1. Where we are going

A production-grade interview product on the realtime voice engine, with a live
human supervisor and proctoring:

| Surface | User | What it does |
| --- | --- | --- |
| **Console** | Employer | Planned interviews, CV upload, the candidate profile the system understood, the interview plan, reports |
| **Candidate app** | Candidate | Join, consent, device checks (mic, speaker, echo, camera, screen, network), a calm in-call screen with captions |
| **Supervisor dashboard** | A person, on its own link | Live video, audio and screen; live transcript; the plan; Mitra's reading of each answer; live logs; a note box |

Decisions recorded on 2026-11-18 (these override the matching lines in
CLAUDE.md, which says so at the top):

1. **The realtime engine is the main engine.** `INTERVIEW_ENGINE=pipeline` is the
   fallback and the rollback. Done on `dev`.
2. **Integration branch `dev`** = `stg` + `realtime-poc`. Done.
3. **React + Vite + TypeScript** for the new console, dashboard and candidate app.
4. **The consent notice says a person may watch live.** Lands with the
   supervisor's hidden join, not before (the notice must be true).
5. **The supervisor sends notes only.** No speaking, no taking over.
6. **Proctoring:** build the initial capture-and-review layer now; gaze and
   screen analysis are planned below and built after it.

Names used everywhere below, because "supervisor" meant two things before:

| Name | What it is |
| --- | --- |
| **Mitra** | The realtime model talking to the candidate. The fast brain |
| **Director** | Our brain + judge reading the transcript and steering Mitra with notes. The slow brain |
| **Supervisor** | The human watching the dashboard |
| **Arbiter** | The secondary model that triages the supervisor's notes before they reach Mitra |

## 2. The split

Two tracks, divided by **who the code serves**, which is also how the
directories divide. Each track is development plus its own research.

### Track C: Candidate and interview intelligence

Everything that touches the candidate or decides what Mitra says.

- `backend/bot/`: the realtime engine, Director, note delivery into the live
  session, event emission, Deepgram as the independent transcript.
- `backend/blueprint/`: CV understanding (`CandidateProfile`), plan generation.
- The new candidate app (`frontend/candidate-app/`).
- Proctoring **capture**: camera and screen requirements, technical prompts in
  the browser, recording.
- Research: the confusion scenarios (noise, echo, backchannel, injection,
  mishearing), behaviour checks against the realtime prompt, tone policy.

### Track S: Supervisor and console

Everything a person on the employer side sees or does.

- `backend/supervisor/` (new): the event relay, event storage, supervisor
  access links, the Arbiter, note intake.
- The new console and supervisor dashboard (`frontend/console/`).
- The hidden supervisor join to the Daily room.
- Proctoring **review**: live tiles, the event timeline, and the after-interview
  analysis job (gaze, screen, second voice) and its report section.
- Research: Arbiter quality (does a note land without sounding like an
  interruption), what the dashboard should visualise, proctoring thresholds.

### Shared, change-controlled

Touched by one PR at a time, reviewed by both engineers:

`backend/shared/` (contracts and events), `backend/app/main.py`,
`backend/app/db.py`, `backend/pyproject.toml` + `uv.lock`, `backend/Dockerfile`,
`frontend/ui/` (design system), `CLAUDE.md`, this file, `docs/TECH_SPEC.md`.

## 3. The seam between the tracks

The tracks meet at exactly two contracts. Both are written in week 0, before
the split, and everything after builds against them.

```
            Track C (bot process)                        Track S (backend + dashboard)
  ┌──────────────────────────────────────┐        ┌──────────────────────────────────────┐
  │ Mitra · Deepgram · Director · coord. │        │ relay · event store · Arbiter · UI   │
  │                                      │ events │                                      │
  │   sink.emit(InterviewEvent) ─────────┼───────►│ EventSink impl ──► store ──► WS ──► UI│
  │                                      │        │                                      │
  │   inbox.next() ──► coordinator ◄─────┼────────┤ NoteInstruction ◄── Arbiter ◄── note  │
  └──────────────────────────────────────┘ notes  └──────────────────────────────────────┘
```

**Contract 1: `InterviewEvent`** (`backend/shared/events.py`). One versioned,
typed union of everything the bot can report: transcript partial and final (per
source: `deepgram`, `openai`), turn started and ended, reply started and ended,
Director analysis (claims, coverage, depth stage, contradictions), plan state
(section, time left, next move), note lifecycle, channel health, proctoring
signals, and log lines. Track C emits; Track S stores and renders.

- `bot/events.py` holds an `EventSink` protocol with `NullSink` (default) and
  `JsonlSink` (writes `sessions/<id>.events.jsonl`). Track S adds the relay
  sink in `backend/supervisor/`. Neither waits for the other.
- **Emission never blocks the call.** `emit()` is non-blocking with a bounded
  queue that drops on overflow and counts the drops. A test asserts this. A
  dashboard that falls over must not cost a candidate a single word.
- **Additive changes only.** A new event type or a new optional field is fine
  in a normal PR. Renaming or removing anything bumps `schema_version` and is
  agreed by both.

**Contract 2: `SupervisorNote` → `NoteInstruction`** (`backend/shared/notes.py`).
`SupervisorNote` is what the person typed. The Arbiter turns it into a
`NoteInstruction`: priority (`stop` / `next_turn` / `boundary` / `context` /
`rejected` / `stale`), the instruction text in Mitra's voice, and the reason.
Track S produces it; Track C's coordinator delivers it into the live session,
removes it once acted on, and emits the lifecycle events.

- **Track C owns the last step into the model**, so the guardrails that protect
  the conversation (never "my supervisor says", probe a contradiction at most
  once, one question at a time) live in one place, next to the other notes the
  Director inserts.
- **Track S owns triage**, including refusing notes that are biased, ask about
  protected attributes, or tell Mitra to evaluate out loud.

## 4. How merge conflicts are avoided

Conflicts come from two people editing the same lines. The plan is that they
almost never can, and that when they can, the conflict happens in a small
deliberate PR rather than inside a feature.

| Rule | Why it prevents conflicts |
| --- | --- |
| **New code goes in new directories.** S builds in `backend/supervisor/` and `frontend/console/`; C in `backend/bot/`, `backend/blueprint/`, `frontend/candidate-app/` | Each side's files are disjoint from the other's |
| **Routes register once.** Week 0 adds `app.include_router(supervisor_router)` and a candidate router to `main.py` with empty stubs. After that, nobody edits `main.py` to add a route | `main.py` is 900 lines and the most likely collision point |
| **Tables live with their track.** New SQLAlchemy models go in `backend/supervisor/models.py` and the track's own module, on the shared `Base`. Week 0 makes `db.py` import a list of model modules so `create_all` sees them | Two people adding columns to `db.py` is a guaranteed conflict |
| **Contracts change first, alone.** A change to `shared/` is its own PR, merged before the feature that needs it; both sides rebase | The shared seam never changes inside a large diff |
| **No shared append-only files.** `CHANGELOG.md` takes every change at its top, so two branches always conflict there. From now on each PR adds `changes/<date>-<slug>.md` instead; whoever promotes `dev` folds them in | Removes the one file both tracks would touch every day |
| **Dependencies in their own PR.** Adding a package is a tiny PR. On rebase, a `uv.lock` conflict is resolved by running `uv lock`, never by hand | Lockfile conflicts are mechanical; keep them out of feature work |
| **Separate frontend apps, separate lockfiles.** `frontend/console/` and `frontend/candidate-app/` each own their `package.json` and lockfile. `frontend/ui/` is a folder both import by path, not a package | One npm workspace lock would conflict on every install |
| **Tests live with their track.** `backend/tests/supervisor/` and `backend/tests/candidate/` for new tests | New test files never collide |
| **Short branches, daily rebase.** Feature branches `c/<topic>` and `s/<topic>`, one to three days, rebased on `dev` every morning | Drift is what turns a small conflict into a large one |

The files that can still collide, and who owns them:

| File | Owner | How the other side changes it |
| --- | --- | --- |
| `bot/presence.py` | C | S needs it to ignore hidden supervisors: agreed in week 0 (section 6) |
| `bot/realtime_coordinator.py` | C | S never edits it; S's notes arrive through the inbox |
| `frontend/candidate/index.html` (old page) | C | Frozen except for fixes; the new app replaces it |
| `frontend/admin/` (old panel) | S | Frozen except for fixes; the new console replaces it |
| Consent copy | C | S writes the supervisor sentence in a PR, C reviews |
| `backend/Dockerfile` | shared | Week 0 adds the Node build stage once, for both apps |

## 5. Testing live, before and during the split

### The baseline, before anyone branches

Both engineers run `dev` exactly as it is and hold one real interview each. This
is "the original code" both tracks compare against, and it proves each
machine's setup.

```bash
git switch dev && cd backend && uv sync && uv run pytest -q   # 782 pass
uv run python scripts/realtime_smoke.py                        # OpenAI accepts the settings
uv run python scripts/dev_interview.py                         # voice only, realtime engine
uv run uvicorn app.main:app --port 8000 --reload               # whole product, /admin and /join
```

Each engineer uses **their own API keys** in their own `.env`, so spending is
attributable and one person's rate limit never stalls the other.

### The old flow stays runnable throughout

New UIs are served at **new paths** (`/console`, `/supervise/<token>`,
`/interview`) beside the existing `/admin` and `/join`. Nothing old is switched
off until the cutover at the end of phase 3. New behaviour sits behind flags
that default off: `SUPERVISOR_ENABLED`, `PROCTORING_ENABLED`,
`DEEPGRAM_RECORD_TRANSCRIPT`. So at any moment, on any branch, the original
interview can still be run end to end, and a regression shows up as "the old
flow broke", which is easy to see.

### Track C, testing its own work live

| Need | How |
| --- | --- |
| A real voice call | `scripts/dev_interview.py`, or the full flow through `/join` |
| See what the bot emits, without a dashboard | `JsonlSink`: `tail -f backend/sessions/<id>.events.jsonl`, plus a small `scripts/watch_events.py` that pretty-prints it |
| Behaviour, repeatably and cheaply | Text-mode runs against the realtime model (phase 0 in REALTIME_ARCHITECTURE.md): scripted candidates for stacking, fabrication, praise, withdrawal, contradiction |
| Notes, before S's Arbiter exists | `scripts/send_note.py <session> "text" --priority next_turn` writes a `NoteInstruction` straight into the inbox |

### Track S, testing its own work live

| Need | How |
| --- | --- |
| Build the dashboard with no calls at all | **Replay**: `scripts/replay_events.py <file.jsonl> --speed 1` feeds a recorded session through the real relay at real pace. Week 0 records 2 to 3 real sessions as fixtures |
| A live stream without paying for one | A **fake bot** (`scripts/fake_bot.py`) that emits plausible events on a clock and accepts notes, so the full note round trip works offline |
| A real interview on the dashboard | Run `dev`'s real bot from the S branch. S's branch is rebased on `dev` daily, so it always carries C's latest merged bot |
| Arbiter quality | A labelled set of notes (good, stale, biased, protected attribute, urgent) scored offline, the way the judge's precision is measured today |

### Together

- **A shared Railway environment `dev`**, deployed from the `dev` branch with
  its own Postgres, separate from production and from `stg`. Both tracks see
  the integrated build on a real URL from a phone or another network.
  *Someone with Railway access has to create it.*
- **CI on every PR into `dev`** (GitHub Actions, `uv run pytest -q`). There is
  none today. With two Claude accounts writing code, an automatic gate is the
  only reviewer that never gets tired.

## 6. Where the tracks affect each other

| Interaction | Risk | Handled by |
| --- | --- | --- |
| Event schema | S renders a field C renamed | Additive-only rule, `schema_version`, a contract test both suites run |
| Event volume | A slow relay stalls the call | Non-blocking emit with a bounded queue; asserted in a test |
| Notes into the conversation | A badly worded note derails Mitra | S triages, C wraps the final wording; joint pass bar in phase 2 |
| **Hidden supervisor in the Daily room** | `presence.py` would count the supervisor as "a second participant", and the silence ladder and dropped-call logic would read the wrong room | Week 0: supervisor tokens carry a fixed `user_id` prefix and Daily's hidden-presence permission (verify the current Daily docs first); C's presence ignores that prefix. Test on both sides |
| Database tables | Both add models | Separate modules on one `Base`; additive migrations as today |
| Consent copy | Says more or less than the product does | Changed only in the same PR as the feature it describes |
| Proctoring | C captures, S analyses | The recording format and the proctoring event types are part of contract 1 |
| CPU in the container | Dashboard relay and video next to live bots | Measured at each checkpoint; analysis runs after the interview, never live |

## 7. How it comes together

```
main (production) ◄── stg (staging, auto-deploys) ◄── dev (integration) ◄── c/*  s/*
```

1. Feature PRs merge into `dev` when CI is green and the other engineer has
   looked at anything in the shared list.
2. **Integration checkpoints** close each phase: a live session on the `dev`
   environment against the pass bar below, with both engineers listening.
3. After a checkpoint passes, `dev` is promoted to `stg`, then `stg` to
   `main`. **Only when no interview is live**: a deploy kills every bot.

| Phase | Track C | Track S | Checkpoint (pass bar) |
| --- | --- | --- | --- |
| **0. Foundation** (together, a few days) | Contracts 1 and 2, `EventSink`, inbox, router and model stubs, Node build stage, design tokens, CI, `changes/`, recorded fixtures, presence rule | | Both run the baseline; CI green; contracts merged |
| **1. See it live** | Deepgram as the independent record; emit transcript, turn and plan events; `deliver_note()`; captions ported (done) | Relay, event store, WebSocket; dashboard v1: transcript, plan timeline, logs; hidden join with live audio and video; supervisor link | A real interview watched live on the dashboard from another machine |
| **2. Steer it** | Director on the transcript: claims, coverage, depth, contradictions, the "you said" fabrication check; note lifecycle | Arbiter; note composer and status; "Mitra's reading" charts | A blind listener cannot tell which questions came from notes; a `next_turn` note lands within one exchange |
| **3. Front doors** | `CandidateProfile` and plan; device checks; candidate app; confusion-scenario fixes | Console: planned interviews, profile review, reports; cutover from `/admin` | The full flow on the new UIs with no API calls by hand; old UIs retired |
| **4. Proctoring** | Capture and prompts (initial, section 8) | Review and analysis (section 8) | Staged sessions below |
| **5. Production** | Session hand-over past 55 minutes; engine hardening | Bot workers separate from the web process; supervisor auth; Railway config migration | A redeploy during a test interview does not end it |

## 8. Proctoring

### Initial build (now)

Capture and evidence for a person, with nothing inferred yet.

| Piece | Track | Detail |
| --- | --- | --- |
| Camera required | C | On for the whole call; a technical prompt if it goes off ("your camera seems to be off") |
| Entire-screen share required | C | `getDisplayMedia`, checked with the track's `displaySurface === "monitor"`; a prompt if sharing stops; desktop only, said before consent |
| Tab focus | C | Already on `stg`: count and duration, shown to the reviewer |
| Recording | C | Daily raw-tracks (mic, camera, screen as separate files) to durable storage, never the container disk. Verify the Daily API before building |
| Proctoring events | C emits, S stores | `camera_off`, `share_stopped`, `wrong_surface`, `focus_lost`, `face_missing` (browser check, prompt only), each timestamped |
| Live tiles | S | Camera and screen on the dashboard via the hidden join |
| Review section | S | "Integrity signals for review" on the report: the events, each linked to its moment in the recording. No score |

### Gaze and screen analysis (planned, built after the initial layer)

Runs **after the interview** on the recorded tracks, scheduled the moment the
recording is ready, the same pattern as feedback scoring. Never live: there is
nothing to do live (no mid-interview integrity warnings, rule 4 in
REALTIME_ARCHITECTURE.md), and video work would take CPU from live calls.

**Gaze** (camera track)

1. Sample 2 to 5 frames a second.
2. MediaPipe Face Landmarker: face count, head pose from the transformation
   matrix, and iris position relative to the eye corners.
3. **Calibrate per candidate.** Webcams sit above, below and beside screens, so
   "looking away" means nothing without a baseline. Use the first minute, or a
   short look-at-the-dots step in the device check.
4. Report **patterns, not glances**: away for longer than N seconds, or repeated
   away-and-back during answers. N is set from real sessions, not guessed.
5. Output: `no_face`, `multiple_faces` and `sustained_look_away` events with
   timestamps and confidence.

**Screen** (screen track)

1. Sample a frame every 3 to 5 seconds, plus on large frame changes.
2. A vision model classifies each sample: the interview page only, another
   application, a chat or AI assistant window, notes or documents, a code editor.
3. Output: `other_app_visible` and `ai_assistant_visible` events with a
   thumbnail, kept for the shortest retention of anything stored.

**Second voice** (mic track): Deepgram batch diarisation, reporting a second
speaker with timestamps.

**Rules, unchanged from REALTIME_ARCHITECTURE.md section 7:** proctoring never
enters Mitra's context; it is evidence for a person and never changes a score or
the recommendation; thresholds come from staged sessions; glasses, lighting,
webcams and neurodivergent traits produce false signals, which is why no event
is ever an accusation. Legal review of the consent copy (India DPDP Act) before
any real candidate, and an accommodation route for anyone who cannot meet the
camera or screen requirement.

**Pass bar (staged sessions):** a phone glance held for 10 seconds, a second
person walking in, a chat window on the shared screen and someone whispering all
appear at the right time; a normal session with ordinary thinking glances
produces nothing.

## 9. Working with two Claude accounts

- **Shared knowledge lives in the repo.** CLAUDE.md for rules, this file for the
  plan, `docs/` for the rest. A decision made in one engineer's chat is not
  known to the other's Claude until it is written here.
- **Each engineer keeps a personal `CLAUDE.local.md`** at the repo root
  (gitignored), so their Claude knows its track without being told every
  session:

  ```markdown
  # Track S (supervisor and console). Read docs/DEV_PLAN.md first.
  - I own backend/supervisor/, frontend/console/, backend/tests/supervisor/.
  - Do not edit backend/bot/ or frontend/candidate-app/. Need a change there?
    Write it up for the other engineer instead.
  - Anything in backend/shared/ or the shared list in DEV_PLAN.md section 2 is
    its own PR, reviewed by the other engineer, merged before the feature.
  - Branch from dev as s/<topic>. Rebase on dev before starting work.
  - Develop the dashboard against scripts/replay_events.py, not live calls.
  - Add changes/<date>-<slug>.md instead of editing CHANGELOG.md.
  ```

  Track C's is the mirror image.
- **Start each session the same way:** `git fetch && git rebase origin/dev`, read
  this file, run the suite.
- **When one Claude needs something from the other track**, it writes the
  request as an issue or a note in the PR, not a cross-track edit.

## 10. Open items

1. Who takes Track C and who takes Track S. Then a `CODEOWNERS` file with both
   GitHub usernames, so shared files request the right review automatically.
2. Creating the Railway `dev` environment.
3. Whether to push `dev` to GitHub now (it is local until someone pushes it).
4. `Settings.load()` still requires ElevenLabs keys though realtime does not use
   them. Kept because the fallback needs them; revisit when the pipeline is
   retired.
5. Interviews over 55 minutes: cap or build the session hand-over (phase 5).
