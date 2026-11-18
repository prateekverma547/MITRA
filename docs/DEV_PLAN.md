# Development Plan: two tracks, one product

> **Status: agreed direction, 2026-11-18.** Branch `dev`. This file is the
> shared brief for both engineers and both Claude accounts. Claude memory is per
> account and is not shared, so **anything both sides must know lives here or in
> CLAUDE.md, never only in one person's memory.** Update this file in the same
> PR as any change that alters what it says.

## 1. Where we are going

A production-grade interview product on the realtime voice engine, watched and
steered live by the employer who planned it, with proctoring.

**Two products, two people.**

| Product | Who | What it does |
| --- | --- | --- |
| **MITRA console** | The employer | Everything on the employer side, from planning an interview to watching it live to reading the report |
| **Candidate app** | The candidate | Join, consent, device checks (mic, speaker, echo, camera, screen, network), a calm in-call screen with captions |

**The employer's journey, all inside the console:**

```
Console home: planned interviews ─┬─ open an existing one
                                  └─ New
                                       │
   1. Upload the CV ───────────► 2. Check what MITRA understood:
                                    the candidate profile, and the
                                    interview plan built from it.
                                    Edit, then approve.
                                       │
                                  3. Book ──► MITRA gives back TWO links
                                               │
               ┌───────────────────────────────┴──────────────────────────┐
     Candidate link                                          Live view link
     /join + meeting ID + password                           /console/#/interviews/<id>/live
     sent to the candidate                                   for the same employer, behind the
                                                             console login
                                                             │
                                                     during the interview:
                                                     live video, audio and screen;
                                                     live transcript; the plan;
                                                     Mitra's reading of each answer;
                                                     live logs; the note box
                                                             │
                                                     after it: the report, in the same place
```

The live view is **not a separate product or a separate login**. It is one
screen of the console, for one interview, reached by the link MITRA hands back
at booking or from the planned-interviews list. Whoever can sign in to the
console can open it; nobody else can.

**Decisions recorded on 2026-11-18** (these override the matching lines in
CLAUDE.md, which says so at the top):

1. **The realtime engine is the main engine.** `INTERVIEW_ENGINE=pipeline` is the
   fallback and the rollback. Done on `dev`.
2. **Integration branch `dev`** = `stg` + `realtime-poc`. Done.
3. **React + Vite + TypeScript** for the new console and candidate app.
4. **The consent notice says a person from the hiring team may watch live.**
   It lands with the live view, not before, so the notice is always true.
5. **The employer sends notes only** while watching. No speaking, no taking over.
6. **Proctoring:** build the initial capture-and-review layer now; gaze and
   screen analysis are planned below and built after it.

**Names used everywhere below:**

| Name | What it is |
| --- | --- |
| **Mitra** | The realtime model talking to the candidate. The fast brain |
| **Director** | Our brain + judge reading the transcript and steering Mitra with notes. The slow brain |
| **Employer** | The console user: plans the interview, watches it live, reads the report |
| **Live view** | The console screen for one interview in progress |
| **Arbiter** | The secondary model that triages the employer's live notes before they reach Mitra |

## 2. The split

Two tracks, divided by **which side of the call the code serves**, which is also
how the directories divide. Each track is development plus its own research.

### Track C: Candidate and interview intelligence

Everything the candidate touches, and everything that decides what Mitra says
or what MITRA understands.

- `backend/bot/`: the realtime engine, Director, note delivery into the live
  session, event emission, Deepgram as the independent transcript.
- `backend/blueprint/`: **the intelligence** behind CV understanding
  (`CandidateProfile`) and plan generation. Functions, not screens.
- The new candidate app (`frontend/candidate-app/`).
- Proctoring **capture**: camera and screen requirements, technical prompts in
  the candidate's browser, recording.
- Research: how well the CV is understood, the confusion scenarios (noise, echo,
  backchannel, injection, mishearing), behaviour checks against the realtime
  prompt, tone policy.

