## 2026-11-19: the technical round is real

**What**

The technical round no longer gives placeholder links. It runs MITRA's actual
interview, with the machinery already in the codebase:

1. **MITRA's plan.** When HR chooses "Proceed to technical", MITRA builds the
   candidate's interview plan from the job's spec, the JD and the CV. The
   candidate page shows the plan being prepared, ready (with its length), or
   failed (with "Prepare the plan again"). The name HR gave the candidate is
   kept; the generator would otherwise replace it with the name from the CV.
2. **Booking.** "Book the technical round" is enabled once the plan is
   ready. It creates the interview, with a private room, a meeting ID and a
   password.
   - HR sees the credentials to send, with copy buttons and a ready-made
     invitation.
   - The BU head sees the meeting ID but not the password.
   - The candidate joins on the join page, and MITRA joins with them.
   - A booking nobody has joined can be cancelled with a reason; one in
     progress cannot.
3. **After the interview,** a background pass records what happened:
   - a finished, scored interview moves the candidate to "Technical done".
     This is a system move, with the report's reading stored beside it;
   - a report that never arrives still moves them on after 30 minutes;
   - a failed start or an expired room moves them back to "Ready for
     technical", with the reason.
4. **The report** appears on the results page in the console's own design:
   - MITRA's reading and summary;
   - a score per competency, each with its reasoning and the candidate's
     quotes;
   - every quote opens its line in the transcript;
   - strengths and red flags observed, and anything not covered;
   - a notice when the connection was poor;
   - an Integrity section, which says it arrives with the interview track.

**Why**

Track I's code showed that everything the technical round needs already runs
here: plan generation, the room, the bot and scoring. Waiting for the merge
would have held up the whole journey for no reason. The console is the
product, so the report is shown in its own design from Track I's
`FeedbackReport` contract, not by embedding their `<InterviewReport>`.

**How**

- `backend/hiring/technical.py` is rewritten:
  - `prepare_plan` calls `app.main.generate_blueprint_task`;
  - `book_technical` returns Track I's `TwoLinks` field names;
  - `sweep_once` records how each booked round ended.
- `hiring/routes.py`: moving to `ready_for_technical` starts the plan in the
  background.
- `hiring/hr_evaluation.py`: the existing background loop also runs
  `sweep_once`.
- `hiring/results.py`: returns the full report and the transcript.
- `frontend/console/src/features/results/`:
  - `TechnicalEntry.tsx` is rewritten;
  - `TechnicalReport.tsx` replaces `TechnicalReportSlot.tsx`.
- `scripts/simulate_technical_done.py` stays, for walking through the journey
  without holding a real interview.

**Left for Track I**

