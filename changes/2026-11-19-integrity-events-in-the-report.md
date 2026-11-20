## 2026-11-19: The MITRA round's integrity events reach the report (option A)

**What**

The candidate's room warned about a covered camera, a second person, looking
away and a stopped screen share, but stored only tab switches, so the report
said nothing about the rest. Now every one is stored and shown.

- **Track I's code, ported into the console** (`features/interview-room/proctoring/`):
  the engine, the reminder policy and its wording, gaze rules, the server clock,
  the sender and its API, from `frontend/candidate-app/src/proctoring/` and
  `src/api/backend.ts`. Unchanged but for import paths, plus one added method,
  `judgedGaze`, that takes our face check's "head turned away" through their
  same gaze rules (a glance never counts; 6 s, or 5 looks in a minute, does).
  Their engine, reminder and gaze tests are ported too.
- **Option A:** our own face check (`proctor.ts`) still watches the camera. Four
  times a second its face count and head direction go into their engine, with
  the browser's own events: tab hidden, window not in focus, the screen share
  (whole screen, a window, or stopped), paste and a second screen.
- **The room** (`proctoring/useIntegrity.ts`, `Room.tsx`): keeps the proctoring
  token from the join in memory and posts events to Track I's intake
  (`/api/proctoring/interviews/{id}/events`), plus a status reading once a
  second, which also lets the server notice a dropped call in seconds. Reminders
  now come from their engine, still at most 3 per kind, 2 minutes apart, shown
  for 6 seconds, with their wording. The End button closes every open event and
  sends it before telling Mitra; closing the page sends them by keepalive. The
  old `/interviews/focus` reporter is gone: tab and focus are events now.
- **The report and the live room** (`hiring/technical.py` `integrity_facts_for`):
  read the stored events and word them, for example "No face in view for 12
  seconds; reminder 1 of 3 shown" and "Screen sharing stopped for 35 seconds;
  asked to share the whole screen again". Older interviews' tab reports still
  show. Screens are unchanged.

Facts for a person to review, never part of a score. Nothing but events and
readings leaves the page: no image, video or sound.

**Thresholds** (Track I's, untuned on real people): no face for 3 s, a second
face for 2 s, the window out of focus for 2 s. Ours were 5 s and 3 s.

**Confirm**

- Book a MITRA round and join. Cover the camera for about 10 seconds: "We can't
  see you clearly" (1 of 3). Stop sharing the screen, then share again.
- In HR's live room, and in the report afterwards, both appear with their times,
  lengths and what the candidate was shown.
- `cd frontend/console && npm test`: 82 passed. Backend: 2,172 passed.

**Update 2026-11-20: the face warning is continuous**

Product owner's decision: whenever the candidate's face is not in view, the
warning is on screen, every time, for as long as it lasts, not three times.

- The room shows "We can't see you clearly. Please sit facing your camera, with
  your face in view." after 1.5 s without a face, and removes it the moment the face
  is back (`Room.tsx`). Same amber notice as before; no count, no limit.
- `face_missing` joins the technical prompts (`TECHNICAL_PROMPT_KINDS` in
  `shared/interview_contracts.py`, `TECHNICAL` in the console's `reminders.ts`),
  the contract's existing state for a prompt shown every time, like "share your
  screen again". Every occurrence is still recorded; the report reads "No face in
  view for 12 seconds; a warning was on screen until their face was back in view".
- The other reminders (second person, looking away, leaving the tab) keep their
  limit of 3, 2 minutes apart. For Track I: a change to your contract and your
  reminder set, by the product owner's decision.
