# Operations

Running MITRA once it is deployed. [DEPLOY.md](../DEPLOY.md) covers first-time
setup; this covers everything after.

---

## The one rule

**Never deploy while an interview is running.**

A bot is a child process of one specific container. A redeploy kills every
in-flight bot, mid-sentence, and the candidate is left in an empty room. There
is no graceful drain.

Check before shipping:

```bash
# in the admin panel: any candidate showing "in progress"
# or in the logs:
grep "bot registered" | tail
```

This is why `railway.json` pins replicas at 1. More replicas means a rebalance
can orphan a live interview in ways that are hard to observe.

---

## Environment variables

| variable | required | notes |
|---|---|---|
| `OPENAI_API_KEY` | yes | brain, blueprint, feedback |
| `ELEVENLABS_API_KEY` | yes | voice |
| `ELEVENLABS_VOICE_ID` | yes | verified at bot startup, before joining |
| `DAILY_API_KEY` | yes | rooms and tokens |
| `DATABASE_URL` | yes in deployment | refuses to start without it |
| `ADMIN_PASSWORD` | yes | no default, deliberately |
| `DEEPGRAM_API_KEY` | optional | present → Deepgram STT, absent → OpenAI |
| `MAX_CONCURRENT_INTERVIEWS` | optional | default 6 |
| `DEFAULT_TIMEZONE` | optional | greeting fallback, default `Asia/Kolkata` |
| `OPENAI_LLM_MODEL` | optional | live conversation, default `gpt-4.1-mini` |
| `OPENAI_BLUEPRINT_MODEL` | optional | default `gpt-4.1` |
| `OPENAI_FEEDBACK_MODEL` | optional | default `gpt-4.1` |

Two of these are worth knowing about specifically.

**`DATABASE_URL` missing is a hard failure, on purpose.** Without the guard the
app would fall back to local SQLite and write every interview to a container
filesystem the next deploy erases: green health checks, working interviews,
silent total data loss.

**`DEEPGRAM_API_KEY` is the entire STT switch.** Deleting the variable reverts
to OpenAI on the next bot spawn, with no deploy and no code change. That is the
fastest rollback in the system.

---

## Capacity

`MAX_CONCURRENT_INTERVIEWS` defaults to 6. When it is reached, a joining
candidate gets:

> All interview slots are busy right now. Your link is still valid, please try
> again in a few minutes.

Refused **before** minting a Daily token or recording consent, because taking
someone's consent and then failing to seat them is worse than turning them away.
A candidate reconnecting to their own running bot is never refused.

The cap is deliberately conservative. Memory allows roughly 19 on the 8GB
replica; the limit is 6 because per-bot CPU has never been measured and VAD plus
turn detection run continuously. **Raise it from the Metrics tab during
concurrent interviews, not from the memory arithmetic.** Raising it past what the
replica carries converts a clean refusal into an OOM restart that kills every
in-flight interview.

---

## Reading a session

Every interview writes to `backend/sessions/<id>.json` and to `session_metrics`
in the database. The container filesystem is ephemeral, so **the database copy
is the real one**.

```
===== LATENCY =====
ttfa_median_ms          end of candidate speech -> first bot audio
endpointing_median_ms   how long we took to decide they had finished
stt_lag_median_s        speech end -> transcript in hand
tolerated_pauses_s      pauses sat through without interrupting
stt_provider            which vendor produced these numbers
```

What good looks like, on Deepgram:

```
ttfa_median_ms      ~1700     endpointing_median_ms   ~380
stt_lag_median_s    ~0.27     tolerated_pauses_s      several, all short
```

An occasional TTFA above 5000ms is **usually correct behaviour**, not a fault.
Check `tolerated_pauses_s` for that turn: several tolerated pauses means the
candidate was speaking haltingly and the system waited rather than cutting them
off. That is the trade working.

---

## Diagnosing

