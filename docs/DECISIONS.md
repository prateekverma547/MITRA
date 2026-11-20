# Decisions, and the measurements behind them

Most of what looks arbitrary in this codebase was measured. This file exists so
nobody quietly undoes a decision that cost real sessions to reach.

Every entry says what was tried, what the numbers were, and what would change
the answer. If you disagree with one, the useful move is to re-run its
measurement rather than to argue.

---

## Voice stack

### Pipecat, self-hosted, not a managed voice agent

Rejected: Vapi, OpenAI Realtime speech-to-speech, Deepgram's Voice Agent API.

Reasons, in order of weight:

1. **Turn-taking must stay tunable.** `SMART_TURN_STOP_SECS` is set so a
   three-second thinking pause is never cut off. That is a DoD requirement and
   rule one of the tuning order. A managed orchestrator makes it a black box.
2. **Observability.** Every number in this document comes from observers on
   Pipecat frames. Inside a managed agent, "it feels slow" stops being
   diagnosable.
3. **Cost at forty-minute sessions.** Deepgram's Voice Agent is $4.50/hour, or
   $3.00 per interview. The assembled stack is well under a dollar.
4. **The transcript is the auditable ground truth**, and it must be ours.

**Would change the answer:** an orchestrator exposing a tunable endpointing
threshold and frame-level telemetry.

### Deepgram for STT, replacing OpenAI

The decision rule was written before the measurement: median STT lag of
300-500ms means keep OpenAI, ~1s or more means run a Deepgram trial. It came in
at 1.04s and the rule fired.

**The mechanism is a protocol gap, not accuracy.** Ending a turn requires
knowing the transcript is complete, and a provider says so by confirming
finalisation. Verified in Pipecat's source: `confirm_finalize()` is called zero
times by the OpenAI service and once by Deepgram's. Without it, every turn waits
out a fixed timer however fast the words arrived. Pipecat's own defaults say the
same thing: 1.66s for OpenAI, 0.35s for Deepgram.

Measured on `int_3c38981d581a` (10 samples) against the OpenAI baseline (12):

| | OpenAI | Deepgram | |
|---|---|---|---|
| `stt_lag` median | 1.04s | **0.27s** | −74% |
| `stt_lag` max | 1.08s | **0.31s** | −71% |
| endpointing median | 1483ms | **378ms** | −74% |
| TTFA median | 2430ms | **1694ms** | −30% |

About 736ms off every turn. One turn measured **1450ms**, under the < 1.5s DoD
target this project had recorded as *unreachable*. It was unreachable because of
the vendor, not the architecture.

Deepgram is also **72% cheaper**: $0.0048/min against `gpt-realtime-whisper` at
$0.0170, so roughly $0.19 per forty-minute interview instead of $0.68. Note we
had deliberately chosen OpenAI's most expensive transcription model for speed
and still waited out the timeout, because none of theirs confirm.

**Status:** trial, switched by `DEEPGRAM_API_KEY`. Present means Deepgram,
absent means OpenAI, and that is the whole switch, reverting needs no deploy.
When a full-length session confirms it, delete the OpenAI path. This is a trial,
not an abstraction layer.

**Would change the answer:** truncated transcripts. The finalisation wait is
0.35s against a measured max of 0.31s, and that margin is thin. If the
interviewer ever answers half a sentence, raise it before doing anything else.

### Turn-taking: VAD plus semantic endpointing, `stop_secs = 4.0`

Silero VAD with `stop_secs = 0.2` decides *someone is speaking*. Smart-turn v3
decides *they have finished*. When the semantic model is confident it releases
immediately; the 4.0s is only the fallback when it stays unsure.

The default of 3.0s collided directly with the requirement not to interrupt a
three-second thinking pause, so it is 4.0.

**This is the largest remaining source of latency and it is a deliberate
trade.** A live session showed one turn at 5734ms, of which 4321ms was this
timeout, and the log shows it working: the candidate answered haltingly, five
separate pauses were tolerated without cutting her off, and only then did the
timeout expire.

