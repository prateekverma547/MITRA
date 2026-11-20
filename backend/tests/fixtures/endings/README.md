# Ending samples (Session A, round 16)

One stored record per way an interview ends, for Sessions B (report and live pages) and
D (summaries) to use as test fixtures. Five are REAL calls; one is CONSTRUCTED and says so.

| File | Ending | Real? |
| --- | --- | --- |
| `completed.json` | `interview_complete` / `completed` (Mitra wrapped up and called end_interview) | real |
| `withdrew.json` | `candidate_withdrew` / `withdrew` (she asked once, they said yes) | real |
| `end_button.json` | `candidate_ended_button` (the app's End button) | real |
| `did_not_return.json` | `candidate_did_not_return` (left, not back in 90 s) | real |
| `silence_close.json` | `abandoned_by_candidate` / `silence_close` (about two minutes of silence) | real |
| `hard_stop_CONSTRUCTED.json` | `interview_complete` / `forced_at_hard_stop` | **constructed** (see `how_made`) |

How the real ones were made: the real route end to end (Daily room, the real bot, OpenAI
Realtime), the candidate a headless browser speaking synthetic clips (macOS voice "Rishi"),
a seeded scratch database. No real person, no personal data, no credentials (checked by
`tests/test_ending_samples.py`, which also proves its search can find one).

| File | Code | Transcript |
| --- | --- | --- |
| `completed.json`, `withdrew.json` | round 16 (after the goodbye fix) | stored, full `Transcript` |
| `end_button.json` | round 16 (before the goodbye-cut fix, which cannot affect it: the goodbye was the only audio) | stored, full `Transcript` |
| `did_not_return.json`, `silence_close.json` | **round 15** (not re-run, to save time: Karandeep's choice) | **trimmed**: round 15's harness kept only index, speaker, text, pre_interview, timing_source, cut and audio times, so these do not validate as a full `Transcript` (no `at_seconds`); the test checks them with a relaxed shape |
| `hard_stop_CONSTRUCTED.json` | constructed from the real `completed.json` | stored; only the ending is rewritten |

`completed.json` used a short test plan so a completion fitted a short call. Field
`transcript` says `stored` or `trimmed`; `code` says which round's code made it.

Each file: `sample`, `real`, `how_made`, `interview_id`, `ended_because` (EndReason),
`ending` (session_metrics["ending"]: ending, hang_up_by, areas, notes, backstop_moves, …),
`coordinator_events`, `connection`, `transcript_turns`, `transcript` (stored or trimmed), `code`, `events` (the live event
stream), `feedback_report`, `cleaned_transcript`.

Notes for readers:
- From round 16 the call hangs up only after the goodbye that FOLLOWS an accepted
  end_interview (A-106): in `completed.json` and `withdrew.json` the tool is called first
  (silently) and the goodbye is the last turn. The two round 15 records predate this.
- The words for each ending come from the codes (Session B's `ui/src/report/endingWords.ts`);
  the backend sends codes only.