### Track E: Employer console

Everything the employer sees or does, before, during and after the interview.

- `frontend/console/` (new): planned interviews, New (CV upload, profile and
  plan review and approval, booking), the live view, reports.
- `backend/console/` (new): the console's API routes, including the screens
  over Track C's profile and plan functions, and booking with its two links.
- `backend/live/` (new): the event relay and store, the Arbiter, note intake,
  and the live view's hidden join to the Daily room.
- Proctoring **review**: live tiles in the live view, the event timeline, the
  after-interview analysis job (gaze, screen, second voice) and its report
  section.
- Research: what the live view should show and how, Arbiter quality (does a note
  land without sounding like an interruption), proctoring thresholds.

**The one place the tracks share a feature on purpose:** CV understanding.
Track C builds what MITRA understands from a CV; Track E builds the screen where
the employer checks and corrects it. They meet at the `CandidateProfile`
contract, so neither waits: E builds the review screen against fixture profiles,
C improves the profile against real CVs.

### Shared, change-controlled

Touched by one PR at a time, reviewed by both engineers:

`backend/shared/` (contracts and events), `backend/app/main.py`,
`backend/app/db.py`, `backend/app/interviews.py` (booking is E's, joining is C's),
`backend/pyproject.toml` + `uv.lock`, `backend/Dockerfile`, `frontend/ui/`
(design system), `CLAUDE.md`, this file, `docs/TECH_SPEC.md`.

## 3. The seam between the tracks

The tracks meet at three contracts, all written in week 0 before the split.

```
            Track C                                        Track E
  ┌──────────────────────────────────────┐        ┌──────────────────────────────────────┐
  │ blueprint/: CV -> CandidateProfile ──┼───────►│ console: review, edit, approve, book │
  │             -> plan                  │profile │                                      │
  │                                      │        │                                      │
  │ bot/: Mitra · Deepgram · Director    │ events │ live/: relay · store · WebSocket      │
  │   sink.emit(InterviewEvent) ─────────┼───────►│        ──► the live view             │
  │                                      │        │                                      │
  │   inbox.next() ──► coordinator ◄─────┼────────┤ NoteInstruction ◄── Arbiter ◄── note │
  └──────────────────────────────────────┘ notes  └──────────────────────────────────────┘
```

**Contract 1: `CandidateProfile`** (`backend/shared/contracts.py`). What MITRA
understood from a CV: work history with dates and gaps, skills each anchored to
the CV line that supports them, quantified claims, projects, a seniority
estimate, fit against each competency (evidenced, claimed, absent), thin spots,
and stated expectations. Every item is checked against the CV text in code, the
way report quotes already are. The employer's edits are stored beside the
model's version, never over it, so the record shows what was changed.

**Contract 2: `InterviewEvent`** (`backend/shared/events.py`). One versioned,
typed union of everything the bot can report: transcript partial and final (per
source: `deepgram`, `openai`), turn started and ended, reply started and ended,
Director analysis (claims, coverage, depth stage, contradictions), plan state
(section, time left, next move), note lifecycle, channel health, proctoring
signals, and log lines. Track C emits; Track E stores and renders.

- `bot/events.py` holds an `EventSink` protocol with `NullSink` (default) and
  `JsonlSink` (writes `sessions/<id>.events.jsonl`). Track E adds the relay
  sink in `backend/live/`. Neither waits for the other.
- **Emission never blocks the call.** `emit()` is non-blocking with a bounded
  queue that drops on overflow and counts the drops. A test asserts this. A
  live view that falls over must not cost a candidate a single word.
- **Additive changes only.** A new event type or a new optional field is fine
  in a normal PR. Renaming or removing anything bumps `schema_version` and is
  agreed by both.

**Contract 3: `EmployerNote` → `NoteInstruction`** (`backend/shared/notes.py`).
`EmployerNote` is what the employer typed in the live view. The Arbiter turns it
into a `NoteInstruction`: priority (`stop` / `next_turn` / `boundary` /
`context` / `rejected` / `stale`), the instruction in Mitra's voice, and the
reason. Track E produces it; Track C's coordinator delivers it into the live
session, removes it once acted on, and emits the lifecycle events.