- The live view link: the booking says it arrives with the interview track.
- Storing the `IntegrityReport`.
- Adding the HR round's findings to the plan.
- Choosing the length per booking (today it is the plan's).

**Verified**

- 12 tests in `tests/hiring/test_technical.py`, covering:
  - the plan is built on proceeding, and HR's name is kept;
  - booking is refused before the plan is ready, and retry works;
  - the sweep starts plans nobody started;
  - booking returns real credentials, and the password goes to HR only;
  - cancelling;
  - a round in progress cannot be cancelled;
  - a scored interview moves the candidate on;
  - a report that never arrives does not hold the candidate;
  - a failed or expired room goes back with the reason.
- Checked in screenshots: plan ready, booked with credentials, and the report
  with a quote opening its transcript line.

### Less on every page, a light theme, and one sample to demo with (same day)

- **The results page** opens on "Your decision", with MITRA's reading and the
  buttons, followed by four small tiles (CV fit, HR round, technical round,
  who sent it) and the technical report. The CV breakdown, MITRA's summary of
  the CV, the HR round and the history are under "More insight". The decision
  bar is no longer pinned to the bottom of the screen.
- **Lists:** table rows are shorter, so more fits on a screen.
- **Theme:**
  - Light is the default. A light/dark switch sits in the sidebar and on the
    welcome and sign-in pages, and the choice is remembered in this browser.
  - The theme is applied before the first paint (`index.html`), as
    `data-theme` on the page instead of following the operating system.
  - The dark theme is now a soft slate, not navy-black. The chart palettes
    were checked again against its surface.
  - The footer shows one logo in each theme.
- **One sample:**
  - `samples/` holds an invented job description and CV, with a walkthrough
    of every panel.
  - `scripts/reset_local.py --yes` clears a local database to the four demo
    accounts, backing up the file first, and refuses to run on a deployed
    instance.
  - `scripts/seed_org.py` now creates accounts only; `--examples` adds the
    two sample jobs.

### The HR round as a meeting room, the technical round's live room, and product wording (same day)

- **The HR round is a meeting room** in the style of Google Meet, built on
  Daily's call object (`features/hr-round/MeetingRoom.tsx`, `RoomPanel.tsx`).
  - **Lobby:** a camera preview, mic and camera toggles, device choice, "Join
    now", and the invite card (meeting ID, password, join page, "Copy
    invitation", "Cancel invitation").
  - **In call:**
    - the candidate's tile large and HR's self-view small;
    - a ring on whoever is speaking, a muted-mic icon and a weak-connection
      warning;
    - a "Recording" pill and a timer;
    - a shared screen takes the main tile;
    - the control bar has mic, camera, present, Questions, Notes, Leave and
      "End round" (which asks first);
    - Ctrl or Cmd plus D or E, as in Meet.
  - **Starting and cancelling:** "Start the room" books and opens the lobby in
    one step, and cancelling needs no reason ("Cancelled by HR").
- **The technical round has a live room**
  (`/candidates/:id/technical-room`):
  - the status, from waiting through live to scoring and report ready, and
    the elapsed time;
  - MITRA's plan with the section it should be on by the clock;
  - integrity events from the candidate's browser as they happen ("Facts for
    a person to review. Never part of a score.").

  Nobody joins the call from here, so the candidate is never shown a second
  person. Live video comes with Track I's hidden live view. The report's
  Integrity section lists the same browser facts.
- **Wording:**
  - the welcome page, sign-in and candidate page describe a proctored AI
    interview in product language;
  - the demo seed writes realistic competency descriptions, CV evidence,
    summaries and reasons instead of "Example …";
  - there is no internal or test wording ("stub", "placeholder", "interview
    track") on any screen.
- **Fixes:**
  - `plan_minutes` reads the plan's `evaluation_spec`, so a real plan's length
    shows;
  - cancelling a rebooked HR round no longer picks the wrong round.

### Fix: HR rounds stuck at "Collecting the recording"

- **Symptom:** an HR round sat at "Collecting the recording" and never moved
  on.
- **Cause:** Daily had the recording, but every download failed with 403.
  `bot/services/daily.py` `download_recording` lets aiohttp re-encode Daily's
  pre-signed download link, which changes the signed characters, so Daily
  refuses it.
  - Measured on one recording: the link as given returns 200; aiohttp's
    re-encoded copy returns 403.
  - The technical round's recordings go through the same function and fail
    the same way.
- **Fix in Track H:** the HR round now collects its own recording, within 30
  seconds of Daily finishing it rather than waiting for the five-minute sweep.
  `hiring/hr_evaluation.py` `collect_recording` requests the link exactly as
  given (`yarl.URL(link, encoded=True)`) and stores the file the way the sweep
  does. The test is `test_the_pipeline_fetches_a_finished_recording_itself`.
- **For Track I:** in `download_recording`, request
  `yarl.URL(url, encoded=True)` instead of `url`. It is a one-line change in
  their file, and it fixes the technical round's recordings too.

### A typed job description, two-column BU pages, model-call traces and costs, and org insights (same day)

- **New job request:** the job description can be uploaded, or typed and
  pasted (up to 30,000 characters). The page is two columns, with "What
  happens next" beside the form.
- **The job request page** is two columns: what MITRA understood, and beside
  it where the request stands with its actions and the conversation.
- **The BU home** puts the decisions to make beside the job requests. **The
  HR home** puts "Needs you" beside the jobs with work. The other charts are
  under "More insight". Every section's title and caption say what it shows.
- **Transparency gains "AI calls"**, a trace of every model call: stage,
  model, result, tokens, time, cost and a details drawer. Only metadata is
  kept, never prompts, CVs or transcripts.
- **It also gains "Cost"**: spend per stage, per candidate, per hire, per model
  and per business unit, and the weekly trend.
  - Prices are from a dated table in `hiring/usage.py`, overridable with
    `MODEL_PRICES_JSON`. Unknown models are marked, never guessed.
  - Migration `0008` adds the `model_calls` table.
  - The job chat's calls are now linked to their job.
- **The org home** leads with computed insights. Each is a sentence with its
  numbers and a link to act, and needs at least 5 cases. Then four health
  KPIs against the previous period (rates change in percentage points), and a
  compact business-unit table.
