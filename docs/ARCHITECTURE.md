# Architecture

How MITRA is put together, and which part owns which problem.

Read [BRAIN.md](BRAIN.md) next if you are changing interview behaviour, and
[DECISIONS.md](DECISIONS.md) before changing anything that looks arbitrary. A
surprising amount of what looks arbitrary was measured, and the numbers are
there rather than here. [CHANGELOG.md](../CHANGELOG.md) holds the history of why
each thing became what it is. This document is the map.

---

## The one-paragraph version

An employer uploads a job description and talks to an AI until the evaluation
criteria are settled. They upload a CV, and a candidate-specific interview plan
is generated from the two. The candidate joins a branded page with a meeting ID
and password, and a bot conducts a voice interview against that plan. Afterwards
the transcript is scored into a report of evidence, which a human reads. Nothing
in the system decides anything about anyone.

---

## Four subsystems

They are deliberately separable. Only one of them is real-time.

```
backend/
├── blueprint/   JD and CV -> what this interview should test        (offline)
├── bot/         the live voice interview                           (real-time)
├── feedback/    transcript -> a report of evidence                 (offline)
├── app/         FastAPI: uploads, auth, spawning bots, serving     (request)
└── shared/      Pydantic contracts every other package imports
```

`shared/contracts.py` is the single source of truth for every shape crossing a
package boundary. Nothing redefines those types locally. If you are tempted to,
the type is probably wrong and should change there.

### blueprint/, deciding what to ask

| file | job |
|---|---|
| `documents.py` | pull text out of PDF, DOCX, txt |
| `clarify.py` | the multi-turn chat that fills an `EvaluationSpec` |
| `generate.py` | CV + spec -> `InterviewBlueprint`, including time budgets |
| `refine.py` | change a generated plan by describing the change |

`simulated_employer.py` supplies scripted employers, including one that answers
a different question than the one asked.

Runs on the reasoning-tier model. Latency is irrelevant here, and this is where
the product's intelligence is front-loaded: deep CV analysis, per-claim
verification targets, questions written against the CV's thin spots.

**Time budgets are computed in code, not by the model.** Models cannot be relied
on to make a set of numbers sum exactly. The model supplies a relative
`emphasis` per competency; minutes are derived and normalised in
`allocate_minutes`.

Six job description templates live as Markdown in `backend/jd_templates/`,
served by a guarded `GET /jd-templates`. **They are text only**: a template fills
the JD box and decides nothing, the clarification chat still runs in full, and
the text is always editable before it is used. An uneditable template would feed
the model words the employer never meant, and every competency after that would
be derived from them.

### bot/, conducting the interview

A Pipecat pipeline, one OS process per interview, spawned by the API when the
candidate joins.

```
Daily in -> STT -> user aggregator -> BrainDirector -> LLM -> TTS -> Daily out
                                           |
                                     InterviewBrain
```

| file | job |
|---|---|
| `run_bot.py` | wires the pipeline and owns the session lifecycle |
| `brain/` | the state machine and context planner. See [BRAIN.md](BRAIN.md) |
| `brain_director.py` | the Pipecat adapter: rewrites context, fulfils judgements |
| `turn_taking.py` | VAD and semantic endpointing: *when has the candidate finished* |
| `services/` | STT, TTS and the live LLM, one small adapter each so a vendor swap is one file |
| `blueprint_source.py` | the seam that loads a plan. Fixture or database, one function |
| `ending.py` | hanging up when the interview is over |
| `tools.py` | the `end_interview` function the model can call |
| `config.py` | environment, model tiering, and the feature switches |

`silence.py` runs the escalation ladder for dead air, `presence.py` tracks who
is actually in the room, `greeting.py` puts the opening on the candidate's clock
rather than the server's, `observers.py` carries the instrumentation and
`persistence.py` does the final write.

**Observers versus processors matters and has bitten repeatedly.** A processor
only sees frames passing its own position; an observer sees every frame wherever
it sits. Anything reacting to what the *output* transport emits, such as the bot
finishing speaking, must be an observer. `ending.py` is one for that reason.

### feedback/, scoring the transcript

Runs once, at the end, against the complete saved transcript. Never
incrementally, never between turns, never on the conversational path.

| file | job |
|---|---|
| `score.py` | the model call, plus quote verification |
| `health.py` | three separate accounts of whether the interview can be trusted |
| `run.py` | orchestration and persistence |