Rule one of the tuning order: **never trade "doesn't interrupt" for "feels
fast."** Lowering this is a decision to be taken deliberately with a listening
session, not a tuning tweak.

### A turn is an exchange, not an utterance

Live session `int_0a7ca5d0aca5`, a Business Analyst interview booked for forty
minutes, reached its closing after **291 seconds** with every section marked
`insufficient`. Nothing crashed. The brain genuinely believed it had finished.

The candidate spoke in short clauses with pauses between them. The transcript
recorded 12 interviewer turns against **52 candidate utterances**, one answer
arriving as eight of them:

```
[86s] I
[88s] under I tried to understand the client 1st.
[89s] So that
[92s] I get to know what their businesses are.
```

`turns_spent` was incremented once per utterance, so a six-turn section was
spent on a single reply. Replaying the real transcript through the brain:

| | sections entered | ends in |
|---|---|---|
| counting utterances | 8 of 8 | `closing`, matching the live abort |
| counting exchanges | 3 of 8 | `prioritization`, still interviewing |

Only the first utterance after an interviewer turn now spends a turn. The rest
are the same answer still arriving.

**The text-mode harness could not have caught this**, and that is the general
lesson rather than a detail: a `ScriptedCandidate` replies exactly once per
question, so an answer is never split. The suite was 474 green before the fix
and 474 green after it. Three tests now encode the live transcript.

**The upstream cause is separate and still open.** `UserStartedSpeakingFrame`
fired **74 times in 291 seconds**, so the turn really was starting and stopping
every few seconds. Two things contribute:

- `endpointing=False` on Deepgram disables `speech_final`, which is the signal
  meaning *the speaker stopped*. Pipecat emits a `TranscriptionFrame` on every
  `is_final`, which only means *these words will not change* and fires during
  continuous speech. Verified in `services/deepgram/stt.py`.
- Smart-turn v3 judges each falling-tone clause a finished turn for a speaker
  who talks this way.

**Fixed on the second point.** Pipecat hardcodes `probability > 0.5` inside
`LocalSmartTurnAnalyzerV3`, a coin flip: a clause that sounds 51% finished ends
the turn. `ConfidentSmartTurnAnalyzer` raises that bar to `TURN_END_CONFIDENCE`
(0.8), overriding the threshold only and leaving the model, its features and its
inference untouched.

This cannot hang a turn. `stop_secs` remains the backstop, so a candidate the
model stays unsure about is released after the same 4s already accepted for a
thinking pause. It also costs nothing on a clear ending, where the probability
is high and the turn releases at the first VAD gap as before. The direction of
the trade is deliberate: more patient, never less, per rule one.

**0.8 is a starting point, not a measurement.** `turn_end_probabilities` records
what the model actually said on every turn, so set it from a real session.
`false_turn_ends` and `false_turn_end_rate` measure the symptom directly: a turn that
ends and is resumed within 2.0s did not end because the candidate had finished.
**Read that number off the next real session before touching
`SMART_TURN_STOP_SECS` or `VAD_STOP_SECS`**, because rule one of the tuning
order still applies and the audible behaviour was never the complaint here.

### The turn ceiling follows the time the section was given

A flat six turns for every competency, whatever it had been allocated. An eleven
minute section and a four minute one got the same six questions.

Across **20 competency sections in 15 stored interviews**, what ended a section:

| ended by | sections |
|---|---|
| turn ceiling | **14** |
| time budget | **0** |
| judge said `sufficient` | **0** |

Of the three ways a competency can end on its own terms, one fired always and
two had never fired at all. Sections used 2% to 58% of their allocated time,
median 17%. Interviews ran at **18 to 26% of their configured length**: the
employer sets twenty minutes, the bot says "about twenty minutes" out loud, and
the interview ends after five. Candidates assumed the clock had run out.

