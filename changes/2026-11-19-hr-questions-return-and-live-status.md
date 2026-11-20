## 2026-11-19: HR questions you can read live, returning after a decision, and statuses that say what is happening

**What**

1. **The HR round's questions, before and during.**
   - Before the round, on the start screen and in the lobby, all of MITRA's
     questions are shown to read and prepare with, two columns wide.
   - In the room the questions panel is open from the start, and is wider.
   - The panel has a prompter at the top: the question to ask now, in large
     type, with why it is asked, "Asked, next", and previous and next. The full
     list sits below it; clicking a question brings it into the prompter.
2. **Returning after a decision.** Once a decision is saved, the page returns
   to where you came from, with a confirming toast. This covers:
   - the screening decision;
   - HR's decision after the HR round;
   - the BU head's decision;
   - "Send to the BU head".

   Opened from a pasted link, the page goes to the job's candidate list or to
   the candidate instead.
3. **Statuses that say what is happening.**
   - **Technical round.** The candidate page and the live room show, in steps:
     - the interview;
     - "Candidate left the call" (Mitra waits about two minutes for them to
       rejoin);
     - "Interview ended" (closing and saving);
     - "Compiling results" (scoring and checking quotes);
     - "Report ready".

     Before this change, the booking vanished from the candidate page between
     the end of the call and the report, and "in progress" stayed up after the
     candidate had left.
   - **HR round, in the room.** When the candidate leaves, the room says so and
     offers "End round and evaluate". Before, it showed "Waiting for them to
     join" again.
   - **HR round, on the page.** The header and the BU head's view say who is
     actually in the room. When the room has emptied with the round still
     open, a notice offers to end it so the evaluation can start. The page
     refreshes this every 8 seconds while the round is live.
4. **Fix:** the HR room created a second Daily call object on a remount while
   the first was still being destroyed, and showed "You left the room". Call
   objects are now created and destroyed one after another, as in the
   candidate room.

**Why**

- The candidate leaving changed nothing on record until Mitra closed the
  session minutes later, so every screen said "in progress".
- A decision saved on a page left HR on that page with nothing left to do
  there.

**How**

- `backend/hiring/presence.py` (new): Daily's
  `GET /rooms/{name}/presence`, which reports who is in the room now.
  - Each participant is sorted into Mitra ("Interviewer" or `BOT_NAME`), the
    candidate (by name, or Track I's `candidate:` user_id) or someone else.
  - Answers are cached for 4 seconds, with a 3-second timeout.
  - It is a fact for the screens only and moves nothing. When Daily cannot be
    asked, the screens use the stored status as before.
- `hiring/technical.py`:
  - `live_state` takes presence and adds `starting` (a 45-second grace after
    joining), `candidate_left` and `closing`;
  - `round_state` adds `scoring_step` (saving, then compiling);
  - `GET /candidates/{id}/technical` returns `round` while booked or just done;
  - the live endpoint returns `presence` and `scoring_step`.
- `hiring/hr_round.py`: the round view carries `presence` while in progress.
- `tests/hiring/conftest.py` (new): presence is stubbed for every hiring test,
  so no test reaches Daily even with a real key in `.env`. New tests are in
  `tests/hiring/test_presence.py`.
- Console:
  - `features/results/RoundStatus.tsx` (new), the shared states and steps;
  - `features/hr-round/RoomPanel.tsx`: the prompter and `QuestionBrief`;
  - `MeetingRoom.tsx`: questions open, the brief in the lobby, the candidate
    leaving, and the serialised call;
  - `HRRoundPage.tsx`: presence words, the empty-room notice, the poll;
  - `useGoBack` in `shell/ui/BackButton.tsx`.

**Confirm**

```bash
cd backend && uv run pytest -q          # 980 passed, 22 deselected
cd frontend/console && npm run build
```