- **Track C owns the last step into the model**, so the guardrails that protect
  the conversation (never "the hiring team says", probe a contradiction at most
  once, one question at a time) live in one place, next to the Director's own
  notes.
- **Track E owns triage**, including refusing notes that are biased, ask about
  protected attributes, or tell Mitra to evaluate out loud.

## 4. How merge conflicts are avoided

Conflicts come from two people editing the same lines. The plan is that they
almost never can, and that when they can, it happens in a small deliberate PR
rather than inside a feature.

| Rule | Why it prevents conflicts |
| --- | --- |
| **New code goes in new directories.** E builds in `backend/console/`, `backend/live/`, `frontend/console/`; C in `backend/bot/`, `backend/blueprint/`, `frontend/candidate-app/` | Each side's files are disjoint from the other's |
| **Routes register once.** Week 0 adds `app.include_router(...)` lines for `console`, `live` and a candidate router to `main.py`, each with an empty stub. After that, nobody edits `main.py` to add a route | `main.py` is 900 lines and the most likely collision point |
| **Tables live with their track.** New SQLAlchemy models go in the track's own `models.py` on the shared `Base`. Week 0 makes `db.py` import a list of model modules so `create_all` sees them | Two people adding columns to `db.py` is a guaranteed conflict |
| **Contracts change first, alone.** A change to `shared/` is its own PR, merged before the feature that needs it; both sides rebase | The shared seam never changes inside a large diff |
| **No shared append-only files.** Each PR adds `changes/<date>-<slug>.md` instead of editing `CHANGELOG.md`; whoever promotes `dev` folds them in | Removes the one file both tracks would touch every day |
| **Dependencies in their own PR.** On rebase, a `uv.lock` conflict is resolved by running `uv lock`, never by hand | Lockfile conflicts are mechanical; keep them out of feature work |
| **Separate frontend apps, separate lockfiles.** `frontend/console/` and `frontend/candidate-app/` each own their `package.json` and lockfile. `frontend/ui/` is a folder both import by path, not a package | One npm workspace lock would conflict on every install |
| **Tests live with their track.** `backend/tests/console/`, `backend/tests/live/`, `backend/tests/candidate/` for new tests | New test files never collide |
| **Short branches, daily rebase.** Feature branches `c/<topic>` and `e/<topic>`, one to three days, rebased on `dev` every morning | Drift is what turns a small conflict into a large one |

The files that can still collide, and who owns them:

| File | Owner | How the other side changes it |
| --- | --- | --- |
| `app/interviews.py` | shared | Booking and its two links are E's; joining and spawning the bot are C's. One PR at a time |
| `bot/presence.py` | C | E needs it to ignore the live view's hidden join: agreed in week 0 (section 6) |
| `bot/realtime_coordinator.py` | C | E never edits it; notes arrive through the inbox |
| `frontend/candidate/index.html` (old page) | C | Frozen except for fixes; the new candidate app replaces it |
| `frontend/admin/` (old panel) | E | Frozen except for fixes; the new console replaces it |
| Consent copy | C | E writes the "may watch live" sentence in the live view PR, C reviews |
| `backend/Dockerfile` | shared | Week 0 adds the Node build stage once, for both apps |

## 5. Testing live, before and during the split

### The baseline, before anyone branches

Both engineers run `dev` exactly as it is and hold one real interview each. This
is "the original code" both tracks compare against, and it proves each
machine's setup.

```bash
git switch dev && cd backend && uv sync && uv run pytest -q   # 782 pass
uv run python scripts/realtime_smoke.py                        # OpenAI accepts the settings
uv run uvicorn app.main:app --port 8000 --reload               # /admin, then the candidate link
```

Each engineer uses **their own API keys** in their own `.env`, so spending is
attributable and one person's rate limit never stalls the other.

