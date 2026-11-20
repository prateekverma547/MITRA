## 2026-11-19: Logo azure, real business units, one-page CV upload, the HR voice module, on-device proctoring

**What**

- **Colour.** The signal colour is now the Appinventiv logo's azure: `#009ad9`
  for glows and gradients, `#0077b6` wherever text or a button needs 4.5:1 on
  white, `#3dbdf5` in the dark theme. The chart colours were re-validated.
- **Business units.** The seed now creates AI, ML & Data Science; Pre-sales;
  Design; Sales; Marketing; Frontend; Backend.
  - The second demo BU head is `sales.head@mitra.local`, with a Sales
    Executive job.
  - The tests follow.
  - A local database was updated in place, after a backup.
- **Adding a candidate is one page.**
  - The name and CV are on the left and what screening does is on the right.
  - Once the CV is uploaded, the same card shows "<name>'s résumé is
    uploaded" with Proceed to screening and Re-upload.
  - Proceed opens the candidate with screening under way.
- **"Begin HR round" stays on the page,** which then offers "Open the HR round
  room" and Reject. Only a rejection returns you to the list.
- **Until the HR round,** a highlighted "Questions for the HR round" block
  leads the candidate page, and "Gaps to probe" moves to the side beside it.
- **The HR room is a voice module, not a video call.**
  - **Before joining:** a light card with a microphone check (a level that
    moves with your voice), microphone and speaker choice, "Join the call" and
    Mute, beside the joining details. The question list is gone from here.
  - **In the call:**
    - **The voice module:** the timer, recording, each person with a live
      level and Speaking, Listening or Muted, and Mute, Leave call and End
      round.
    - **The live transcript, as a chat:** HR in blue on the right, the
      candidate on the left, and lines still being heard in italics. It keeps
      its scroll position while you read back, then follows new lines again.
    - **Compact questions and notes,** with smooth, self-contained scrolling.
- **The MITRA interview round's room:**
  - **What the proctoring sees, shown to the candidate:** face visible, one
    person, eyes on the screen, screen shared, on this tab. Each check is green
    or amber.
  - **A preview of what they are sharing,** bottom left.
  - **The time:** the elapsed time in large figures, with "about N min left",
    turning amber in the last 5 minutes and red when over.
  - **Reminders on screen only,** at most 3 of each kind and 2 minutes apart,
    numbered "n of 3":
    - left the tab;
    - looking away (held 3 s);
    - face not visible (held 5 s);
    - someone else in view (held 3 s).
  - **A consent option:** "Turn off reminders about looking away".
- **On-device face watching:** `features/interview-room/proctor.ts`.
  - It uses MediaPipe's face detector (`@mediapipe/tasks-vision@1.2.0`;
    the wasm comes from jsDelivr and the model from Google's model store),
    running in the candidate's browser about 4 times a second.
  - Head direction comes from six face keypoints, and a change must hold for
    3 samples before it counts.
  - Nothing is uploaded and nothing is scored.

**For Track I**

- Looking away, a hidden face and a second person are shown to the candidate
  but not stored. `/interviews/focus` only takes `hidden` and `blur`. An events
  endpoint for the `IntegrityReport` would let the room send them; the room
  already counts them.
- `proctor.ts` is a stand-in for Track I's proctoring and can be swapped at the
  merge.

**Confirm**

```bash
cd backend && uv run pytest -q          # 984 passed, 22 deselected
cd frontend/console && npm run build
```
