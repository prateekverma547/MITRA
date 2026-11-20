## 2026-11-20: Pre-screening HR questions, skipping the HR round, MITRA retakes, phone-call HR rounds

**What**

Product owner's feedback, five items.

1. **HR questions are a pre-screening call** (`screening/fit.py`). The same ten, in
   order, for every candidate: profile in two minutes; why change now (current
   employer and tenure); current CTC and its split; expected CTC; notice period;
   location and work mode; the focus they want (areas from the CV); one timeline
   point to clarify; other offers; what they know about us and why this role. Code
   owns the fixed wording. The model writes only the four CV ones, and for the
   tenure returns the employer and start month; code counts the tenure ("about two
   and a half years": the model said "six months" for May 2024). Each CV question
   falls back to a general one when the CV does not give its detail. Prompt version
   `hr-questions-2026-11-20-prescreen-template`; earlier candidates keep their set.
2. **More than one MITRA round**, as many as HR wants, each a retake of the same plan.
   New move `technical_done -> technical_booked` (HR). A retake that is cancelled,
   missed or fails returns the candidate to `technical_done` when an earlier round
   has a report (`technical_booked -> technical_done` is now system or HR). Each
   round keeps its own report: `rounds` in the round view and the results;
   `GET /api/candidates/{id}/results?interview=` shows one; the results page has a
   round switcher. Bookings are stamped to the microsecond, so rounds never tie.
3. **HR round on HR's own phone** ("Listen"). Migration `0010`: `hr_rounds.mode`
   (`room`, `listen`) and `phone_consent_at`. Booking with `{"mode": "listen"}` makes
   the room but sends no meeting ID or password. Joining needs HR's tick that the
   candidate agreed to the call being recorded and transcribed. HR's microphone runs
   without echo cancellation or noise suppression (the phone's speaker is the
   candidate's voice); live transcription asks Deepgram to tell the voices apart;
   the first voice, or one asking an HR question, is HR's; a "Swap speakers" button
   fixes a mix-up, saved lines included (`POST .../hr-round/transcript/swap`). After
   the call the recording is transcribed and attributed as before.
4. **Skipping the HR round** (`POST /api/candidates/{id}/hr-round/skip`, optional
   reason). From `ready_for_hr`, or `hr_booked` while the room is unjoined (it is
   cancelled). New moves to `ready_for_technical` by HR; recorded as a decision
   "HR round skipped"; MITRA's plan is prepared at once. The candidate page,
   results and comparison show "Skipped".
5. **Why the first MITRA round is slow: measurement only.** Stored data: the model
   is normal everywhere (0.7 to 1.1 s); slow sessions wait for turn detection
   (median 2.9 to 5.0 s, timeouts on 33 to 100% of replies against 0 to 11%), and
   were each the first after a break. MITRA starts within 1.4 to 3.4 s and greets
   at the same point in every session. The face check is loaded once per tab and
   kept (`proctor.ts`), which fits "slow first, fast after". Added, to prove or
   disprove it: the room reports its face-check load time, main-thread stalls,
   join timings, whether it was the tab's first call and its microphone settings
   (`hiring/diagnostics.py`, `features/interview-room/diagnostics.ts`), and
   `scripts/latency_report.py` puts them beside the reply timings. Remove once
   the cause is known.

**Confirm**

- New candidate: the HR questions read as the ten above, with the tenure counted.
- Skip the HR round from the candidate page; the results say "Skipped".
- After a MITRA round, "Book another MITRA round"; the results switch between rounds.
- HR round, "Listen to a phone call": both ticks needed; the transcript labels the
  two voices; "Swap speakers" fixes them.
- Run three MITRA rounds in a freshly opened tab and three in the same tab after
  them, then `cd backend && uv run python scripts/latency_report.py`.
- Backend: 2,198 passed; console: 84 passed.