`ceiling_turns = 6` had no entry here. Its only justification was a field comment
saying an over-running section "is eating another competency's time" — a scarcity
that has never existed, because the budget has never fired. The case it does
protect against, a section that will not converge, is now held by the two-probe
term allowance and the let-go.

**The divisor is measured.** Seconds per competency turn, from
`section_outcomes`:

| sample | n | median | mean | p10 | p90 |
|---|---|---|---|---|---|
| under current code | 12 | **21.6s** | 23.4s | 18.0 | 32.9 |
| before the turn-counting fix | 8 | 7.0s | 7.5s | 1.9 | 16.4 |

The second sample is excluded: that is the bug above, where fifty-two utterances
counted as twelve turns. So `SECONDS_PER_COMPETENCY_TURN = 22`, and a section
allocated N seconds can hold about N/22 exchanges.

**The floor is the old flat six**, so the change can only add questions to a
section, never remove them.

**The cap is the material the blueprint actually planned**, not a round number
and not a guess at the candidate's patience. `blueprint/generate.py` asks the
model for "3 or 4 questions" per competency. Each one carries the three stages of
the depth ramp, and the section's term allowance adds two more, so the cap is
`3 × seed_questions + MAX_TERM_PROBES_PER_SECTION`. Past that the interviewer has
run out of planned material and is improvising, which is not what a competency
budget was allocated for. It bounds a wrong allocation, not a long conversation:
a ninety minute interview with one competency would otherwise hand a single topic
more than two hundred turns.

**What would change the answer.** Re-measure the divisor if turn-taking,
endpointing or the twenty-word turn rule move, because all three change how long
an exchange takes. Raise the cap only by generating more seed questions: with
four planned questions a twelve minute section still cannot be filled without
improvising, and that gap between `allocate_minutes` and what generation plans is
the next constraint, not this one.

### Neither STT vendor does its own endpointing

`turn_detection=False` on OpenAI, `endpointing=False` on Deepgram. Both offer
silence-based endpointing, and letting either have an opinion would duplicate
the turn-taking layer or quietly undo its pause tolerance.

### The greeting is spoken, not generated

The opening line was already written word for word in the prompt and the model
was reproducing it almost verbatim. A full round trip for a known sentence cost
around seven seconds of somebody wondering whether the call had connected.

It is now assembled in Python and handed straight to TTS. The second benefit is
larger than the speed: **the introduction can no longer be skipped or
pre-empted.** A candidate saying hello first used to consume the opening turn,
and the interview would begin without ever saying who was asking.

`GREETING_SETTLE_SECONDS = 1.0` exists because `on_client_connected` fires
before the candidate's browser has subscribed to the audio track, and anything
spoken in that window is lost, reported live as "Good" missing from "Good
morning". The LLM round trip used to hide this by accident.

---

## Models

### Three roles, never collapsed into one

| variable | role | default | why |
|---|---|---|---|
| `OPENAI_LLM_MODEL` | live conversation | `gpt-4.1-mini` | latency is everything |
| `OPENAI_BLUEPRINT_MODEL` | blueprint generation | `gpt-4.1` | latency irrelevant, this is the IP |
| `OPENAI_FEEDBACK_MODEL` | scoring | `gpt-4.1` | off-path, judgement about a person |

**Never put a reasoning model on the live conversation path.**

### `gpt-4.1-mini` stays on the live path, measured, not assumed

The guardrail said: once the sectioned brain passes its suites on mini, run the
scripted-candidate tests against both models and compare. If mini complies
lazily, the default shifts, no debate.

Run after four mandatory blocks had been added to the prompt, which is precisely
the instruction load the guardrail exists to test. The one failure found was
**stacking two questions into a single turn**, which matters out loud, because
the candidate hears both, holds neither, and answers the easier one:

| scenario | mini before | mini after | 4.1 before | 4.1 after |
|---|---|---|---|---|
| thin answer | 35% | **0%** | 13% | 0% |
| contradiction | 9% | **0%** | 0% | 0% |
| off topic | 22% | **0%** | 0% | 0% |
| repair | 38% | **0%** | 8% | 0% |

