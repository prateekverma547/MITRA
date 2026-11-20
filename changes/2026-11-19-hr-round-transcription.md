## 2026-11-19: Better HR round transcripts (Deepgram nova-3, checked against the recording)

**What**

HR saw fragments in the live transcript ("Ski, / your down, / share / Chi"). The
live feed is Daily's built-in transcription (Deepgram) with no model named and
`language: "en"`. Measured on two recorded HR rounds against what HR actually
asked: the same audio through Deepgram `nova-3` gave the whole conversation;
OpenAI's file models were close but invented sentences on quiet stretches
("Thank you for watching, I hope you enjoyed my video"); `gpt-realtime-whisper`,
streamed like a live call, caught almost nothing.

- **Live view** (`features/hr-round/MeetingRoom.tsx`): Daily transcribes with
  `model: "nova-3"`, `language: "multi"` (Indian English and Hindi-English mixed)
  and `smart_format`. If Daily refuses those settings, the room falls back once
  to the previous ones, so HR never loses the live view to this change.
- **The evaluation** (`hiring/hr_evaluation.py`): with `DEEPGRAM_API_KEY` set,
  ending a round with live lines now waits for the recording, transcribes it with
  Deepgram `nova-3` (`multi`, diarized) and evaluates from that. The live lines
  are kept beside it (`attribution.live_transcript`). They are used instead when:
  - the recording's two voices cannot be told apart (the live lines name each
    speaker for certain; the recording is one mixed track);
  - the recording cannot be transcribed;
  - there is no recording.
  Each case is noted in `attribution.recording_check`. Without a Deepgram key,
  nothing changes: live lines are evaluated at once, OpenAI transcribes a round
  without them.
- The progress panel shows the recording's steps while a round waits for it.

**Why**

The transcript is what the evaluation quotes; fragments became weak or missing
evidence. Deepgram on the recording was the most accurate source that never
invented a sentence, and an invented sentence in a hiring record is the worst
failure this product has.

**Confirm**

- Run an HR round with two people in different rooms. The live column should
  read as sentences. After ending it, the panel shows "Collecting the recording",
  then "Transcribing", then the evaluation; the badge shows how the voices were
  told apart.
- On a round where one person played both parts, the evaluation uses the live
  lines (`recording_check` says the voices could not be told apart).
- Deploying: `DEEPGRAM_API_KEY` must be set on Railway for the recording path.
- Not yet measured properly: a known 3 to 5 minute script read by two people,
  scored against the script.