**Name the layer before touching anything.** These symptoms feel alike and live
in different files.

| symptom | look at | not |
|---|---|---|
| wrong words in the transcript | STT vendor | turn-taking |
| cut off mid-sentence | `turn_taking.py`, then the STT finalisation wait | the prompt |
| long silence before replying | `stt_lag` first, then `endpointing_median_ms` | the model |
| first word of the greeting missing | `GREETING_SETTLE_SECONDS` | anything else |
| asks a strange question | `brain/prompting.py` or the blueprint | the model tier |
| repeats itself | brain state, `brain/brain.py` | the prompt |
| will not hang up | `bot/ending.py` and the brain's `is_finished` | the transport |
| panel shows old data | hard-reload once; `Cache-Control` is `no-cache` | the API |
| report reads unfairly | `ConversationHealth` first, then the scorer | |

### When a call will not end

That chain has broken in four separate places, so check it in order:

1. Does the brain report `is_finished`? It only does once the interviewer has
   **delivered** a closing turn, not when the candidate next speaks.
2. Did the interviewer actually speak the goodbye? A tool handler returning a
   falsy result produces no follow-up completion, so nothing is said.
3. Is `SessionEnder` registered as an **observer**, not a pipeline processor?
4. Is it hashable? Pipecat keeps observers in a set, and a plain `@dataclass`
   makes the class unhashable and registration dies.

There is a backstop: if the interview is over and the room goes quiet, the
silence ladder ends the call. Worst case is roughly fifteen seconds of quiet
rather than a hung call. If you see `ended_interview_over` in the silence
events, the backstop fired and the primary path is broken.

---

## Data and privacy

The database holds job descriptions, CVs, full interview transcripts, and
reports about named people. Treat it accordingly.

- Every admin route is behind `ADMIN_PASSWORD`. A test sweeps the route table
  and fails on any new endpoint that is neither guarded nor explicitly public.
- The only unauthenticated route is the candidate's own join, whose credential
  is the meeting ID and password.
- Rooms are private. Candidates never see a Daily URL, and a leaked room link is
  not enough to walk into someone's interview.
- Consent is explicit, blocking, and timestamped against the interview record.
  No bot is ever started without it.
- `cv/`, `jds/`, `uploads/`, `*.db` and `backend/sessions/` are gitignored
  because they contain personal data. Keep it that way.
- Passwords are stored as written rather than hashed, deliberately and narrowly:
  the employer has to read them back to send to a candidate. They are never
  logged.

Deleting a profile cascades to its CVs, plans, interviews and transcripts. There
is no undo, which is why the panel asks first.

---

## Costs

Rough per forty-minute interview, at current list prices:

| | |
|---|---|
| STT (Deepgram Nova-3) | ~$0.19 |
| STT (OpenAI, if reverted) | ~$0.68 |
| LLM, TTS, Daily | not measured |

**Cost telemetry is not built.** It is an outstanding Milestone 3 item, and
until it exists every figure here is arithmetic rather than observation. If cost
per interview matters to a decision, build that first.

---

## Routine checks

**After any deploy**, confirm the guards still hold:

```bash
U=https://<your-app>.up.railway.app
curl -s $U/health                                   # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}' $U/jobs      # 401
curl -s -o /dev/null -w '%{http_code}' $U/join      # 200
```

Note that `health` returning 200 proves the API booted. It proves **nothing**
about the bot, which runs as a separate process and is only exercised when
somebody joins. Changes to `bot/` are verified by a real call, not a health
check.

**After changing prompts**, re-measure rather than assume:

```bash
cd backend
PYTHONPATH=. uv run python scripts/compare_models.py
PYTHONPATH=. uv run python scripts/contradiction_rate.py
```

**Before trusting a heuristic**, run it over the real recordings in
`backend/sessions/`. A conversation-health heuristic that passed every unit test
flagged five of eight healthy sessions, and only real data showed it.