### The old flow stays runnable throughout

The new apps are served at **new paths** (`/console` for the employer,
`/interview` for the candidate) beside the existing `/admin` and `/join`.
Nothing old is switched off until the cutover at the end of phase 3. New
behaviour sits behind flags that default off: `LIVE_VIEW_ENABLED`,
`PROCTORING_ENABLED`, `DEEPGRAM_RECORD_TRANSCRIPT`. So at any moment, on any
branch, the original interview can still be run end to end, and a regression
shows up as "the old flow broke", which is easy to see.

### Track C, testing its own work live

| Need | How |
| --- | --- |
| A real voice call | The full flow through `/admin` and the candidate link, or `scripts/dev_interview.py` for voice only |
| See what the bot emits, with no live view | `JsonlSink`: `tail -f backend/sessions/<id>.events.jsonl`, plus `scripts/watch_events.py` to pretty-print it |
| Behaviour, repeatably and cheaply | Text-mode runs against the realtime model (phase 0 in REALTIME_ARCHITECTURE.md): scripted candidates for stacking, fabrication, praise, withdrawal, contradiction |
| Notes, before E's Arbiter exists | `scripts/send_note.py <session> "text" --priority next_turn` writes a `NoteInstruction` straight into the inbox |
| CV understanding | `scripts/profile_cv.py <cv-file>` prints the `CandidateProfile` and every item whose anchor failed |

### Track E, testing its own work live

| Need | How |
| --- | --- |
| Build the live view with no calls at all | **Replay**: `scripts/replay_events.py <file.jsonl> --speed 1` feeds a recorded session through the real relay at real pace. Week 0 records 2 to 3 real sessions as fixtures |
| A live stream without paying for one | A **fake bot** (`scripts/fake_bot.py`) that emits plausible events on a clock and accepts notes, so the full note round trip works offline |
| The planning screens without waiting on C | Fixture `CandidateProfile`s for the sample CVs in `tests/fixtures/documents/` |
| A real interview in the live view | Run `dev`'s real bot from the E branch. E's branch is rebased on `dev` daily, so it always carries C's latest merged bot |
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
| `CandidateProfile` shape | E's review screen shows a field C renamed | Additive-only rule, `schema_version`, a contract test both suites run |
| Event schema | Same, for the live view | Same |
| Event volume | A slow relay stalls the call | Non-blocking emit with a bounded queue; asserted in a test |
| Notes into the conversation | A badly worded note derails Mitra | E triages, C wraps the final wording; joint pass bar in phase 2 |
| **The live view's hidden join** | `presence.py` would count the employer as "a second participant", and the silence ladder and dropped-call logic would read the wrong room | Week 0: live view tokens carry a fixed `user_id` prefix and Daily's hidden-presence permission (verify the current Daily docs first); C's presence ignores that prefix. Tests on both sides |
| Database tables | Both add models | Separate modules on one `Base`; additive migrations as today |
| Consent copy | Says more or less than the product does | Changed only in the same PR as the feature it describes |
| Proctoring | C captures, E analyses and shows | Recording format and proctoring event types are part of contract 2 |
| CPU in the container | Relay and video next to live bots | Measured at each checkpoint; analysis runs after the interview, never live |

## 7. How it comes together

```
main (production) ◄── stg (staging, auto-deploys) ◄── dev (integration) ◄── c/*  e/*
```

1. Feature PRs merge into `dev` when CI is green and the other engineer has
   looked at anything in the shared list.
2. **Integration checkpoints** close each phase: a live session on the `dev`
   environment against the pass bar below, with both engineers on it.
3. After a checkpoint passes, `dev` is promoted to `stg`, then `stg` to `main`.
   **Only when no interview is live**: a deploy kills every bot.