**Every quote is verified against the transcript in code before it reaches a
report.** A fabricated quote inside a hiring record is the worst failure this
product has, so a score whose evidence does not survive verification is
downgraded rather than published.

`health.py` holds three classes that exist for one reason: **a technical failure
must never be written down as a fact about a person.** `ConversationHealth` is
about the channel, `JudgmentHealth` about our own analysis, `QuestioningHealth`
about the interviewer. Each is derived from stored data rather than recorded
live, so rebuilding an old report picks up improved heuristics, and each caps the
recommendation rather than adjusting a score.

### app/, the API and the panel

| file | job |
|---|---|
| `main.py` | uploads, clarification, blueprint generation, serving the UI |
| `interviews.py` | create, join, run, persist an interview |
| `recordings.py` | optional recording, the retention sweep, playback |
| `jd_templates.py` | the job description starting points |
| `auth.py` | single-admin password and session cookie |
| `capacity.py` | how many bots may run at once |
| `db.py` | SQLAlchemy models and the additive migration |
| `meeting.py` | human-readable meeting IDs and passwords |

Async work uses FastAPI `BackgroundTasks` plus a status column. No queue, no
Celery, no Redis. **Work is scheduled the moment its input arrives, never the
moment its output is needed:** blueprint generation starts at CV upload, scoring
starts when the transcript is saved.

---

## The brain

This is the part a new reader gets wrong most easily. [BRAIN.md](BRAIN.md) is the
full account; what matters here is where it sits.

**The brain is a context planner and a state machine, and it never generates
text.** `plan_turn()` assembles what the model will see; `observe()` updates
state from what was said. Generation stays in the streaming pipeline, because a
brain that returned finished text would delay the first token until the whole
response existed and roughly double time to first audio.

**It is pure and synchronous.** Nothing in `brain.py`, `state.py`, `prompting.py`
or `terms.py` is `async`, nothing awaits, and none of it imports Pipecat. Work
that needs a model is *requested* and fulfilled elsewhere:
`pending_judgment_request()` hands one out, `apply_judgment()` takes the answer
back, and if it has not arrived then heuristics rule that turn and the interview
carries on. That seam is what lets the text-mode harness drive the identical
object with a different loop.

A turn is governed by three things in order. Whether something has claimed the
turn outright, which is the mandatory-block arbitration described below. Which
section is current, which decides the persona, the material and the seed
questions. And how many turns have been spent in that section, which drives the
depth ramp and the turn ceiling.

A section ends for one of a small set of reasons, recorded on the outcome as
`_advance_reason`: the judge ruled coverage sufficient and the floor has been
met, the depth ceiling was reached, the budget ran out, or the interview is
being closed early. The ordering of those tests is load-bearing and was
reordered once already, because `ceiling` was shadowing `depth_reached` and
nothing downstream could tell which had happened.

**A turn is an exchange, not an utterance.** Speech arrives in fragments, and
only the first utterance after an interviewer turn spends a turn. Counting each
fragment separately once ran a 40-minute interview to its closing in 291
seconds.

---

## The prompt

Every block that renders into a competency turn, in the order they are
concatenated by `render_section_prompt` (`bot/brain/prompting.py:1`). **The
ordering is load-bearing and was measured**, so do not move a block without
reading the note attached to it.

Two blocks return early and replace everything else. `OFFER_TO_STOP` outranks
all, because somebody who has asked to leave must not be asked to repeat
themselves. A repair block is next, because nothing else this turn is worth
doing if they could not hear the question.

Otherwise, in order:

| # | block | mandatory? |
|---|---|---|
| 1 | header: name, role, seniority, tone, `VOICE_RULES`, domain language | rules |
| 2 | `_render_refusal_guidance` | advisory |
| 3 | `_decided_question_early`: a contradiction, and only a contradiction | **mandatory** |
| 4 | `candidate_context`: the CV material | advisory |
| 5 | `body`: footing, the section, target depth, dealbreaker, one seed question, depth ramp | advisory |
| 6 | `_decided_question_late`: an unexplained term, or the let-go | **mandatory** |
| 7 | carried claims, on a section's first turn only | advisory |
| 8 | `_render_time_pressure` | advisory |
| 9 | `HOW TO INTERVIEW` guidance | advisory |
| 10 | red flags | advisory |
| 11 | scope | rules |