**The fix was the prompt, not the model.** The rule existed as a bullet in a
list; it is now its own block at the top of `VOICE_RULES`, naming the tempting
move and forbidding it with a say-this/not-this pair.

Re-run `scripts/compare_models.py` after any edit to `VOICE_RULES` or the
mandatory blocks.

---

## Prompting

### Structure beats emphasis, measured three times

**Contradiction callbacks**, 10 trials per model:

| prompt version | gpt-4.1-mini | gpt-4.1 |
|---|---|---|
| "raise it once, only if it fits naturally" | 0% | 0% |
| "your next question MUST be about it" | 80% | 27% |
| forbids the alternative, supplies the sentence, placed at the top | **100%** | **100%** |

Every failure at the middle version looked identical: the model asked the
natural conversational follow-up instead. **Removing the alternative beat adding
emphasis.** Note also that the *stronger* model complied *less* at the middle
version, instruction strength is not intuitive.

The same shape then worked twice more: the one-question rule above, and the
repair blocks that force a return to the same question.

### Judge precision is measured too

`tests/test_judge_precision.py`, 12 labelled cases. Genuine contradictions must
be separated from hedges, non-answers, and legitimate opinion revision.
Revision is candour, and flagging it punishes honesty. Precision went 0.50 →
1.00 (recall 1.00) once the judge prompt listed the exclusions explicitly.

### No em dashes, anywhere a person reads

They are the strongest tell that text was machine-written, and this product is
shown to people deciding whether to trust it with hiring.

Two sources, and the second is larger: hardcoded copy, and model output. The
rule lives once in `shared/branding.py` as `PROSE_STYLE` and is appended to
every prose-producing prompt. Measured on the same report regenerated from the
same transcript: **3 em dashes before, 0 after.**

Prompt examples matter here. A sample question containing an em dash produced
em dashes in the transcript.

---

## Fairness and the record

These are the decisions that are about people rather than software.

### Nothing decides anything

`RecommendationSignal` describes **the strength of the evidence gathered**, not
whether to hire. An excellent candidate given a short interview is
`limited_evidence`. `FeedbackReport.is_decision` returns `False` and exists so
the intent is impossible to misread.

### Every score cites verified evidence

A model asked for verbatim quotes will occasionally produce a plausible sentence
nobody said. Every quote is matched against the transcript in code:

- matched on lowercase alphanumerics, which survives the model tidying
  punctuation but not invention
- found at a different turn than claimed → **re-anchored**, because the model
  miscounted but the words were said
- found nowhere → **dropped**, and if that leaves a score with no evidence the
  score is downgraded to insufficient signal rather than published
- only candidate turns count, or the bot's question becomes the candidate's answer
- an unevidenced red flag is dropped entirely: it is an accusation, not a finding

Verified against a real transcript (6/6 quotes independently confirmed) and
against a deliberately poisoned report, which lost its invented quote, its
invented red flag, and its inflated recommendation.

### Confidence is capped by weighted coverage

A confident signal requires ≥75% of the spec **by employer weight** to have been
assessed. Covering one of two competencies means something different depending
on whether it was the 60% one or the 40% one, and counting cannot tell them
apart.

### The channel is evidence about the connection, never about the candidate

Repairs, dead air, echo and disconnects go in one place (`ConversationHealth`).
Without one, they silently become evidence about the person: a cheap headset
reads as an incoherent answer inside a hiring record. A degraded recording is
stated at the top of the report and caps the recommendation.

**The first calibration was wrong, and only real recordings showed it.** Counting
short turns as broken audio flagged five of eight real interviews, healthy ones
included, because the STT emits "uh", "so" and "okay" as separate turns in every
session. Fillers are excluded now and fragment counts are never decisive alone.

> **A signal that fires on everything cannot be the one that decides anything.**

### A candidate can always leave

