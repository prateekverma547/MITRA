## 2026-11-19: the HR round is recorded, transcribed and evaluated

**What**

When HR joins an HR round from the console, Daily starts recording the call.
When HR ends the round, MITRA:
1. collects the recording once Daily has finished it;
2. transcribes it and tells the two voices apart;
3. evaluates the candidate's answers against six soft skills;
4. moves the candidate to "HR round done".

HR sees each step as it happens. Once the evaluation is in, HR sees:
- MITRA's recommendation: proceed, reject, or discuss;
- each soft skill rated with the candidate's own words, and a link from every
  quote to its line in the transcript;
- the points to probe in the technical round;
- the full transcript.

HR then decides: Proceed to technical, Not now (with a reason) or Reject
(with a reason). The recommendation is stored beside the decision.

If a step fails, HR can retry it, and the retry resumes where it stopped. A
round with no recording (because HR did not join it from the console) can be
completed on HR's notes, with a reason.

**Why**

Track H phase 2: the HR round has to produce evidence a person can check before
they decide. Per decision 27 there is one combined recording, not one track per
person.

**How**

- `backend/hiring/hr_evaluation.py`:
  - ffmpeg shrinks the recording to 16 kHz mono mp3, under OpenAI's 25 MB
    upload limit;
  - `gpt-4o-transcribe-diarize` returns the speaker segments;
  - the voice that asked the screening questions is taken as HR, and the
    confidence (clear, likely or uncertain) is stored and shown;
  - quotes must be the candidate's own words and are verified in code; a
    rating left without a verified quote becomes "nothing to go on";
  - the steps run as a restartable state machine, with a background loop
    started at app startup (`HR_PIPELINE_DISABLED` turns the loop off).
- `bot/services/daily.py`: `create_meeting_token(start_cloud_recording=True)`
  for HR's token.
- New routes: `POST /api/candidates/{id}/hr-round/retry` and `/complete`.
- `shared/stages.py`: HR may now move `hr_booked` to `hr_done`, but only through
  `/complete`, which requires a failed evaluation and a reason.
- Migration `0006`: evaluation status, error, transcript, attribution and
  evaluation columns on `hr_rounds`.
- Console: `AfterRound.tsx` (progress, failure, evaluation and decision) and
  `Pips.tsx`.

**Verified**

- 10 new tests in `backend/tests/hiring/test_hr_evaluation.py`.
- A synthetic two-voice round through the real transcription and evaluation
  models:
  - 16 segments and 2 speakers, told apart as "clear";
  - the recommendation was proceed to technical;
  - every quote was verified.

**Needs**

- `ffmpeg` on the machine that runs the pipeline. The Docker image has it;
  locally, run `brew install ffmpeg`. Without it, a recording over 25 MB fails
  with that instruction.
- The candidate's old join page shows only their own video, so in an HR round
  they hear HR but do not see them. Track I's candidate app replaces that page.
