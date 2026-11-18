# Track H: Hiring flow

> **Owner:** `sparshpatial5` · **Branch:** `track_h` · **Merges into:** `dev`, by PR
>
> This is Track H's working brief. The shared plan is
> [DEV_PLAN.md](DEV_PLAN.md): read it first. This file breaks Track H's part of
> it into tasks. If the two ever disagree, DEV_PLAN.md wins; fix this file in
> the same PR.

## What you own

Everything **around** the AI interview, from the model to the screen:

- **The organisation:** sign-in, roles, BU scoping.
- **Job requests:** the BU head's side.
- **CV screening and the HR round:** HR's side.
- **Decisions:** candidate status rules and the audit log.
- **The BU head's results**, including the side-by-side comparison.
- **The org dashboard, transparency and user management.**

Track I owns the AI interview itself: the candidate app, MITRA, proctoring,
the live view, the report, Check Intelligence and analytics.

## Where you work

| | Paths |
| --- | --- |
| **Yours** | `backend/org/`, `backend/hiring/`, `backend/blueprint/clarify.py`, `backend/blueprint/documents.py`, `frontend/console/src/shell/`, `frontend/console/src/features/{auth,jobs,screening,hr-round,results,org,transparency,admin-users}/`, `backend/tests/org/`, `backend/tests/hiring/` |
| **Track I's, do not edit** | `backend/bot/`, `backend/feedback/`, `backend/proctoring/`, `backend/live/`, `backend/analytics/`, `backend/blueprint/generate.py`, `backend/blueprint/refine.py`, `frontend/candidate-app/`, `frontend/ui/`, `frontend/console/src/features/{live,report,analytics}/` |
| **Shared: one PR at a time, the other engineer reviews** | `backend/shared/`, `backend/app/main.py`, `backend/app/db.py`, `backend/app/interviews.py`, `backend/app/meeting.py`, `backend/migrations/`, `pyproject.toml` + `uv.lock`, `Dockerfile`, consent copy, `CLAUDE.md`, `docs/DEV_PLAN.md` |

If you need a change in Track I's code, write it up as an issue or a PR note
for them rather than editing it. A new UI component goes in `frontend/ui/` in
a small PR that Track I reviews.

## The seams with Track I

