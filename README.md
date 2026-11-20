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
| know what one file is for | [docs/MODULES.md](docs/MODULES.md) |
| change interview behaviour | [docs/BRAIN.md](docs/BRAIN.md) |
| know why something is the way it is | [docs/DECISIONS.md](docs/DECISIONS.md) |
| know what changed and what it measured | [CHANGELOG.md](CHANGELOG.md) |
| run or debug the deployed system | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| deploy it the first time | [DEPLOY.md](DEPLOY.md) |
| see what is known to be wrong | [AUDIT.md](AUDIT.md), [AUDIT_FOLLOW_UP.md](AUDIT_FOLLOW_UP.md) |

**Read [docs/DECISIONS.md](docs/DECISIONS.md) before changing anything that
looks arbitrary.** Most of it was measured, and the numbers are in there.

---

## State

Working end to end and deployed: the employer panel, the clarification chat,
blueprint generation, the live voice interview, and the feedback report.

**Deployment target: AWS, Mumbai (`ap-south-1`), as one image on one ECS task.**
FastAPI serves the API and both UIs, and spawns a bot process per interview as a
child of that same container. The replica cap and the `/health` check live in the
Terraform under `infra/envs/staging/`; [DEPLOY.md](DEPLOY.md) has the setup steps
and their status.

**This branch auto-deploys on push, and a deploy kills every live bot.** The
bots are children of the web process, so shipping mid-interview ends it. Check
for active sessions before pushing. That is the one operational rule worth
knowing before you have read anything else.

Known open items are listed at the bottom of
[docs/DECISIONS.md](docs/DECISIONS.md), and the two audits hold the detailed
findings. The largest is **cost telemetry**, which is not built, so every
per-interview cost figure in this repo is arithmetic rather than observation.

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
Create a profile, giving it a job description: start from one of the built-in
role templates, write your own, or upload a file. Answer the clarifying
questions, upload a CV, wait for the plan, and press **Start interview**. That
gives you a link, a meeting ID and a password. Open the link in another tab, or
on your phone.

A template only fills the JD box. The clarification chat still runs and is still
what decides what the interview tests.

For a local run without Postgres, set `APP_ENV=development` and leave
`DATABASE_URL` empty. The app then uses a local SQLite file, so no Postgres is needed
to try things out. Without `APP_ENV` set, the app refuses to start rather than guess,
because a guess would lose every transcript on a deployed instance.

### With Postgres, as deployed

```bash
cp .env.example .env          # set POSTGRES_PASSWORD (letters and digits) and the API keys
docker compose up --build
```

This runs the app and a Postgres 17 database, the same engine Railway uses. The
app container migrates the database to head before it serves. Data lives in the
`pgdata` volume and survives `docker compose down`. `docker compose down -v`
deletes it.

Open <http://localhost:8000/admin>. To reach the database with a client, connect
to `localhost:5432` (or `POSTGRES_HOST_PORT`) as user `mitra`, database `mitra`.

To browse the database in a web UI, open <http://localhost:5050> (pgAdmin). Sign in with
`PGADMIN_EMAIL` and `PGADMIN_PASSWORD` from `.env`, then enter `POSTGRES_PASSWORD` once for
the "MITRA (local)" server. Both ports are published to loopback only.

The app's `stop_grace_period` is two minutes so that a stop does not kill a bot
mid-interview. Use `docker compose stop` and wait, not `kill`.

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
uv run pytest -q                                            # 754, seconds, no keys needed
RUN_BEHAVIOR_TESTS=1 uv run pytest tests/test_behavior.py    # real model calls, costs money
```

Two suites, never mixed: deterministic logic against a fake LLM, and tolerant
behaviour tests against a real one. The default run deselects the behaviour
suite, which is what the "22 deselected" line means.

There is **no JavaScript harness**, so both frontend surfaces ship unverified
apart from a copy-style check over their text.

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
│   ├── services/   one file per vendor, the swap points
│   └── fixtures/   static blueprints, for running without a database
├── feedback/       transcript -> a report of evidence
├── shared/         Pydantic contracts everything else imports
├── jd_templates/   starting-point job descriptions, one Markdown file per role
├── scripts/        real-conversation harnesses and measurement tools
└── tests/

docs/               architecture, modules, brain, decisions, operations
CHANGELOG.md        every change, with what it measured
AUDIT.md            end-to-end audit findings
AUDIT_FOLLOW_UP.md  the same for the follow-up question machinery
CLAUDE.md           instructions for AI agents working in this repo
```

Every module in that tree is explained in [docs/MODULES.md](docs/MODULES.md).

---

## Two rules worth knowing on day one

**Name the layer before changing anything.** Wrong words in the transcript are
an STT problem. Cutting someone off is an endpointing problem. They feel
similar, live in different files, and tuning one to fix the other wastes days.
The symptom table is in [docs/OPERATIONS.md](docs/OPERATIONS.md).

**Tuning priority is fixed: (1) doesn't interrupt, (2) feels fast.** Never trade
the first for the second. Cutting off a candidate mid-thought is worse than a
candidate waiting.
