# Architecture

How MITRA is put together, and which part owns which problem.

Read [BRAIN.md](BRAIN.md) next if you are changing interview behaviour, and
[DECISIONS.md](DECISIONS.md) before changing anything that looks arbitrary. A
surprising amount of what looks arbitrary was measured.

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
| `simulated_employer.py` | scripted employers for tests and demos |

Runs on the reasoning-tier model. Latency is irrelevant here, and this is where
the product's intelligence is front-loaded: deep CV analysis, per-claim
verification targets, questions written against the CV's thin spots.

**Time budgets are computed in code, not by the model.** Models cannot be relied
on to make a set of numbers sum exactly. The model supplies a relative
`emphasis` per competency; minutes are derived and normalised in
`allocate_minutes`.

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
| `brain_director.py` | the Pipecat adapter: rewrites context, retargets the prompt |
| `turn_taking.py` | VAD and semantic endpointing: *when has the candidate finished* |
| `services/stt.py` | transcription. Vendor swap point |
| `services/tts.py` | voice |
| `services/llm.py` | the live conversation model |
| `silence.py` | the escalation ladder for dead air |
| `presence.py` | who is actually in the room |
| `ending.py` | hanging up when the interview is over |
| `tools.py` | the `end_interview` function the model can call |
| `observers.py` | latency and transcript instrumentation |
| `persistence.py` | writing the finished interview to the database |

**Observers versus processors matters and has bitten repeatedly.** A processor
only sees frames that pass through its position in the pipeline. An observer
sees every frame regardless of where it sits. Anything reacting to what the
*output* transport emits, the bot finishing speaking, for example, must be an
observer. `ending.py` exists as an observer for exactly this reason, and its
docstring records what happened when it was not.

### feedback/, scoring the transcript

Runs once, at the end, against the complete saved transcript. Never
incrementally, never between turns, never on the conversational path.

| file | job |
|---|---|
| `score.py` | the model call, plus quote verification |
| `health.py` | was the candidate audible? derived from the transcript |
| `run.py` | orchestration and persistence |

**Every quote is verified against the transcript in code before it reaches a
report.** A model asked for verbatim evidence will occasionally produce a
plausible sentence nobody said, and a fabricated quote inside a hiring record is
the worst failure this product has. A score whose evidence does not survive
verification is downgraded rather than published.

### app/, the API and the panel

| file | job |
|---|---|
| `main.py` | uploads, clarification, blueprint generation, serving the UI |
| `interviews.py` | create, join, run, persist an interview |
| `auth.py` | single-admin password and session cookie |
| `capacity.py` | how many bots may run at once |
| `db.py` | SQLAlchemy models and the additive migration |
| `meeting.py` | human-readable meeting IDs and passwords |

Async work uses FastAPI `BackgroundTasks` plus a status column. No queue, no
Celery, no Redis. **Work is scheduled the moment its input arrives, never the
moment its output is needed:** blueprint generation starts at CV upload, scoring
starts when the transcript is saved.

---

## How an interview flows

```
1.  POST /jobs                  JD uploaded, clarification chat begins
2.  POST /jobs/{id}/clarify     repeated until an EvaluationSpec is agreed
3.  POST /jobs/{id}/candidates  CV uploaded; blueprint generation starts here
4.  POST /candidates/{id}/interviews
                                mints a private Daily room, meeting ID, password
5.  POST /interviews/join       candidate's credentials -> short-lived token
                                AND the bot process is spawned, now, not earlier
6.  ... the interview happens ...
7.  bot saves transcript, then scores it, then exits
8.  GET  /interviews/{id}       the report
```

Step 5 is the only unauthenticated route. Everything else is behind the admin
password. A test sweeps the route table and fails on any new endpoint that is
neither guarded nor explicitly listed as public.

---

## The diagnostic map

**Name the layer before touching anything.** This single rule has saved more
time than any other in the project, because these symptoms feel similar and the
fixes are in completely different files.

| symptom | layer | file |
|---|---|---|
| wrong words in the transcript | STT | `bot/services/stt.py` |
| cuts the candidate off mid-thought | endpointing | `bot/turn_taking.py` |
| long pause before replying | STT finalisation, then endpointing | measure first |
| asks a poor question | brain or blueprint | `brain/prompting.py`, `blueprint/generate.py` |
| repeats itself, ignores an answer | brain state | `brain/brain.py` |
| does not hang up | the ending chain | `bot/ending.py`, and see below |
| report is unfair or thin | scorer, or conversation health | `feedback/` |
| panel shows stale data | browser cache, or the API | check `Cache-Control` |

Never tune endpointing to fix transcription, or the reverse. They are different
problems with the same feel.

**"Does not hang up" deserves its own note**, because that chain has broken in
four separate places: a check placed in a processor that could not see the
frame, an observer Pipecat could not hash, a brain that never marked itself
finished, and a brain that never heard the goodbye. Each was invisible to tests
that exercised one link. `tests/test_ending.py` now drives the whole chain
through a real pipeline.

---

## Measuring, not guessing

Every session writes a record to `backend/sessions/<id>.json` and to
`session_metrics` in the database:

```
ttfa_median_ms          end of candidate speech -> first bot audio
endpointing_median_ms   how long we took to decide they had finished
stt_lag_median_s        speech end -> transcript in hand
tolerated_pauses_s      pauses we sat through without interrupting
stt_provider            which vendor produced these numbers
brain_events            section transitions and judgements
silence_events          the escalation ladder firing
```

`ttfa` is the number that matters to a candidate. It decomposes into
endpointing plus generation, and knowing which half moved is the difference
between a fix and a guess.

---

## Testing

Two suites, never mixed. Mixing them produces a flaky suite nobody trusts,
which wastes the speed advantage text-mode testing exists to buy.

```bash
cd backend
uv run pytest tests/ -q                       # deterministic, ~470 tests, seconds
RUN_BEHAVIOR_TESTS=1 uv run pytest tests/test_behavior.py    # real model calls
```

**The tests are documentation.** Several encode specific live failures with the
actual sentences in them, and are worth reading before changing the thing they
cover:

| test | what it records |
|---|---|
| `test_withdrawal.py` | the verbatim transcript where withdrawal detection failed |
| `test_health.py` | why a heuristic that looked right flagged healthy sessions |
| `test_ending.py` | four separate ways the call failed to hang up |
| `test_repair.py` | the four sentences nothing detected |
| `test_judge_precision.py` | 12 labelled contradiction cases, precision 1.00 |
| `test_copy_style.py` | user-facing copy must not read as machine-written |

There are also scripts under `backend/scripts/` that hold real conversations
against the live model and print the transcript. They cost a little money and
are the only way to check whether behaviour *feels* right:

```bash
PYTHONPATH=. uv run python scripts/withdrawal_run.py
PYTHONPATH=. uv run python scripts/compare_models.py
PYTHONPATH=. uv run python scripts/unheard_candidate_run.py
```

**Green unit tests have hidden broken features here more than once.** If a
change affects how the bot behaves in an interview, run a real conversation
through it before believing the suite.