**The mandatory slot is split in two on purpose.** A contradiction sits above
the section's material because below the seed questions it competed with a page
of "here is what to ask about" and compliance fell to 27 percent against 80
percent from above. The unexplained-term half sits below. Moving the block
wholesale would have demoted the contradiction into the position already
measured at 27 percent, so the two halves were separated rather than moved
together.

A mandatory block names one specific item and hands over the sentence to say. An
advisory block is a preference among available options, and an advisory block
naming a specific item was measured at roughly no effect. This is where the
repo's general lesson comes from, **removing the alternative beats adding
emphasis**. The compliance table is in [DECISIONS.md](DECISIONS.md).

Four blocks can claim a turn and the arbitration between them is only partial.
That is written up as F6 in [AUDIT_FOLLOW_UP.md](../AUDIT_FOLLOW_UP.md).

---

## The judge

A second model, off the critical path, reading one section at a time. It runs on
the reasoning tier (`settings.blueprint_model`), wired in `bot/run_bot.py:268`.
It is requested by the brain and fulfilled by `BrainDirector`, so a slow or
failed judgement costs nothing the candidate can hear.

It is asked twice per section: `kind="depth"` after answers accumulate, and
`kind="section_end"` when the section closes. It is given the target depth, the
section's transcript, and claims carried from earlier sections. It returns five
fields (`JUDGE_SYSTEM`, `bot/brain/drivers.py:27`), and they are read by
different things with different persistence rules:

| field | who reads it |
|---|---|
| `coverage` | the brain's advance decision, and `SectionOutcome.coverage` |
| `rationale` | written to `SectionOutcome.depth_rationale` and read by nothing. See below |
| `claims` | carried across sections, persisted, and the raw material of the report |
| `contradictions` | the mandatory contradiction block, and the outcome |
| `unexplained_terms` | the term-probing machinery. Replaced wholesale, never merged |

The replacement rule matters. `apply_judgment` assigns the term list rather than
accumulating it, so a term the candidate has since explained stops being asked
about. The raise count survives the replacement, because that is a fact about
the section rather than about the term object.

**Precision is weighted above recall for contradictions.** A false contradiction
becomes a written claim about a real person's honesty; a missed one can still be
caught by a human reading the transcript. `tests/test_judge_precision.py` holds
12 labelled cases.

The judge is asked the wrong question at section end, which is F8 in the
follow-up audit and is open.

---

## The data model

Four tables: `jobs`, `clarification_turns`, `candidates`, `interviews`
(`app/db.py:145` onward). The interview row carries the transcript, the section
outcomes, the session metrics and the feedback report as JSON.

**The distinction that has caused the most bugs is what a fact is about.** It is
worth stating explicitly, because blurring it is how a technical problem becomes
a written judgement about a person.

A **fact about a candidate** is something they said or did: their CV text, their
transcript turns, a claim the judge extracted, a competency score with a
verified quote behind it.

A **fact about a section** is scoped to one topic and resets at its boundary:
turns spent, coverage, declined turns, which terms were raised and how often.
The raise count lives on the `Section` and not on the term, and a stale copy of
it on a term object is what once told candidates they had been asked twice when
they had been asked once, 29 times across six runs.

A **fact about the interview** is about the session as a whole: disconnects,
dead air, how many judgements failed, whether the interviewer repeated itself.
These belong to the three health classes and never to a competency score.

The rule that follows: **`declined_turns` is recorded separately from low
coverage.** "Declined to answer" and "answered shallowly" say different things
about a person and only one is about their ability. The same reasoning produced
each health class in turn. If you find yourself about to let one of these read
as another, that is the bug.

A candidate is scored against the spec snapshot embedded in their own blueprint,
never the profile's current spec. That is what makes revising a profile safe: an
already-interviewed candidate is still measured against what they were actually
asked.

---

## How an interview flows

```
POST /jobs                        JD uploaded, clarification chat begins
POST /jobs/{id}/clarify           repeated until an EvaluationSpec is agreed
POST /jobs/{id}/candidates        CV uploaded; blueprint generation starts here
POST /candidates/{id}/interviews  mints a private Daily room, ID and password
POST /interviews/join             credentials -> token, AND the bot is spawned
GET  /interviews/{id}             the report, once the bot has scored and exited
```

