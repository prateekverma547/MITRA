## 2026-11-19: results and decisions, the org admin's section, and a home for each role

**What**

*Phase 3: from the technical round to the BU head's decision.*
- HR books the technical round from the candidate page, with a duration.
  Until Track I's booking lands, the two links are placeholders and the page
  says so.
- HR sends a finished candidate to the BU head, with an optional note.
- The BU head's results page shows:
  - the CV fit against what the job asks for, competency by competency;
  - strengths and gaps, each labelled by where it came from (CV or HR round);
  - the HR round's evaluation with its quotes;
  - a slot for MITRA's technical report;
  - how the candidate got here.
- The BU head decides there: Shortlist, On hold or Reject. On hold and Reject
  need a reason.
- Every job has a side-by-side comparison of its candidates. Ratings are
  printed in shaded cells, and "no evidence" is shown differently from a low
  score.

*The org admin's section.*
- **People:** invite someone (they get a one-time password, since there are no
  email notifications), change their role or business unit, deactivate them,
  or reset their password. Create and rename business units.
- **Privacy (DPDP Act 2023):**
  - deletion requests, which an admin completes (erasing the candidate's data)
    or refuses with a reason;
  - retention periods, with the number of candidates now past each one and an
    admin-confirmed deletion run;
  - consent records.
- **Transparency:**
  - every decision beside MITRA's recommendation at the time, marked as
    followed, differed, or not comparable;
  - searchable HR round transcripts, evaluations and screenings;
  - the AI usage register (purpose, model and prompt version);
  - the audit trail.

*A home for each role.*
- **HR:** what waits on them, how long it has waited at each step, the load on
  each job, screening scores, and their own week.
- **BU head:** the decisions that are theirs, how strong the field is for each
  job (every screened candidate as a dot on one scale), what they have
  decided, and their job requests.
- **Org admin:** where everyone stands, how far candidates get, time in each
  step, business units side by side, what happened to candidates sent on below
  the cut-off, the weekly flow, and recent activity.

*Look and feel.*
- The sidebar shows "N waiting for you".
- A soft gradient now sits behind every signed-in page, instead of sweeping
  through the buttons. Primary buttons are solid and turn the signal blue on
  hover.

**Why**

The user asked for the next steps (phase 3 and the org plan). They also asked
for analytics that fit each role rather than one dashboard for everyone, and
for the gradient to sit in the page rather than in the buttons.

**How**

- **Backend:**
  - `hiring/technical.py`: the `book_technical` stub, kept as an audit event so
    it needs no migration.
  - `hiring/results.py`, `hiring/transparency.py` and `org/admin.py`.
  - `hiring/insights.py`: `/overview` gains `ageing`, `jobs`, `conversion`,
    `stage_time`, `proceed_anyway` and `mine`, and there is a new `/badges`
    route.
  - Migration `0007`: retention columns on `organisations`, and the
    `deletion_requests` table.
- **Console:**
  - feature folders `results/`, `admin/` and `transparency/`;
  - `shell/home/` holds one page per role;
  - new charts in `shell/viz/`: `Ageing`, `ScoreStrip` and `Funnel`;
  - nav items now declare their own icon, sidebar section and badge
    (`registry.ts`).
- **Demo data:** `scripts/seed_demo_pipeline.py` now dates jobs before their
  candidates and records MITRA's recommendation on screening and HR-round
  decisions, the way the real flows do.

**Verified**

- Backend: 939 tests pass, including new ones for the admin rules,
  transparency, results, the technical stub and the role analytics.
- The console builds.
- Every new screen was checked in screenshots, in light and dark, and at phone
  width with no sideways scroll.

**Needs**

- Track I:
  - the real `book_technical`, keeping the stub's signature;
  - the `technical_booked` → `technical_done` move;
  - `<InterviewReport>` in `TechnicalReportSlot`;
  - consent capture in the candidate app.
- The shared-PR process: migration `0007`.
- The job-understanding chat should record its model and prompt version, for
  the register.
- A screen where people change their own password.

### The welcome page

The front door is new.
- **The interview room.** A sample technical round plays in a dark "room":
  - it connects first, while camera, microphone, screen share and distance are
    checked;
  - Mitra's orb rings as she asks;
  - the candidate's camera tracks the face, and their voice meter moves as
    they answer;
  - the transcript types itself;
  - integrity tiles show face, gaze, screen share, and distance within the
    50–80 cm band;
  - an integrity note says "Recorded, not scored".
- **Floating quotes.** Two rows of candidates' quotes drift past continuously,
  each marked "Found in the transcript". Hovering pauses them.
- **Start.** The button opens the three ways in (HR, BU head, Organisation).
- **The journey.** Four steps, from CV to decision.

Every line in the room and every quote is invented, and the page says so.
"Reduce motion" shows a still room and a static set of quotes. Files:
`features/auth/InterviewRoom.tsx`, `Welcome.tsx` and `welcome.css`. The old
welcome rules were removed from `shell.css`.

### Calmer, and less to read (same day, after review)

- **Motion is quieter.**
  - Nothing scrolls or spins on its own: the quote stream is now a fixed row
    of three, borders no longer turn, the page wash is still, and there is no
    glow around Start.
  - The sample room shows each line whole as it arrives, without typing, and
    Mitra's orb rings once, slowly, while she speaks.
  - Motion only answers something a person did.
- **What is open looks open.**
  - The open section in the sidebar turns signal blue.
  - Open panels, open tabs and an opened "More insight" section are tinted.
- **Each home shows what that role acts on:** three numbers, a queue of five,
  one or two charts, and the first five jobs, with "Show all" for the rest.
  - Everything else is behind a "More insight" panel, which remembers whether
    it was left open. Nothing was removed.
  - Cards, numbers and rows are smaller.
- **Transparency** opens on people's decisions, 15 to a page, with a single
  "Did people follow MITRA?" bar and a button to show the ones that differed.
  MITRA's recorded facts are one click away.

### The welcome page's live element, and demo sign-in (same day, second review)

- **The mock video call is gone.** The welcome page now has a quiet card,
  "Inside every MITRA interview". One line at a time fades in, holds and gives
  way to the next ("Asks…", "Listens…", "Watches…", "Checks…", "Leaves…"):
  - the verb is in the signal blue;
  - small markers show which line is showing, and selecting one jumps to it;
  - hovering pauses it.
- **Demo sign-in.** On a developer's machine, `GET /api/auth/demo` lists the
  seeded demo accounts, but only those that really sign in with the demo
  password.
  - The sign-in screen pre-fills the account for the chosen way in, and offers
    one-click buttons for every demo account.
  - On a deployed instance (`db.is_deployed()`) the list is always empty, and
    `DEMO_LOGIN=0` hides it locally too.
  - The demo password now lives in one place, `org.seed.DEMO_PASSWORD`.

### One-screen welcome, full-width pages, a clearer candidate page (third review)

- **Welcome page:** it is the opening section only. The quotes and the four
  steps are gone, and the section is centred to fill one screen.
- **Page width:** signed-in pages use up to 1680px of width, instead of 1080
  or 1240.
- **Caching:** the server sends the console's `index.html` with
  `Cache-Control: no-cache`, so a new build shows on the next load. The hashed
  bundles can still be cached.
- **The candidate page is reordered** around what a person needs:
  1. **What happens now:** who acts next (and "Your move" when it is you),
     with only that stage's actions.
  2. **At a glance:** four small tiles for CV fit, evidence in the CV, the HR
     round and the technical round.
  3. **Strengths and gaps to probe.**
  4. **The CV against the job:** a compact table, heaviest competency first.
     Selecting a row shows the reasoning and the quoted lines.
  5. **At the side:** MITRA's summary of the CV, the HR questions (five shown,
     "Show all" for the rest) and the history, newest first.

  Figures are smaller throughout.
- **Simulating a finished technical round:** `scripts/simulate_technical_done.py`
  marks a booked technical round as done, on a developer's machine only, so
  the second half of the journey can be walked through before Track I's
  interview lands. It refuses to run on a deployed instance.