An explicit instruction to stop ends the interview at once. Only a softer signal
gets a single confirming question, and repeating it in any form counts as
accepting. Nobody is asked a third time. Skipped competencies say *"chose to end
the interview… this reflects their decision to stop, not their ability"*, never
the time-limit wording.

### A revised spec never reaches backwards

Reopening a profile's spec regenerates plans for candidates who have not been
interviewed. It never touches one who has: their blueprint is the record of what
they were actually asked, and rewriting it would let a later report score them
against competencies that did not exist during their interview. Hand-refined
plans are left alone and flagged stale, so discarding an employer's own edits is
their choice rather than a side effect.

---

## Infrastructure

### One bot process per interview, spawned at join

Not at scheduling. Creating an interview a week early costs nothing until
somebody joins.

Bots die on redeploy. Accepted for the POC; the mitigation is operational, see
[OPERATIONS.md](OPERATIONS.md).

### Concurrency is capped, and the cap refuses rather than degrades

`MAX_CONCURRENT_INTERVIEWS`, default 6. A bot is ~224MB idle. One too many gets
the container OOM-killed and restarted, which ends **every** interview running on
it. Turning one candidate away costs a reschedule; accepting them costs all of
them.

Counted from live child processes, never from interview rows: a bot that crashes
never writes `completed`, so a status-based count drifts upward until the cap
refuses everything forever.

Memory would allow roughly 19 on the 8GB replica. The default is 6 because
**per-bot CPU is unmeasured** and VAD plus turn detection run continuously.
Raise it from the Metrics tab, not from the memory arithmetic.

### No queue

FastAPI `BackgroundTasks` plus a status column. Work is scheduled when its input
arrives, never when its output is needed.

### Replicas stay at 1

A bot is a child process of one specific container. More replicas means a
redeploy or rebalance can orphan a live interview in ways that are hard to
observe.

### Migrations through Alembic, not at startup

The app used to add missing columns to live tables at startup. That could not express
a rename, a backfill, a dropped index, or a data-dependent change, and it altered
production silently every time the app started.

Schema changes now go through Alembic revisions (`backend/alembic/versions/`). The
startup check refuses a database that is not exactly at the head revision, and refuses
one that has tables but no migration record. A test fails when a model changes without
a revision (`alembic check`). Deploys run `alembic upgrade head` before the app starts.
See [OPERATIONS.md](OPERATIONS.md), "Database migrations".

---

## Deliberately not built

Not oversights. Ask before building any of these.

| | why |
|---|---|
| Google Meet / Zoom / Teams | own branded join page instead; never browser-bot hacks |
| Proctoring, video analysis | out of scope for a POC, and ethically loaded. One narrow exception is recorded below: the camera hearing check. Everything else in this row stays out |
| Detecting scripted or AI-assisted answers | same |
| Automatic hiring decisions | reports inform humans; nothing auto-rejects |
| Multi-tenant auth | single admin password. Do not half-build accounts |
| Nuisance-noise detection | misclassifying a nervous candidate as noise costs far more than missing a time-waster. The honest outcome is already right: answers that never address the question leave coverage insufficient |
| Distress detection | wrong in either direction is bad, and it lands in a record about a person's composure |
| Other languages | `EvaluationSpec.language` exists and is read by nothing. Better honestly dead than half-wired |

---

## Known open items

| | state |
|---|---|
| Cost telemetry (tokens, TTS characters, STT minutes) | **not built.** An M3 DoD item. Every cost figure here is arithmetic, not observation |
| `SMART_TURN_STOP_SECS` | largest remaining latency; needs a listening session |
| Deepgram trial | won on one session; needs a full-length interview, then delete the OpenAI path |
| Bot evaluating out loud | observed saying "that's a good approach", which leaks judgement and biases the next answer |
| Echo detection in the live loop | currently counted only after the fact |
| Plain HTML + JS frontend | Replaced React + Vite + TypeScript. Both pages are plain JS modules served from `frontend/static` with no build step. The Daily SDK is vendored at 0.94.0 so the CSP stays `'self'` plus Daily. CLAUDE.md still says React and needs updating |

