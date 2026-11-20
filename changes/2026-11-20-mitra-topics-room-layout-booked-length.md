## 2026-11-20: MITRA follows a topic offered twice, a light three-column room with rules and counted warnings, a length per booking

**What**

Product owner's feedback, three items.

1. **A topic the candidate keeps offering is followed when it fits the role**
   (`bot/realtime_coordinator.py`, `bot/realtime_prompt.py`). The first time a
   candidate says "I know about AWS", MITRA acknowledges it and stays on the plan,
   as before. The second time, the coordinator notices it in code (`offered_topic`)
   and sends MITRA a private note: if the topic is relevant to this role, ask about
   it now, at most two questions aimed at one of the plan's goals, then return to
   the plan; if not, acknowledge it and carry on. A prompt rule alone measured 0 of
   9; it conflicted with the "I don't know" rule, now reworded. With the note: 3 of
   3 follow LLMs for an ML role, 0 of 3 follow cooking. Each topic is followed at
   most once, and the "I don't know" count restarts when one is followed.
2. **The MITRA room, redesigned** (`features/interview-room/Room.tsx`, `meet.css`,
   `warnings.ts`). Three rounds of product-owner feedback the same day; the last
   asked for research first. What it found, and what was built from it:
   - **The interviewer is the centre.** Voice-agent screens are a legible state
     display around one presence. One calm stage holds MITRA, larger, with its
     state ("Listening", "Mitra is speaking") as a pill.
   - **No mirror of your own screen.** A live preview of the shared screen shows
     the interview page inside itself (Mercor's own docs call it "a hall of
     mirrors"). It is now a small card, top left: a thumbnail and "Entire screen
     shared", or red "Screen not shared".
   - **The camera in the corner**, bottom right of the stage, as in any video call.
   - **Calm until something needs fixing** (HackerRank and Talview warn only when
     something is wrong, each with a plain fix; candidates' anxiety comes from fear
     of being wrongly flagged). A rail on the right, headed "Instructions", lists the rules point by point,
     each with what is seen now and its warning count: keep your face in view; be
     alone in the room; keep your eyes on the screen; keep your entire screen
     shared; stay on this tab. Below, "All clear", or the warnings.
   - **A warning for every kind, every time, with its count** ("2nd time"), large
     and bold: screen no longer shared (with "Share again"), face not visible, more
     than one person, looking away (unless gaze reminders were turned off at
     consent), left the tab (shown on return). The stage gets an amber edge while
     one is showing. Face warnings wait 1.5 s (looking away 2.5 s) before showing
     and stay 3 s after the problem clears, so a flickering detector neither
     flashes them nor recounts them; a rule turns amber only while its warning
     shows, so the two always agree (a live test showed "More than one person in
     view" on the rule with no warning under it).
   - **The share card opens a split view** (product owner, after a live test).
     Clicking it gives the shared screen the left half of the page; the right half
     holds the instructions and warnings, then MITRA at full size with the
     candidate's camera in the bottom right corner, below it. "Minimise", or Esc, returns to the usual view, and
     so does the share stopping.
   - **"You are under live proctoring"**, with a blinking blue dot, at the top of
     the rail. True as written: the checks run throughout, and HR and the BU head
     can watch live.
 (dark only if the viewer chose it),
     with soft status colours. This covers the HR round's candidate room too.
   - Under 900 px wide the page stacks and scrolls: stage, then the rail.
   - **This supersedes "at most three reminders per kind, two minutes apart" for
     what the candidate sees** (CLAUDE.md, 2026-11-18 direction). What is recorded
     for the report is unchanged: Track I's engine still records every event, and
     its "reminder n of 3" labels still follow its own cap.
3. **HR picks the MITRA round's length when booking**: 20, 30, 40, 50 or 60
   minutes (`hiring/technical.py`). This uses Track I's work: `size_to_booking`
   and `apply_booked_timing` (`blueprint/refine.py`) and `booked_timing`
   (`live/booking.py`), with the same 20-60 bounds and message. The stored plan
   keeps every area, and the bot sizes it to the booked length when the interview
   starts (`bot/run_realtime.py`), so a retake can be booked at a different
   length. Default: the plan's own length. The room's clock and the live view use
   the booked length.

**Confirm**

- In a MITRA round, offer the same relevant topic twice: MITRA asks one or two
  questions on it, then goes back to the plan. Offer an unrelated one twice:
  acknowledged, not followed.
- In the room: stop sharing, cover the camera, bring a second person into view,
  switch tabs and come back. Each shows its own warning with its count, and the
  rule's line shows how many warnings it has had.
- Book a MITRA round at 30 minutes: the room's clock counts down from 30, and
  MITRA closes on time.
- Backend: 2,202 passed; console: 85 passed.