| You give Track I | When | Until then they use |
| --- | --- | --- |
| `current_user()`, role guard, `bu_scope()` | Phase 0 | Nothing; it is a week 1 deliverable |
| `CandidateStage` and `move(candidate, to, by, reason)` | Phase 0 | Nothing; week 1 |
| `HRRoundEvaluation` (for MITRA's plan) | Phase 2 | One JSON fixture per sample CV |
| `JoinResponse.round` set at booking (`hr` or `technical`) | Phase 2 | Both values in fixtures |
| `Decision` rows (for business analytics) | Phase 2 | Seeded decisions |

| Track I gives you | When | Until then you use |
| --- | --- | --- |
| `book_technical(candidate, duration) -> TwoLinks` | Phase 2 | A stub returning fake links |
| `FeedbackReport` + `IntegrityReport` and the `<InterviewReport>` component | Phase 0 (on fixtures), real in phase 2 | Fixture reports rendered by the component |
| Design system components in `frontend/ui/` | Phase 0 onward | Ask for what you need |

Contracts change by **adding** fields only. Renaming or removing one needs both
engineers.

## Models you own

Every text model below defaults to `OPENAI_TEXT_MODEL`. That is `gpt-6.1-sol`
today, and becomes `gpt-6.2-sol` once your key can call it
(`scripts/check_models.py` from Track I will tell you).

| Job | Env slot |
| --- | --- |
| BU head's job chat and "what MITRA understood" | `OPENAI_CLARIFY_MODEL` |
| CV screening and the 10 HR questions | `OPENAI_SCREENING_MODEL` |
| HR round evaluation | `OPENAI_HR_EVAL_MODEL` |
| HR round transcription (file, after the call) | `OPENAI_TRANSCRIBE_MODEL` (`gpt-4o-transcribe`) |

Reasoning models reject `temperature`. `blueprint/clarify.py` passes it today;
use Track I's request helper from phase 0 when you move the job chat onto the
text model.

## Rules that matter most in your area

- **People decide; models recommend.**
  - HR rejects at screening and after the HR round.
  - After the technical round HR can only **send to the BU head**, who decides.
  - Every move goes through `move()` and records who, when, the model's
    recommendation and the reason.
  - Nothing moves a candidate by itself.
- **The screening percentage is computed in code.**
  - The model rates each competency 0–4 and must quote the CV for each rating.
  - Quotes are checked against the CV text in code; an unverifiable quote is
    dropped.
  - % = Σ weight × rating ÷ (4 × assessed weight).
  - "No evidence" is shown separately from a low score.
- **Identity is hidden from the scorer:** name, photo, email, phone, links,
  gender, age, address and university name are removed before screening.
- **The 50% cut-off is a setting,** not a constant. It only changes which
  buttons are offered; "Proceed anyway" needs a typed reason.
- **The chat never puts words in the BU head's mouth.** Anything the model
  assumed is marked "assumed" (the existing `inferred` mechanism in
  `clarify.py`; keep it).
- **BU scoping happens in server queries,** never only in the UI. A BU head who
  edits a URL still sees nothing from another BU. The route sweep test must
  cover every route and every role.
- **`org_id` goes on every new table.** Every schema change is an Alembic
  migration. Generate migrations only on a branch freshly updated from `dev`,
  one migration PR at a time.
- **The HR round has no bot.** It is recorded per participant and transcribed
  after the call, the raw transcript is never changed, and evaluation quotes
  are checked against it.
- **Candidates are in India:** DPDP Act consent and retention.
- **No emotion or tone inference anywhere.**

## Tasks by phase

Tick these in your PRs. Each phase ends with a joint live checkpoint on `dev`.

### Phase 0: Foundation (week 1)

- [x] Contracts in `backend/shared/`, written with Track I: `CandidateStage`,
      `Decision`, `HRQuestionSet`, `FitAnalysis`, `HRRoundEvaluation`, each with
      a fixture
- [x] Alembic set up; first migration for `Organisation`, `BusinessUnit`,
      `User` (role, BU, `org_id`), and job request fields on `Job` (BU,
      creator, status)
- [x] Sign-in:
  - [x] hashed passwords and httpOnly session cookies;
  - [x] `current_user()`, a role guard and `bu_scope()`;
  - [x] the route sweep test, which replaces the admin-only one.
- [x] Candidate stage machine: `CandidateStage`, the allowed moves, and `move()`
      writing a `Decision` and an audit entry
- [x] Register routers once in `app/main.py` (`org`, `hiring`, plus Track I's
      packages) with empty stubs
- [ ] Console app (`frontend/console/`, React + Vite + TS):
  - [ ] the shell, with layout, navigation and a home screen per role;
  - [ ] self-registering feature routes (`import.meta.glob` over
        `features/*/routes.tsx`).
- [x] `scripts/seed_org.py`: one admin, one HR, two BU heads in different BUs,
      sample jobs
- [ ] The welcome page with three doors, all going to one sign-in

**Checkpoint:** three seeded accounts sign in, and each lands on its own empty
home scoped to its role. CI is green.

> **Status 2026-11-18:** the backend half is done: contracts with fixtures,
> migrations, sign-in and scoping (`job_scope` rather than `bu_scope`), the
> stage machine, router registration and the seed script. The console shell
> and welcome page are next, and need Node.js. Track I's contract half
> (`InterviewEvent`, the report types, `TwoLinks`) is theirs to add.

### Phase 1: Job to screening (weeks 2–3)

**BU head: job request**
- [ ] New job request form: title (required), JD file (optional)
- [ ] Chat UI over `clarify.py`, which works with or without a JD
- [ ] "What MITRA understood": competencies and weights, depth, dealbreakers,
      duration, with assumptions marked; Edit goes back to the chat, Approve
      publishes it
- [ ] Job request list with status; published jobs appear to HR under the BU

**HR: lists and upload**
- [ ] HR home: BU → job → candidates, with status chips and filters ("needs
      me", "below threshold", "this week")
- [ ] CV upload with Re-upload or Proceed

**Screening**
- [ ] `analyse_fit(job, cv) -> FitAnalysis`:
  - [ ] identity removed first;
  - [ ] ratings 0–4 with CV quotes, the quotes verified in code;
  - [ ] the % computed in code, plus the assessed share of weight and the
        reasoning;
  - [ ] run as a background task the moment the CV is uploaded.
- [ ] `hr_questions(job, cv, fit) -> HRQuestionSet`: 10 questions, each with a
      "why we're asking"
- [ ] Fit analysis screen: overall %, a per-competency chart, reasoning tied to
      CV lines, "no evidence" items, the 10 questions
- [ ] Gates:
  - [ ] below 50%: Reject (red), or Proceed anyway with a reason;
  - [ ] at or above 50%: Not now, or Begin HR round.

**Checkpoint:** a BU head raises a job; HR uploads a CV and sees an evidenced
fit score; HR rejects or proceeds, and the decision is in the audit log.

### Phase 2: HR round (weeks 3–5)

- [ ] `round` field on the interview (`hr` / `technical`); the join path never
      spawns a bot for `hr` (a shared PR in `app/interviews.py`)
- [ ] HR round booking: a room, candidate credentials, and HR's own join from
      the console
- [ ] Recording per participant (Daily raw-tracks), required for the HR round
      regardless of `RECORDING_ENABLED`. Check the current Daily API first
- [ ] After the call:
  - [ ] each voice transcribed with OpenAI file transcription;
  - [ ] the voices merged into one transcript ordered by spoken time;
  - [ ] the raw transcript stored unchanged.
- [ ] `evaluate_hr_round(transcript, questions, job) -> HRRoundEvaluation`:
      soft-skill rubric, verified quotes, recommendation, findings for MITRA
- [ ] HR round screen: the credentials to send, the 10 questions as a
      checklist, and a notes box
- [ ] Evaluation review, then Reject or Proceed to technical
- [ ] "Book technical" button, calling Track I's `book_technical()` (stub
      until it lands)
- [ ] Send to BU head
- [ ] In-app badges: "N waiting for you"

**Checkpoint:** a real HR round is held in MITRA, transcribed and evaluated,
and the candidate moves on or is rejected.

### Phase 3: Joined up (weeks 5–7)

- [ ] BU head results page: rubric chart, summary, strengths and gaps, JD
      match, with Track I's `<InterviewReport>` embedded
- [ ] Side-by-side comparison of all candidates for one job
- [ ] BU head decision: Shortlist, On hold or Reject, with a reason
- [ ] HR findings handed to Track I for MITRA's plan (`HRRoundEvaluation` on
      the candidate)

