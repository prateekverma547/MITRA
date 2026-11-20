## 2026-11-19: Track I's backend joined to Track H's

**What**

Track I's phases 3 to 5 (`origin/track_i` at `9eb0d5b`), backend only, brought
into `track_h`. Screens and flows are unchanged; what changed is underneath.

Taken as Track I wrote them (215 files): `live/`, `proctoring/`, `aftercall/`,
`analytics/summary/`, `feedback/`, `blueprint/refine.py` and `generate.py`, the
bot's changes, `app/startup.py`, `app/logs.py`, `app/recordings.py`,
`app/capacity.py`, `shared/contracts.py`, `shared/interview_contracts.py`,
`shared/text_models.py`, their scripts, fixtures and tests.

Merged by hand:

- `app/main.py`: our schema migrations, router loop, `/console` and `/meet`;
  their start-up tasks (restart reconcile, after-call runner, event writer, drop
  watcher), logging and the `/interview` routes. Their explicit router includes
  were dropped: the loop already registers `live`, `proctoring` and `analytics`
  (`analytics/routes.py` now re-exports the summary router).
- `app/interviews.py`: their check, join, rejoin and booking. Our HR round
  handling is kept in join and check: no bot, no capacity claim, no reload
  refusal, no rejoin pass, no proctoring token for an HR round.
- `app/db.py`: both sides' columns; their tables registered in `MODEL_MODULES`.
  Start-up stamps a database that `create_all` built whole at head (their tests
  build databases that way), and fills its missing nullable columns.
- `bot/services/daily.py`: their file, plus our `start_cloud_recording` for the
  HR round's token.
- Migration `0009`: their ten tables and eleven columns. Each is created only
  when missing, for databases that already ran their code. Checked: a fresh
  database matches the models exactly; downgrade and upgrade both run.

**Fixes so the HR side keeps working**

- A server restart no longer fails an HR round in progress (`app/startup.py`
  skips `round = "hr"`).
- Their `no_show` status: an HR round reads as `scheduled` (HR can still
  cancel and invite again); a MITRA round moves the candidate back to be
  booked, as an expired room did; the room lookup says closed.
- Models pinned: the job chat, CV screening, HR questions and HR evaluation
  stay on what they used (`legacy_blueprint_model`, `gpt-4.1` unless
  `OPENAI_BLUEPRINT_MODEL` says otherwise). Their new default text model would
  have broken the job chat (it sends `temperature`).
- Interviews are 20 to 60 minutes in the job chat, as MITRA's plan and booking
  now require.
- HR recordings are collected by their collector (`collect_one`: a claim, so
  one download, and a check that the file plays). Our workaround is gone.
- The org admin's recording player goes through `serve_recording`, so every
  view is logged in `recording_views`.
- Erasing a candidate also deletes MITRA's saved copy of their audio and the
  after-call files (`capture/<id>`, `aftercall/<id>`).
- Room status (`hiring/presence.py`) skips hidden live-view watchers.

**How HR results now feed MITRA**

- The HR evaluation lists only gaps the MITRA round should probe, as neutral
  topics (prompt `hr-eval-2026-11-19-probe-only`). Their preparation step can
  only add a probe or leave a finding out, so "need not repeat" is no longer
  asked for.
- When HR proceeds, the plan is built as before, then, still while it reads as
  being prepared, their `refine_plan_for_booking` adds those findings as probe
  questions, sizes the plan to the interview length and writes question goals.
  Once per plan. The notes go in the audit log (`technical_plan_prepared`).
  Booking stays instant and stores what MITRA runs to (booked, planned and
  grace minutes).
- `live/hr_hook.hr_findings_for` reads `hiring.hr_evaluation.findings_for_technical`
  (their guide, section 7). `HR_FINDINGS_FIXTURE` still overrides it.

**Candidate room**

The End button sends Mitra the end message before leaving, so the interview is
recorded as ended by the candidate instead of a drop Mitra waits 90 seconds for.

`CANDIDATE_AUDIO_CAPTURE=0` in `.env.example`: our consent screen does not
mention the candidate's own audio yet, and the after-call check that uses it is
off. Turn both on together.