### Time the interview is scheduled for is time it is given

**What was wrong.** The turn cap (`3 × seeds + 2`) ended most competency sections
long before their time budget. Turns per section were fixed by the seed count the
blueprint asked for, and a section that finished early gave its unused minutes to
nobody: the rebalance only ever squeezed sections, it never granted time back. A
40-minute interview could finish at about half its length.

**What changed.**

- **Session clock.** `InterviewBrain.session_remaining_seconds` counts down from
  the scheduled `duration_minutes`. It is stated to the interviewer on every turn,
  in the shared header of the system instruction, so repairs and stop offers carry
  it too. Grace is excluded: the clock reads zero at the scheduled end.
- **Time handed forward.** When a section ends early, its unused minutes are shared
  among the remaining competencies by weight. The grant is measured against the
  scheduled length, not the hard stop, because grace is margin for overrun, not
  time to plan for. The closing keeps its own budget.
- **Seed questions sized to the time.** `seed_questions_needed(budget)` is the
  inverse of the turn cap: `ceil((budget/22 - 2) / 3)`, clamped to 3–8. The
  blueprint generator asks for exactly that many per competency, and
  `build_blueprint` logs a warning when a model returns fewer than its final budget
  needs. The brain and the generator share this one function.

**What is not yet measured.** The 22-second divisor and the `3 × seeds` cap were
measured against the old 3–4 seed counts. Neither has been measured with 8 seeds.
Re-measure `section_outcomes` on a real 40-minute interview before trusting the
count.

**Open issue, recorded not fixed.** `_render_seed_question` shows seed `k` at turn
`k`, one seed per turn. The cap assumes each seed carries three turns (the depth
ramp). So with 8 seeds, turns 9 onward get no seed block, and the measurement above
found that turns without a seed produced far fewer mechanism probes. Either the
seed index should advance every three turns, or the cap should follow the seeds
actually shown. Choosing between them needs a measured comparison.

---

## Camera hearing check (narrow exception to "video analysis")

**Decision, owner-approved:** the candidate's camera may be read for one purpose
only: to decide whether speech that no words came back for was real. Lip movement
is judged still or moving; if the lips stay still through an utterance, that
utterance is not counted as an unheard answer.

**What it may do.** Remove an "unheard" count from the hearing monitor. Nothing else.
It cannot end a call, add a count, score the candidate, measure attention or
composure, or feed the brain. A moving mouth, a missing face or a moving head leaves
the audio-only behaviour unchanged.

**Why this and not the rest of the row above.** The audio is what the assessment
rests on. The camera is used only to stop a noise from being mistaken for an unanswered
question, which the audio alone cannot tell apart. The failure it prevents is a
call ending for no reason, which costs the candidate their slot.

**Evidence.** Lip movement separated speaking from silence on a still face (AUC 0.99
on one recording, 0.98 to 0.99 with a steady-head gate). Head movement alone raises
the same measure, which is why the reading is trusted only while the head is steady.
Tested on two or three recordings, not on a labelled set of interviews.

**Off by default.** `CAMERA_HEARING_CHECK` (see `backend/bot/config.py`) must be set
for any candidate video to reach the bot. It also needs the `mediapipe` library. If
the flag is set without the library, the call proceeds audio-only and logs an error.

**Before it is switched on for real candidates:**
- the candidate consent notice must say that the camera is analysed to check hearing,
  and must be reviewed by the owner;
- the `mediapipe` dependency must be added to the deployment image and lock file
  (not done here, so the build is not changed by accident);
- a labelled test where the candidate moves their head while silent must pass, since
  that is the case head-movement gating exists for.

**Where it lives.** `backend/bot/lip_activity.py` (the measure, no vision import),
`backend/bot/camera_hearing.py` (the observer), `backend/bot/hearing.py`
(`note_mouth`), `backend/bot/services/daily.py` (video received only when the flag
is on).