| Phase | Track C | Track E | Checkpoint (pass bar) |
| --- | --- | --- | --- |
| **0. Foundation** (together, a few days) | The three contracts, `EventSink`, inbox, router and model stubs, Node build stage, design tokens, CI, recorded fixtures, the hidden-join presence rule | | Both run the baseline; CI green; contracts merged |
| **1. See it live** | Deepgram as the independent record; emit transcript, turn and plan events; `deliver_note()`; captions ported (done) | Relay, event store, WebSocket; live view v1 (transcript, plan timeline, logs, live audio and video); booking returns the live view link | Book an interview, open both links, watch it live from another machine |
| **2. Steer it** | Director on the transcript: claims, coverage, depth, contradictions, the "you said" fabrication check; note lifecycle | Arbiter; note box and note status; "Mitra's reading" charts | A blind listener cannot tell which questions came from notes; a `next_turn` note lands within one exchange |
| **3. Front doors** | `CandidateProfile` and the plan built from it; device checks; candidate app; confusion-scenario fixes | Console: planned interviews, New (upload, review and approve profile and plan, book); reports; cutover from `/admin` | The whole journey in section 1 on the new apps, with no API calls by hand; old UIs retired |
| **4. Proctoring** | Capture and prompts (initial, section 8) | Review and analysis (section 8) | Staged sessions below |
| **5. Production** | Session hand-over past 55 minutes; engine hardening | Bot workers separate from the web process; console auth beyond one shared password; Railway config migration | A redeploy during a test interview does not end it |

## 8. Proctoring

### Initial build (now)

Capture and evidence for a person, with nothing inferred yet.

| Piece | Track | Detail |
| --- | --- | --- |
| Camera required | C | On for the whole call; a technical prompt if it goes off ("your camera seems to be off") |
| Entire-screen share required | C | `getDisplayMedia`, checked with the track's `displaySurface === "monitor"`; a prompt if sharing stops; desktop only, said before consent |
| Tab focus | C | Already on `stg`: count and duration, shown on the report |
| Recording | C | Daily raw-tracks (mic, camera, screen as separate files) to durable storage, never the container disk. Verify the Daily API before building |
| Proctoring events | C emits, E stores | `camera_off`, `share_stopped`, `wrong_surface`, `focus_lost`, `face_missing` (browser check, prompt only), each timestamped |
| Live tiles | E | Camera and screen in the live view, via the hidden join |
| Review section | E | "Integrity signals for review" on the report: the events, each linked to its moment in the recording. No score |

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
   away-and-back during answers. N is set from staged sessions, not guessed.
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
  # Track E (employer console). Read docs/DEV_PLAN.md first.
  - I own backend/console/, backend/live/, frontend/console/,
    backend/tests/console/ and backend/tests/live/.
  - Do not edit backend/bot/, backend/blueprint/ or frontend/candidate-app/.
    Need a change there? Write it up for the other engineer instead.
  - Anything in backend/shared/ or the shared list in DEV_PLAN.md section 2 is
    its own PR, reviewed by the other engineer, merged before the feature.
  - Branch from dev as e/<topic>. Rebase on dev before starting work.
  - Build the live view against scripts/replay_events.py, not live calls.
  - Add changes/<date>-<slug>.md instead of editing CHANGELOG.md.
  ```

  Track C's is the mirror image.
- **Start each session the same way:** `git fetch && git rebase origin/dev`, read
  this file, run the suite.
- **When one Claude needs something from the other track**, it writes the
  request as an issue or a note in the PR, not a cross-track edit.

## 10. Open items

1. Who takes Track C and who takes Track E. Then a `CODEOWNERS` file with both
   GitHub usernames, so shared files request the right review automatically.
2. Creating the Railway `dev` environment.
3. Pushing `dev` to GitHub after the live test confirms it.
4. `Settings.load()` still requires ElevenLabs keys though realtime does not use
   them. Kept because the fallback needs them; revisit when the pipeline is
   retired.
5. Interviews over 55 minutes: cap or build the session hand-over (phase 5).
6. Whether the live view link should also work from an email without a fresh
   sign-in (a signed, expiring link). Today's plan is: console login only.
