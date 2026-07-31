# MITRA

**Machine Interviewer for Talent Review & Assessment.**

A voice-based AI interviewer. An employer uploads a job description and settles
the evaluation criteria in a conversation with an AI; they upload a CV, and a
candidate-specific interview plan is generated. The candidate joins a branded
page with a meeting ID and password, and a bot called *Mitra* conducts a
~40-minute voice interview. Afterwards the transcript is scored into a report of
evidence.

**A human reads that report and decides. Nothing here decides anything about
anyone.**

---

## Where to go

| you want to | read |
|---|---|
| run it locally, right now | this file |
| understand how it fits together | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| change interview behaviour | [docs/BRAIN.md](docs/BRAIN.md) |
| know why something is the way it is | [docs/DECISIONS.md](docs/DECISIONS.md) |
| run or debug the deployed system | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| deploy it the first time | [DEPLOY.md](DEPLOY.md) |

**Read [docs/DECISIONS.md](docs/DECISIONS.md) before changing anything that
looks arbitrary.** Most of it was measured, and the numbers are in there.

---

## State

Working end to end and deployed: the employer panel, the clarification chat,
blueprint generation, the live voice interview, and the feedback report.

Known open items are listed at the bottom of
[docs/DECISIONS.md](docs/DECISIONS.md). The largest is **cost telemetry**, which
is not built, so every per-interview cost figure in this repo is arithmetic
rather than observation.

---

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

> Python 3.12 specifically, not 3.13, Pipecat 1.6.0 still imports the stdlib
> `audioop` module, which 3.13 removed.

```bash
brew install uv
cd backend && uv sync
```

Then credentials, at the repo root:

```bash
cp .env.example .env
```

Minimum to run a voice session: `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`,
`ELEVENLABS_VOICE_ID`, `DAILY_API_KEY`. Add `ADMIN_PASSWORD` for the panel and
`DEEPGRAM_API_KEY` for the faster transcription path.

**`.env` is gitignored and must never be committed.**

---

## Run it

### The whole product

```bash
cd backend
uv run uvicorn app.main:app --port 8000 --reload
```

Then open <http://localhost:8000/admin> and sign in with `ADMIN_PASSWORD`.
Upload a JD, answer the clarifying questions, upload a CV, wait for the plan,
and press **Start interview**. That gives you a link, a meeting ID and a password. Open the link in
another tab, or on your phone.

Without `DATABASE_URL` it falls back to local SQLite, so no Postgres is needed
to try things out.

### Just a voice session

Skips the panel entirely and interviews against a static fixture:

```bash
cd backend
uv run python scripts/dev_interview.py
```

Creates a Daily room, spawns the bot, prints a join URL. Ctrl-C ends it and
writes the transcript and latency summary to `backend/sessions/`.

---

## Tests

```bash
cd backend
uv run pytest tests/ -q                                     # ~470, seconds, no keys needed
RUN_BEHAVIOR_TESTS=1 uv run pytest tests/test_behavior.py    # real model calls, costs money
```

Two suites, never mixed: deterministic logic against a fake LLM, and tolerant
behaviour tests against a real one.

**The tests are documentation.** Several encode specific live failures with the
actual sentences in them, `test_withdrawal.py`, `test_health.py`,
`test_ending.py`, `test_repair.py`. Read the one covering whatever you are about
to change.

There are also scripts that hold real conversations and print the transcript:

```bash
cd backend
PYTHONPATH=. uv run python scripts/withdrawal_run.py        # a candidate asking to stop
PYTHONPATH=. uv run python scripts/unheard_candidate_run.py  # a candidate who cannot be heard
PYTHONPATH=. uv run python scripts/compare_models.py         # mini vs 4.1, side by side
```

> **Green unit tests have hidden broken features here more than once.** If a
> change affects how the bot behaves in an interview, run a real conversation
> through it before believing the suite.

---

## Layout

```
frontend/
├── candidate/      join, consent, in-call UI, thank-you
├── admin/          the employer panel
└── assets/         brand artwork

backend/
├── app/            FastAPI: uploads, auth, spawning bots, serving the UI
├── blueprint/      JD + CV -> what this interview should test
├── bot/            the live voice interview
│   ├── brain/      the state machine and context planner
│   └── services/   one file per vendor, the swap points
├── feedback/       transcript -> a report of evidence
├── shared/         Pydantic contracts everything else imports
├── scripts/        real-conversation harnesses and measurement tools
└── tests/

docs/               architecture, brain, decisions, operations
CLAUDE.md           instructions for AI agents working in this repo
```

---

## Two rules worth knowing on day one

**Name the layer before changing anything.** Wrong words in the transcript are
an STT problem. Cutting someone off is an endpointing problem. They feel
similar, live in different files, and tuning one to fix the other wastes days.

**Tuning priority is fixed: (1) doesn't interrupt, (2) feels fast.** Never trade
the first for the second. Cutting off a candidate mid-thought is worse than a
candidate waiting.
