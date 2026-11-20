## 2026-11-19: The candidate's interview room, in the console

**What**

The candidate now has a room in the console, at `/meet`, for both rounds.
Before the call it is light; the call itself is dark, as Meet's is.

1. **Enter.** The candidate types the meeting ID and password. The invitation
   link carries the meeting ID (`/meet?id=…`) and never the password.
2. **Consent.** The page says what will actually happen, and each item has its
   own checkbox, none of them ticked in advance:
   - technical round: an AI interviewer, recorded or not recorded, proctored,
     and that someone may watch live;
   - HR round: a person runs it, it is recorded, and MITRA summarises it
     afterwards for HR.
   Both rounds end with a line on the candidate's DPDP rights.
3. **Lobby.** Meet's "Ready to join?" screen, with real checks:
   - camera and microphone, with a sound-level meter and device pickers;
   - a speaker test;
   - for the technical round, the entire screen. A shared window or tab is
     refused.
   A second display gets a warning but does not block joining.
4. **Room.**
   - **Technical round.** Mitra's presence moves with Mitra's actual voice
     level. The candidate's self-view sits in the corner. A chip says when
     someone from the hiring team is watching. Recording and a timer show
     against the booked length.
   - **Controls.** Mute, with Ctrl or Cmd + D. The camera is locked on in the
     technical round. Screen-share state, with "Share again". Help. End.
   - **Notices.** When sharing stops. When the connection is reconnecting.
     When the page went out of view: recorded every time, mentioned at most
     three times.
   - **Ending.** It asks "Do you want to stop here?" once. No captions are
     shown in either round.
   - **HR round.** The HR person fills the stage, with a waiting state until
     they arrive. If they leave, the room says so and does not end the call.
5. **Done.** The page says what happens next and repeats the retention period
   and the candidate's data rights. A dropped connection gets "Rejoin" instead.

**Why**

The product decision is that our console carries the frontend and Track I's
work is the backend logic. The room therefore uses Track I's candidate
endpoints unchanged:
- `POST /interviews/join` records consent and starts Mitra;
- `POST /interviews/focus` stores page-out-of-view absences.

The one thing those endpoints could not answer is what consent must describe
before the candidate agrees.

**How**

- `backend/hiring/room.py` (Track H): `POST /api/room/lookup`, public.
  - It is authenticated the way `/interviews/join` is, with one error message
    for every failure.
  - It returns:
    - the round and its status (open, finished or closed);
    - the role, the company and the candidate's first name;
    - the length, from the plan for a technical round and 30 minutes for HR;
    - whether the round is recorded, from the same switch the recorder reads
      (an HR round always is);
    - the retention period.
  - It changes nothing: no consent is recorded and no bot is started.
- `backend/app/main.py` (shared): `GET /meet` redirects to `/console/#/meet`.
  It keeps only a digits-and-hyphens meeting ID from the query.
- `hiring/hr_round.py` and `hiring/technical.py`: join links are now
  `/meet?id=<meeting id>`.
- Console:
  - `features/interview-room/`;
  - an `open` route flag in `shell/registry.ts` and `App.tsx`, so the room
    renders for everyone, signed in or not, outside the shell;
  - `vite.config.ts` forwards `/interviews` in development.
- The Daily call object is created in the lobby and carried into the call.
  Creating and destroying call objects is serialised, because Daily allows one
  at a time. Video is capped at 640x480, 15 fps, one 200 kbps layer, as the
  old page did.
- Development only: `#/meet?preview=consent|lobby|room|room-hr|done` shows a
  step with sample details. It is not reachable in a production build.

**Shared files (review)**

- `backend/app/main.py`: the `/meet` redirect.
- `backend/tests/test_interviews.py`: `GET /meet` and
  `POST /api/room/lookup` added to `PUBLIC_ROUTES`.
- `backend/tests/org/test_auth.py`: the same lookup endpoint added to the
  signed-out sweep's public list.
- `backend/tests/test_recordings.py`: `room.py` added to the files that may
  read the recording switch. It reads it for the consent notice only, as
  `main.py` does.

**For Track I**

- Mitra is recognised in the room by the Daily `user_name` "Interviewer" (or
  `BOT_NAME`). Every other remote participant in a technical room is shown as
  someone watching. If the bot's name changes, change `isMitra` in
  `features/interview-room/media.ts` with it.
- Screen-share stops and second displays are shown to the candidate but stored
  nowhere: `/interviews/focus` only takes `hidden` and `blur`. An events
  endpoint for the `IntegrityReport` would let the room send them.
- Gaze and distance reminders need face tracking. The room has no source for
  them yet, so it shows none, and consent does not offer to switch them off.

**Confirm**

```bash
cd backend && uv run pytest -q          # 977 passed, 22 deselected
cd frontend/console && npm run build
# then open /meet with the meeting ID and password from an HR-round or
# technical-round booking
```
