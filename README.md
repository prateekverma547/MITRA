# MITRA

**Machine Interviewer for Talent Review & Assessment.**

A voice-based AI interviewer POC. Candidates are interviewed by *Mitra*;
a human reviews the evidence afterwards. Nothing here decides an outcome. See [CLAUDE.md](CLAUDE.md) for architecture
decisions, data contracts, and the milestone plan.

**Current state: Milestone 1 (voice hello-world).**

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

> Python 3.12 specifically, not 3.13 — Pipecat 1.6.0 still imports the stdlib
> `audioop` module, which was removed in 3.13.

```bash
brew install uv
cd backend && uv sync
```

Then fill in credentials:

```bash
cp .env.example .env   # at the repo root, then add your keys
```

Milestone 1 needs `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`,
and `DAILY_API_KEY`. `.env` is gitignored and must never be committed.

## Run a voice session

```bash
cd backend
uv run python scripts/dev_interview.py
```

This creates a Daily room over the REST API, spawns the bot process, and prints
a join URL. Open it in a browser, allow the mic, and talk. Ctrl-C ends the
session and writes the transcript plus latency summary to
`backend/sessions/<session-id>.json`.

## Tests

```bash
cd backend && uv run pytest
```

The turn-latency and transcript logic is tested with synthetic frames — no
audio, no network, no provider keys. Voice *quality* (naturalness, turn-taking
feel) is still a by-ear judgement; the tests only guarantee the measurements are
computed correctly.

## Layout

```
frontend/
├── candidate/          # join + consent + in-call UI
└── admin/              # admin panel

backend/
├── bot/
│   ├── turn_taking.py    # WHEN we decide the candidate finished speaking
│   ├── observers.py      # TTFA / pause-tolerance metrics + transcript capture
│   ├── persona.py        # Milestone 1 interviewer prompt (brain replaces this in M3)
│   ├── run_bot.py        # pipeline assembly + process entrypoint
│   ├── config.py         # env loading
│   └── services/         # one file per vendor — the swap points
│       ├── stt.py        # OpenAI realtime STT
│       ├── llm.py        # OpenAI chat
│       ├── tts.py        # ElevenLabs Flash
│       └── daily.py      # Daily rooms/tokens + WebRTC transport
├── scripts/dev_interview.py
└── tests/
```

## Reading the session metrics

Each session writes a `latency_summary`:

| Field | Meaning |
| --- | --- |
| `ttfa_median_ms` | **The DoD metric.** True end of candidate speech → first bot audio. Target median < 1500ms. |
| `endpointing_median_ms` | Share of that spent *deciding* the candidate had finished. |
| `longest_tolerated_pause_s` | Longest mid-answer silence the bot sat through without interrupting. Must comfortably exceed 3s. |
| `interruptions_by_candidate` | Turns where the candidate talked over the bot — barge-in is working. |

TTFA is deliberately measured from when the candidate *actually* stopped making
sound, not from when our turn detector noticed. Those differ by hundreds of
milliseconds, and the gap is precisely the thing worth tuning.

## Diagnosing voice problems

Name the layer before changing anything:

- **Bot cuts people off, or waits too long** → endpointing. Tune
  `bot/turn_taking.py`. Do not touch the STT vendor.
- **Wrong words in the transcript** → STT. That is a vendor concern in
  `bot/services/stt.py`. Do not touch turn-taking.

Tuning priority is fixed: **(1) doesn't interrupt, (2) feels fast.** Never trade
the first for the second.