**Not brought in yet**

`frontend/ui`, `frontend/candidate-app`, their console files, `Dockerfile`,
`.dockerignore` and `.github/workflows/ci.yml`. Seven backend tests check those
files or need the after-call voice model downloaded, and fail until they arrive.

**For Track I**

1. Two edits in your files, for review: `live/hr_hook.py` (the findings reader,
   now async) and `tests/live/test_seed_interview.py` (the seed script writes
   `INTERVIEW_ENGINE` into `os.environ`; it leaked into later tests).
   `tests/live/test_watcher_counting.py` allows `hiring/presence.py`, which
   skips `watcher:` user ids.
2. Leave HR rounds out of `mark_no_shows`, `stored.write_pending`, the analytics
   summaries and the recording sweep's counts (`round != "hr"`).
3. Fill `org_id` in `live/store.py` and `proctoring/intake.py`.
4. Switches for the auditor and for `RealtimeCaptions`.
5. Issue a proctoring token on the rejoin exchange; a `reason` on Daily token
   failures.
6. The guide's "not committed yet" note on the download fix is out of date.

**Update: Track I's round 18 (`742f8df`)**

The interviewer moved to Track I's latest: `bot/auditor.py`, `bot/live_events.py`,
`bot/realtime_coordinator.py`, `bot/realtime_prompt.py`,
`shared/interview_contracts.py`, `analytics/summary/build.py` and `shapes.py`
as they wrote them, with their tests. `live/hr_hook.py` merged: our findings
reader, their retry without the findings when refinement with them fails.
"Let's begin" is said once, after a short reaction to the candidate's work; the
opening's time note waits a minute and two answers; turns are labelled against
each question's goal; a late answer is filed under the area it answers. Every
interviewer file now equals `origin/track_i`, except `bot/services/daily.py`
(the HR round's recording token) and `live/hr_hook.py` (the findings reader).

**Update: Track I's build, CI and checks, without their frontends**

- `backend/Dockerfile`: Track I's, with its frontend stage building our console
  instead of `frontend/ui` and the candidate app. Their after-call model layer and
  `--timeout-graceful-shutdown 5` are in.
- `.dockerignore`: Track I's, unchanged.
- `.github/workflows/ci.yml`: Track I's backend job unchanged (model fetch and
  check, `pytest -q`, the smoke interview); the frontend job runs only the four
  console steps their file already had (`npm ci`, `npm run typecheck`, `npm test`,
  `npx vite build`).
- The console has `npm test` (vitest 5.1.1, Track I's version) with two test
  files: the ending wording, ported from Track I's `endings.test.tsx`, and small
  console logic.
- `frontend/console/src/features/results/endingWords.ts` and `src/branding.ts`:
  Track I's files, unchanged but for one import path. `EndReason` and
  `ConnectionRecord` mirrored in `contracts.ts`. Not shown on any screen yet.
- `frontend/candidate/index.html` (the old `/join` page): Track I's fix, only the
  interviewer leaving ends the call.
- The after-call speaker model is downloaded locally (`aftercall.models fetch`);
  it is git-ignored.

Result: backend 2,166 passed, 0 failed; the smoke interview 20 of 20; console 34
tests, type check and build pass. Of the 323 files on `track_i` outside its
frontend apps and notes, 303 are byte-identical here and 20 differ, each by
Track H's own work (the hand-merged shared files, our job chat and HR round
code, `alembic`, and the test edits listed above).

**Deploying:** the code turns `CANDIDATE_AUDIO_CAPTURE` on when it is not set.
Set `CANDIDATE_AUDIO_CAPTURE=0` on Railway until the consent screen mentions it.

**Confirm**

- `cd backend && uv run pytest -q`: 2,166 passed, none failing (see the update above);
- Start the server: it migrates the database to `0009`. Run the flow: BU head
  request, screening, HR round (the evaluation now lists probe topics), Proceed
  (the plan shows as preparing a little longer), book the MITRA round, join from
  `/meet`, press End: the report should say the candidate ended it.
