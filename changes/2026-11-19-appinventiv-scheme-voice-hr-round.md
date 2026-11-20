## 2026-11-19: Appinventiv's colours, a voice-only HR round with a live transcript, HR-type questions

**What**

- **Colour scheme.** The console now uses Appinventiv's scheme, taken from
  appinventiv.com:
  - electric blue `#1a69fd` (hover `#0c4fcb`) as the signal and the primary
    button;
  - near-black `#111` ink, with a warm off-white `#f8f7f4` page and warm greys;
  - a dark theme of true near-black;
  - the live gradient moves to blue, sky and cyan.

  The chart colours were re-validated with the dataviz script in both themes.
- **Type.** The root size is 18px. Small labels are larger, and nothing is
  smaller than 12px.
- **Welcome page.** The MITRA core is larger, with a turning neon ring and a
  breathing glow. The four cards are larger and float a little faster. The
  headline and intro text are larger.
- **"Technical round" is now the "MITRA interview round" on every screen,**
  because MITRA interviews for non-technical roles too. This covers stage
  labels, buttons, statuses, the backend's on-screen messages and the
  Transparency stage name. Internal identifiers (`ready_for_technical`, the
  routes) are unchanged.
- **No "Not now".** The screening decision offers Proceed or Reject. After the
  HR round the choices are "Proceed to MITRA interview" or Reject.
- **Leaving an edit.** "Edit with MITRA" can be left with "Keep it as it was"
  (`POST /api/jobs/{id}/keep`), which restores the agreed summary with no
  version change. When MITRA finishes a chat, the page scrolls up to the
  summary and its buttons instead of leaving them out of view.
- **CV upload.** After a CV is uploaded the page shows "<first name>'s résumé
  is uploaded", the file name, Proceed to screening and Re-upload, filling the
  width. The parsed text is no longer shown.
- **"What happens now".** The MITRA interview round's status is a full-width
  horizontal stepper.
- **HR questions are now an HR screen.**
  - The mix is: motivation and career story (2); the candidate's own path from
    this CV, naming real companies, tenures and moves (3); working with people
    (2); practicals (notice period, CTC, location and work mode, other offers)
    (3).
  - Technical depth is left to the MITRA round, and screening's skill gaps are
    no longer fed in.
  - Prompt version `hr-questions-2026-11-19-hr-screen`.
- **The HR round is a voice call.**
  - No camera on either side. Each voice tile's rings move with that
    speaker's actual loudness, with Speaking, Listening or Muted beside it.
  - HR's room starts Daily's live transcription after joining. Lines appear
    under the voices as they are spoken, each tagged with the participant who
    said it, and are saved every 2 seconds
    (`POST /api/candidates/{id}/hr-round/transcript`).
  - The questions prompter and the notes sit beside the transcript.
  - On the candidate's side (`/meet`), consent says the call is recorded and
    transcribed live. The candidate sees no captions.
- **Evaluation from the live transcript.** Ending a round that has one goes
  straight to `evaluating`, with no collecting and transcribing of the
  recording, and attribution is `live` (who said what is known). Without one,
  for example if transcription could not start, it falls back to the
  recording as before.

**Why**

- Product feedback, 2026-11-19.
- A live transcript tells the speakers apart for certain, and removes the
  minutes spent waiting for Daily's recording file.

**Shared or other-track files**

None. Everything here is in Track H's folders.

**Costs and setup**

- Daily bills transcription per minute; it must be enabled on the Daily
  account. If it is not, the room says "Not available: MITRA uses the
  recording instead" and nothing else changes.

**Confirm**

```bash
cd backend && uv run pytest -q          # 984 passed, 22 deselected
cd frontend/console && npm run build
```
