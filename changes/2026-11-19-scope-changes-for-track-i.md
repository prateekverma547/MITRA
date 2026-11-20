## 2026-11-19: Scope changes since the plan, and how Track I's work joins

**What**

Product decisions taken on Track H since docs/DEV_PLAN.md was written. Where
they differ from the plan, these win. They decide which of Track I's work is
merged as is, which is adapted, and which is no longer needed.

| Area | The plan said | Now |
| --- | --- | --- |
| Product UI | Track I builds the candidate app (`frontend/candidate-app`), the live view, the report component and the dashboards | **The console (`frontend/console`) is the product UI**, including the candidate's room at `/meet`. Track I's apps stay for Track I's own testing; what is merged from Track I is backend logic and contracts |
| Live watching | HR and the BU head watch the MITRA interview live (hidden join, watchers' notes, Arbiter) | **No one joins the candidate's call.** The live room (`#/candidates/:id/technical-room`) is a read-only status page: Daily presence, the plan by the clock, integrity facts. Consent no longer mentions live watching. Watcher join, watchers' notes and the Arbiter are not needed |
| HR round | Recorded video call; after the call the recording is transcribed and diarised, then evaluated | **Voice only, no camera.** HR's room starts Daily's live transcription; each line is saved with its speaker as it arrives, and the evaluation starts from it the moment HR ends the round. The recording path stays as the fallback |
| HR questions | Built from screening's competency gaps | **An HR screen:** motivation, the candidate's own path from the CV, working with people, practicals (notice, CTC, location). Skills are left to the MITRA round |
| Naming | "Technical round" | **"MITRA interview round"** on every screen (MITRA interviews for non-technical roles too). Stage ids are unchanged |
| Proctoring in the browser | Track I's MediaPipe face and iris landmarks | The console room already runs MediaPipe's face detector (`features/interview-room/proctor.ts`): a face in view, one person, head turned away. Reminders are capped at 3 per kind. **It stores nothing yet**: Track I's event intake is the missing half |
| Report | `<InterviewReport>` from `frontend/ui` mounted in H's results page | The console renders the report itself (`features/results/TechnicalReport.tsx`) from the `FeedbackReport` contract; the Integrity section reads `IntegrityReport` once it is stored |
| Decisions | Screening and HR round offered "Not now" | Proceed or Reject only. `parked` stays in the stage machine, unused by the UI |

**What Track H needs from Track I next**

1. **An integrity event intake** the room can post to, with the candidate's
   meeting credentials, like `/interviews/focus`: `gaze_away`, `face_missing`,
   `second_person`, `share_stopped`, `second_display`, with start/end seconds
   and the reminder number. Stored, then surfaced as the `IntegrityReport`.
2. **The `IntegrityReport` stored** against the interview and returned with the
   results.
3. **HR findings into the plan:** `hr_rounds.evaluation.findings_for_technical`
   fed into blueprint generation.
4. **Duration per booking** (TwoLinks `duration_minutes` chosen by HR).
5. **The Daily recording-download fix** in `bot/services/daily.py`
   (`yarl.URL(link, encoded=True)`; Track H works around it in
   `hiring/hr_evaluation.py`).
6. **The bot's Daily name stays "Interviewer"** (or `BOT_NAME`): the room
   recognises Mitra by it.
7. **No candidate-facing captions**, as decided. The room shows none.

**How the two tracks join, each stage**

1. Track I pushes the stage to `track_i`; Track H pushes `track_h`.
2. Track H merges `origin/track_i` into a local integration branch cut from
   `track_h` (never into `dev` directly) and resolves conflicts:
   - **UI:** the console wins;
   - **Track I's own files** (bot, feedback, proctoring, live, analytics):
     Track I's version wins;
   - **Shared files** (`app/main.py`, `app/interviews.py`, `shared/`,
     migrations): merged by hand, keeping both sides' routes and fields.
3. Out-of-scope pieces are switched off, not deleted (watcher join, the
   `/interview` app as a product link), and noted here.
4. The integration branch passes:
   - the backend suite;
   - the console build;
   - the end-to-end run: BU head request, HR screening, the voice HR round with
     live transcript, the MITRA interview by voice with proctoring, the report,
     the BU head's decision.
5. Findings go back to Track I as a change note. Track I fixes them on
   `track_i` while starting the next stage.
6. After stage 5, both open PRs into `dev`: Track I's first (its shared files
   reviewed by Track H), then Track H's, merged on top of it. CI green, one
   full run on the `dev` environment, then `stg` when no interview is live.

**Confirm**

Read alongside docs/DEV_PLAN.md; this note supersedes it where they differ.