**Checkpoint (joint):** one candidate goes from CV to the BU head's decision on
the new apps, proctored, with no API calls made by hand. The old `/admin`
panel is retired.

### Phase 4: Org view (weeks 7–8)

- [ ] Org dashboard: every BU's jobs and candidates, read-only
- [ ] Transparency: searchable transcripts, recordings and reports; the audit
      log with each decision next to the model's recommendation
- [ ] AI usage register: the model and prompt version behind every score you
      produce
- [ ] Consent records, retention periods, deletion requests
- [ ] User and BU management: invite, assign a BU, deactivate

**Checkpoint:** an org admin can answer "what happened, what did it cost, who
decided" for any candidate.

### Phase 5: Production and polish (weeks 9–10)

- [ ] Full UI polish of the console screens
- [ ] Accessibility: keyboard, screen reader, status shown by label and not
      only by colour
- [ ] Testing with realistic organisation data

## Testing on your own

| Need | How |
| --- | --- |
| Roles and scoping | `scripts/seed_org.py`, plus the route sweep test |
| Screening quality | `scripts/screen_cv.py <jd> <cv>` prints the `FitAnalysis` and every failed quote. Keep a small labelled set of JD/CV pairs (start with `tests/fixtures/documents/`) |
| Screening fairness | The same CV with names and genders swapped must give the same score |
| A real HR round | Two browsers: HR signed in to the console, the candidate in a private window on the old `/join` page until Track I's app has its HR mode |
| HR evaluation without holding a round | A two-speaker transcript fixture through `evaluate_hr_round()` |
| Results pages before the real report | The fixture reports via `<InterviewReport>` |
| Booking before Track I's booking exists | The `book_technical` stub |

Two suites, never mixed: deterministic tests (fake model, fast, in CI) and
behaviour tests (real model, run deliberately).

## Daily routine

```bash
git switch track_h
git pull origin dev            # bring in Track I's merged work
cd backend && uv run pytest -q
# ... work ...
git push origin track_h
# at least twice a week: open a PR track_h → dev
```

Each PR adds a `changes/<date>-<slug>.md` instead of editing `CHANGELOG.md`.
Never push to `dev`, `stg` or `main` directly.

## Your first week, in order

1. Contracts with Track I (one PR, both review): `CandidateStage`,
   `Decision`, and your model output types with fixtures.
2. Alembic, plus the org, BU and user tables.
3. Sign-in, the guard and BU scoping, plus the route sweep test.
4. `move()` and the audit log.
5. Router stubs in `main.py` (shared PR).
6. Console shell and role homes; `seed_org.py`.
