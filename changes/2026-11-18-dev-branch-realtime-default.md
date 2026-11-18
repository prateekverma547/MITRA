## 2026-11-18: `dev` begins: stg and the realtime engine together, realtime by default

**What**

A new integration branch, `dev`, combines the two lines of work that had split
apart: `stg` (the admin panel, interview pacing, report and candidate-page work)
and `realtime-poc` (the OpenAI Realtime voice engine). The realtime engine is now
the one that runs unless told otherwise. Candidates on it see captions of what
the interviewer says, as they did on stg.

**Why**

The realtime engine was judged far smoother live, and it is the base for the
live view and proctoring work. Both engineers need one starting point that has
everything, and `docs/DEV_PLAN.md` is the plan built on it.

**How**

- Merge of `origin/realtime-poc` into `origin/stg`: no conflicts.
- `bot/config.py`: `interview_engine()`, default `realtime`; `pipeline` is the
  fallback and the rollback. A mistyped value falls back rather than crashing.
- `bot/realtime_captions.py`: captions for the realtime engine. stg's
  `RTVIObserver` would have put every transcript fragment on its own line,
  because the realtime service labels each delta as a sentence.
- `bot/run_realtime.py`: captions, presence that knows the interview is over,
  and the recording switch, carried over from stg's pipeline engine.
- `scripts/dev_interview.py`: realtime by default; `--no-brain` now refuses to
  run unless `--engine pipeline` is given.
- `docs/DEV_PLAN.md`, CLAUDE.md "Current direction", this folder.

**Left undone**

- Not heard in a live call: no API keys were available when this was built.
- The realtime transcript is still OpenAI's own; Deepgram as the independent
  record is Track C phase 1.
- Section ids on realtime transcript turns, and the Director: phase 2.

**Confirm**

```bash
cd backend && uv run pytest -q                       # 782 passed
uv run python scripts/realtime_smoke.py               # needs OPENAI_API_KEY
uv run python scripts/dev_interview.py                # realtime voice session
```
