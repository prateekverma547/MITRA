## 2026-11-18: the HR round, and a console that moves

**What**

HR can book the HR round from a candidate who is ready for it. Booking gives a
private room, a meeting ID and password to send (with copy buttons and a
ready-made invitation), and moves the candidate to "HR round booked". HR then
joins the call inside the console as the room's host. Beside the call are the
ten screening questions as a checklist and a notes box, both saved as HR
works. HR can end the round, or cancel a booking nobody joined, with a reason.
No bot ever joins an HR round.

The console also feels alive now:
- Every action is a real button with an icon and a spinner while it works.
- Pages slide forward and back. Rows are clickable anywhere.
- A live blue-violet-cyan gradient appears on primary buttons and on the
  one or two things on a page that matter most.
- A slow aurora sits behind the welcome page.
- Loading shows shimmering placeholders and a progress bar, numbers count
  up, and toasts confirm decisions.

All of it respects "reduce motion".

**Why**

Track H phase 2 starts with the room and the people in it; the transcript and
the evaluation follow. The user asked for a more animated, interactive,
premium console.

**How**

- `interviews.round` (`technical` or `hr`, migration 0005).
  - `app/interviews.py` skips the bot slot and the bot spawn for HR rounds.
  - `JoinResponse.round` tells Track I's candidate app which mode to use.
- `hiring/hr_round.py`: book, view, join (owner token), autosave progress,
  end, and cancel. Only HR sees the password.
  - The `hr_rounds` table holds what HR asked and noted.
- Console:
  - `features/hr-round/` with the Daily call embedded via `@daily-co/daily-js`.
  - `shell/ui/`: Button and ButtonLink, BackButton, FileDrop, CopyField,
    Spinner, Skeleton, CountUp, Toast, TopProgress and RowLink.
  - The router moved to `createHashRouter`, because page transitions only run
    under a data router.
  - `lucide-react` for icons.

**Left undone**

- Recording the HR round and transcribing it, and the evaluation that moves
  the candidate to "HR round done". The bot used to start recording from
  inside the call; with no bot this needs Daily's recording API, and the
  approach depends on the account's settings.
- The old candidate join page shows only the candidate's own video, so in an
  HR round they hear HR but do not see them. Track I's candidate app covers
  this with its HR mode.

**Confirm**

```bash
cd backend && uv run pytest -q          # 877 passed
cd frontend/console && npm install && npm run build
```