**The bot is spawned at join, not at scheduling**, and the transcript is written
before scoring is attempted. `/interviews/join` is the only route reachable
without the admin password that does anything; the rest of the public list is
pages and login. A test sweeps the route table and fails on any new endpoint
that is neither guarded nor listed in `PUBLIC_ROUTES`
(`tests/test_interviews.py:868`).

---

## Deployment

One image, two roles. The web process serves the employer API and the candidate
join page, and it spawns `python -m bot.run_bot` as a **child process of that
same container**, one per interview. Both need the same dependencies, so they
share an image (`backend/Dockerfile`).

Several constraints follow from that, and they are not independent choices.

**A redeploy kills every live bot.** The bots are children of the web process,
so a deploy takes them down mid-sentence. `STOPSIGNAL SIGINT` gives them a chance
to finish writing a transcript, but the interview is over either way. There is no
fix in the current shape, only an operational rule: check for active sessions
before shipping. This branch auto-deploys on push, which makes that rule sharper
rather than optional. For the same reason **replicas stay at 1**
(`desired_count` in `infra/envs/staging/variables.tf`, which refuses any value
above 1): a bot belongs to one container, and a rebalance would orphan a
live interview in a way nothing can observe.

**Concurrency is capped and the cap refuses rather than degrades**
(`app/capacity.py`). One bot too many gets the container OOM-killed, which ends
every interview on it rather than just the new one. The count comes from live
child processes and never from interview rows, because a crashed bot never
writes `completed` and a status-based count would drift upward until the cap
refused everything forever. Sizing is in [OPERATIONS.md](OPERATIONS.md).

**The database is the record of truth, not the container.** The transcript is
committed before scoring is attempted, and scoring runs outside that try block,
so a scoring failure can never take the transcript down with it. Everything a
report needs is reconstructible from stored rows, which is what makes the health
heuristics improvable after the fact.

Two build details bite anyone editing the Dockerfile: the build context is the
repository root rather than `backend/`, and the image mirrors the repository
layout instead of flattening it, because the app resolves the frontend directory
relative to its own file. Python is pinned to 3.12 because Pipecat 1.6.0 still
imports `audioop`, which 3.13 removed.

---

## Measurement is how this project decides things

A reader who does not know this will not understand the changelog, which is full
of tables of numbers attached to one-line changes.

Almost nothing here is changed on the strength of an argument. The pattern is:
state a decision rule first, build an instrument, run ten interviews a side
against a scripted candidate, and read the result even when it contradicts the
prediction. [CHANGELOG.md](../CHANGELOG.md) records the predictions that were
wrong as well, because that is usually where the useful finding is.

Every session writes a record to `backend/sessions/<id>.json` and to
`session_metrics` in the database: latency, transcription lag, tolerated pauses,
section transitions and judgements, and the silence ladder firing. The full list
and its targets are in [OPERATIONS.md](OPERATIONS.md).

`ttfa`, the time from the end of candidate speech to the first bot audio, is the
number that matters to a candidate. It decomposes into endpointing plus
generation, and knowing which half moved is the difference between a fix and a
guess.

The scripts under `backend/scripts/` hold real conversations against the live
model. They cost a little money and are the only way to check whether behaviour
*feels* right.

| script | what it answers |
|---|---|
| `compare_models.py` | the main harness: scripted candidates through the brain, with the signal set |
| `contradiction_rate.py` | does the callback instruction actually fire |
| `tool_call_check.py` | does the model reach for `end_interview`, and only then |
| `dev_interview.py`, `run_interview.py` | drive one interview locally |

The rest of `scripts/` covers one behaviour each: withdrawal, silence, unheard
audio, the STT vendor comparison.

**An instrument's scope matters as much as its logic, and getting that wrong has
caused three separate wrong findings.** Two cue sets in `compare_models.py` were
both named `_MECHANISM_CUES`, so one silently shadowed the other; and a 2-cue
detector was once compared against a 19-cue one as though they measured the same
thing. Before believing a number, check what produced it.

---

## Testing

Two suites, never mixed, because a flaky suite wastes the speed advantage
text-mode testing exists to buy.

```bash
cd backend
uv run pytest -q                                            # 754 deterministic, seconds
RUN_BEHAVIOR_TESTS=1 uv run pytest tests/test_behavior.py   # real model calls
```

**The tests are documentation.** Several encode specific live failures with the
actual sentences in them, and are worth reading first:

| test | what it records |
|---|---|
| `test_withdrawal.py` | the verbatim transcripts where withdrawal detection failed, and a skip that was read as leaving |
| `test_health.py` | why a heuristic that looked right flagged healthy sessions |
| `test_ending.py` | four separate ways the call failed to hang up |
| `test_repair.py` | the four sentences nothing detected |
| `test_judge_precision.py` | 12 labelled contradiction cases, precision 1.00 |
| `test_copy_style.py` | user-facing copy must not read as machine-written |

There is no JavaScript harness. Everything in `frontend/admin/admin.js` and the
candidate page's script ships unverified, and all CSS is unverified by
definition.

**Green unit tests have hidden broken features here more than once**, most
recently a counting bug that survived nine attempts at the same subsystem
because every test of it used a punctuation production never emits. If a change
affects how the bot behaves in an interview, run a real conversation through it
before believing the suite.

---

## What is dormant, and what is open

**Mechanisms that are correct in themselves and sit where they cannot fire.** A
reader who finds one of these and assumes it runs will be wrong.

**The over-run squeeze does not squeeze.** `_rebalance_remaining`
(`bot/brain/brain.py:1485`) is called on every section advance
(`bot/brain/brain.py:1398`), so it is not dead code, but it measures time left
against `hard_stop_seconds`, which is the configured duration **plus** the grace
period, while the plan is built to the duration alone. The grace is therefore
counted as available and spent silently before the mechanism engages. Checked
against every stored interview: 104 sections across 18 interviews, zero with
`coverage_shortfall` set, and `original_budget_seconds` is `None` everywhere. It
has never fired. This is S6 in the audit and is deliberately left, because
firing it at the configured duration would shrink later sections on nearly every
interview to prevent an overrun that has so far cost nothing.

What *does* set `coverage_shortfall` is a section the candidate declined, one
that ran out of time, or one the interview never reached. The scorer used to
label all of those `SQUEEZED:`, which was true of the one writer that never runs
and false of the four that do. It now says `SHORTFALL:`.

**Two fields on `SectionOutcome` are written, persisted, and read by nothing.**
`unexplained_terms` and `depth_rationale` are S3 and S10, and both were left
dead on purpose: measured against real runs, they record the wrong thing more
often than the right one, and putting them in front of an employer would be
worse than the gap. Do not wire them up without re-reading those findings.

**Two features are switched off by default and their code is fully present.**
Recording is off because Daily bills for every recorded minute and a recording
that starts by accident costs money until somebody notices. The spoken
acknowledgement is off because it was built for a twelve-second gap that turned
out to be an artefact of reading transcript timestamps. Both are in
`bot/config.py` with the reasoning attached. `TERM_PROBING_DISABLED` is inverted
against those two, deliberately: absent means on, because term probing has
shipped in every measurement ever taken here.

**Open items live in the audits, not here.** [AUDIT.md](../AUDIT.md) holds S1 to
S14 on the system end to end, and [AUDIT_FOLLOW_UP.md](../AUDIT_FOLLOW_UP.md)
holds F1 to F11 on the follow-up question machinery. Most are fixed and the
changelog says which. Still open at the time of writing: the squeeze measuring
against the wrong wall (S6, above), `OPENAI_BLUEPRINT_MODEL` driving four things
including the live judge (S7), cost telemetry not existing at all despite being
a Milestone 3 deliverable (S8), sections overrunning by one boundary turn (S12),
the partial arbitration between the four blocks that can claim a turn (F6), and
the judge being asked the wrong question at section end (F8).

One more, recorded in the changelog rather than an audit: fixing the term
counter made `MAX_TERM_PROBES_PER_INTERVIEW` bind for the first time, and
mechanism questions fell from 18 percent to 12 percent as a result. The cap was
chosen against a counter that was undercounting by about a third, so it now buys
fewer real probes than it did when the number was set. Raising it is an open
decision.

---

## Where to look when something is wrong

**Name the layer before touching anything.** Wrong words in the transcript are a
transcription problem; being cut off mid-thought is an endpointing one. They feel
identical and live in different files, and tuning one to fix the other is the
most expensive mistake available here.

The symptom table lives in [OPERATIONS.md](OPERATIONS.md), along with the
four-step checklist for a call that will not hang up. That chain has broken in
four separate places and each break was invisible to tests that exercised one
link, so check it in the order given there rather than guessing.
