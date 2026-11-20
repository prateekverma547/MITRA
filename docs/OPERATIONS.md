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

This is why `desired_count` is capped at 1 in `infra/envs/staging/variables.tf`. More replicas means a rebalance
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

## Logs

Both processes write to stdout, which the container runtime collects (CloudWatch
Logs on AWS). Set `LOG_FORMAT=json` in deployment. Each line is then one object:

| field | meaning |
|---|---|
| `ts`, `level`, `logger`, `msg` | when, how severe, where, what |
| `process` | `web` (the API) or `bot` (one interview) |
| `interview_id` | on every bot line, and on any web line about an interview |
| `request_id` | on every web request. The same ID is returned in the `X-Request-ID` header, so a support ticket can quote it |
| `method`, `path`, `status`, `duration_ms` | on every request line |
| `exc` | the traceback, on any error that has one |

**What is never logged:** cookies, passwords, meeting passwords, API keys, bot and
meeting tokens, request bodies, query strings, and the transcript. The transcript is
written to the database and the session file. The log only records how many turns were
saved. Secret-shaped strings are masked as a second line of defence (`shared/logging_setup.py`).

**The health check is logged at DEBUG**, so it does not drown the rest. Set
`LOG_LEVEL=DEBUG` to see it, and Pipecat's frame chatter, when diagnosing.

### Finding things

In CloudWatch Logs Insights, against the task's log group:

```
# Everything that happened to one interview, web and bot together
fields @timestamp, process, msg
| filter interview_id = "int_abc123" or msg like /int_abc123/
| sort @timestamp asc

# Every error, with its traceback
fields @timestamp, process, msg, exc
| filter level = "ERROR"
| sort @timestamp desc

# One request, from the header a candidate or admin reports
fields @timestamp, method, path, status, duration_ms
| filter request_id = "req-id-from-the-report"

# Slow requests
fields @timestamp, path, status, duration_ms
| filter duration_ms > 2000
| sort duration_ms desc
```

Keep the log group's retention modest. The lines carry candidate and interview IDs,
and error text can quote the data that caused it. CloudWatch keeps them until retention
expires, so treat the log group as personal data.

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

Each interview's usage is recorded as counts, in `session_metrics["usage"]`: live model
tokens, judgement model tokens, feedback scoring tokens, text-to-speech characters, streamed
speech-to-text seconds, and call seconds. The counts are taken from the pipeline's usage
events and the judge's and scorer's own totals, not estimated from the transcript.

Read them with:

```bash
cd backend
DATABASE_URL=... python scripts/interview_cost.py              # latest 50
DATABASE_URL=... python scripts/interview_cost.py --interview int_abc123
```

**Prices are not stored.** They come from environment variables, so a price change does
not rewrite history.

**LLM prices come from a public feed**, `https://www.llm-prices.com/current-v1.json`, which
lists each model's price per million tokens. The report looks up the model each role
actually used, and prices cached input tokens at the cached rate. The feed is cached for
a day in `backend/.cache/llm-prices.json`. If it cannot be fetched, the cached copy is used
and labelled with its date. With no copy at all, LLM lines are reported as unpriced, never
estimated. Every report states which copy it used and the feed's date.

| variable | applies to |
|---|---|
| `PRICE_LIVE_INPUT_PER_MTOK`, `PRICE_LIVE_OUTPUT_PER_MTOK` | optional override for the live model's rate |
| `PRICE_JUDGMENT_INPUT_PER_MTOK`, `PRICE_JUDGMENT_OUTPUT_PER_MTOK` | optional override for the judgement model's rate |
| `PRICE_FEEDBACK_INPUT_PER_MTOK`, `PRICE_FEEDBACK_OUTPUT_PER_MTOK` | optional override for the feedback model's rate |
| `PRICE_LLM_INPUT_PER_MTOK`, `PRICE_LLM_OUTPUT_PER_MTOK` | shared fallback for a role whose model is not in the feed |
| `PRICE_TTS_PER_KCHAR` | ElevenLabs characters (no public feed, so set from the price list) |
| `PRICE_STT_PER_MIN` | Deepgram streamed minutes |
| `PRICE_CALL_PER_MIN` | Daily participant minutes |

Precedence for an LLM role: its override, then the feed entry for its model, then the shared
rate. A model that is in neither is reported as `NOT PRICED`. Use `--no-network` to price from
the cache alone.

With no rates set, the script prints the units and says the interview is not priced. It
does not invent a figure.

**What the counts mean, and where they can mislead:**

- **Speech-to-text is billed on the open stream**, so `stt_stream_seconds` is the whole call,
  silences included. `candidate_speech_seconds` is recorded separately and is a diagnostic.
- **Judgement calls include failed attempts**, because a failed call was still billed.
- **Feedback scoring is counted only when a report is written.** A scoring attempt that
  failed is not counted, so its cost is missing from the record.
- **Blueprint generation and clarification are not in the per-interview record.** They happen
  once per candidate or role, not per interview, and are not yet counted.
- **Daily is billed per participant-minute.** `call_seconds` is the bot's time in the room
  and is an approximation of that, not Daily's invoice.

**Verified:** the observer's counting (including that a frame seen at several hops is counted
once), the judge and scorer totals, the record's round trip through JSON, and the price
arithmetic, by unit tests; and the reader against a seeded database. **Not verified:** a
live call. The usage events from the real providers have not been observed in a session yet,
so check one real interview's record against the provider dashboards before trusting a total.

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

---

## Database migrations

The schema changes only through Alembic revisions in `backend/alembic/versions/`.
The running app never alters a table. Its startup check refuses to run against a
database that is not exactly at the head revision, so a deploy cannot start with a
schema the code does not expect.

**Adding or changing a column, table or index:**

1. Edit the model in `backend/app/db.py`.
2. From `backend/`, generate the revision and read it before trusting it:
   `alembic revision --autogenerate -m "what changed"`
   Autogenerate does not see everything (it missed the partial index, which revision
   0002 writes by hand). Check the file by eye.
3. Apply it locally and confirm the models and the migrated database agree:
   `alembic upgrade head` then `alembic check` (it must print "No new upgrade operations
   detected"). `alembic check` also runs in the test suite (`test_migrations.py`), so a
   model change without a revision fails the tests.
4. Give every revision a working `downgrade()`, and try it once on a copy.

**Existing data.** A revision that can fail on live data (a new unique index, a NOT NULL
column without a default) must be tested against a copy of production first.

**Deploys.** The image runs `alembic upgrade head` before it starts uvicorn, so
deploying the code also applies its migrations. Replicas stay at one (CLAUDE.md), so
two migrations never race. If a migration fails, the container exits and the old
version keeps serving.

### Adopting an existing database

A database created before migrations existed has tables but no migration record. The
app refuses to start on it, which is deliberate: nothing can say what it contains.
Adopt it once, deliberately:

1. **Back it up first** (see Backups below). Do not skip this.
2. Record the baseline. This runs no SQL, it only writes the version record:
   `alembic stamp 315a320c1de2`
3. Apply the rest: `alembic upgrade head`. The only step after the baseline is
   `0002_one_live_interview`, which creates the one-live-interview index. It uses
   `IF NOT EXISTS`, so a database that already has the index is fine. If it fails with a
   UNIQUE error, two candidates have two live sessions. Cancel one of each pair in the
   panel, then run the upgrade again.
4. Confirm the schema matches the models: `alembic check` must print "No new upgrade
   operations detected". If it reports differences, the database's shape is not the
   baseline's. Stop, restore the backup, and find out why before going further. Do not
   edit the models to match the database.
5. `alembic current` should print `0002_one_live_interview (head)`.

This was done on the local development database (`backend/local.db`), which predates
migrations. Its check after the upgrade was clean.

---

## Backups

`backend/scripts/db_backup.py` writes a dump and checks it before it counts as a backup.
For Postgres it runs `pg_dump` in custom format, lists the dump with `pg_restore --list`,
and writes a SHA-256 sidecar. It keeps the newest `--keep` backups (default 14) and deletes
older ones only after the new one is verified.

```bash
cd backend
DATABASE_URL=... python scripts/db_backup.py --dir /secure/location --keep 14
```

**The machine that runs a backup needs `pg_dump` at the same major version as the
server or newer.** The app image does not include it (its base image has no
PostgreSQL client), so schedule backups from a job that does, such as a Railway cron
service or an AWS scheduled task with a matching `postgresql-client` package.

**Backups contain transcripts, CVs, and job descriptions.** They are personal data.
Store them only where people who may read those records can read them, encrypt them at
rest, and do not copy them to a laptop. `backend/backups/` is gitignored and
dockerignored, and that is the only protection the repository gives them.

**Managed backups are a second layer, not a substitute.** RDS and Railway's Postgres
offer point-in-time backups. Turn them on, and still run this script, because a
dump can be restored into a database you control and checked.

### Restore drill (run monthly and after any migration change)

A backup nobody has restored is a hope, not a backup.

1. Restore into a **new** database, never the live one:
   ```bash
   python scripts/db_restore.py --file backups/<dump> \
       --target-url postgresql://user:pass@host:5432/mitra_drill --confirm mitra_drill
   ```
   `--confirm` must equal the target database's name. The script refuses otherwise, and
   refuses a dump whose checksum has changed.
2. Point a copy of the app at the drill database and check: `alembic current` shows the
   expected revision, row counts match the live database at the time of the backup, and
   a recent interview's transcript opens in the panel.
3. Drop the drill database and record the date and result here.

**Drill log**

- **2026-11-06, PostgreSQL 17** (client and server both 17; the Debian trixie image has no
  16 package). Seeded one job, candidate and completed interview. `db_backup.py` wrote a
  custom-format dump with a SHA-256 sidecar. `db_restore.py` refused a wrong `--confirm`
  and a tampered dump, then restored into an empty `mitra_drill`. The restored database
  matched the original on row counts and on an interview-row digest, and sat at head
  `0003_recording_store`. `alembic check` reported no drift on Postgres. Migration
  `0004_admin_revocations` was applied, downgraded, and re-applied on the same database.
  Result: pass. Still to do: run the same drill on PostgreSQL 16 to match the version
  stated above, and the managed-backup and scheduled-job checks.

### What was verified, and what was not

- **Verified against PostgreSQL 16 (postgres:16-alpine):** migrations to head with
  `alembic check` clean; backup, checksum, restore into a second database; row counts,
  revision, and index preserved; refusal of a wrong `--confirm`, a tampered dump, and a
  missing `DATABASE_URL`; pruning.
- **Not verified here:** the scheduled job on Railway or AWS, the managed-backup
  settings, and a restore of real production data. Those need the drill above, on the
  real platform.

---

## Recording storage in S3

Finished recordings are uploaded to an S3 bucket instead of being kept on the container's
disk, which a deploy erases. The upload is checked by size before Daily's copy is deleted.
Playback is a short-lived link straight to S3, so the video does not pass through the app.

### Set up the bucket (AWS console or CLI)

1. **Create a private bucket** in the same region as the app, for example `us-east-1`.
   Keep **Block all public access** on, and do not enable website hosting or an ACL.
2. **Default encryption:** SSE-S3 (AES-256). It costs nothing extra.
3. **Versioning: off.** A versioned bucket keeps deleted recordings after they are
   deleted, which defeats the retention promise.
4. **Lifecycle rule** on the `recordings/` prefix: expire current objects after 11 days.
   The app deletes them after 10 days; this is the backstop if the sweep ever stops. Set it
   just above the retention period so the app's own deletion, with its checks, runs first.
5. **Access logging or CloudTrail data events** on the bucket, so reads of recordings are
   recorded. Recordings are personal data and the access should be auditable.

### Least-privilege IAM policy for the app

Attach this to the app's role (ECS task role) or user. It allows only the object operations
the app uses. It does not allow listing the bucket or changing its settings.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::YOUR-BUCKET/recordings/*"
    }
  ]
}
```

`HeadObject` (used to confirm an upload and check existence) is authorised by `s3:GetObject`.
Replace `YOUR-BUCKET`. Do not grant `s3:*` or `s3:ListBucket` to the app.

### Environment variables

| variable | value |
|---|---|
| `RECORDINGS_S3_BUCKET` | the bucket name. Setting it turns S3 storage on. |
| `RECORDINGS_S3_REGION` | the bucket's region, for example `us-east-1` |
| `RECORDINGS_S3_PREFIX` | optional, default `recordings/` |
| `RECORDINGS_S3_SSE` | optional, default `AES256`. Empty only for servers that reject the header. |
| `RECORDINGS_S3_ENDPOINT_URL` | leave unset for AWS. Only for S3-compatible test servers. |

If the bucket is not set on a deployed instance, the sweep refuses to download recordings
and logs the reason. Nothing is written to the container's disk, and Daily keeps the file
until the bucket is configured.

### What was verified, and what was not

- **Verified:** the storage code against a real S3 API (an S3-compatible test server, not
  AWS): a 20 MB multipart upload, the size check, existence, a presigned link returning
  200, a range request returning 206 so seeking works, deletion, and repeated deletion. The
  sweep, playback and deletion paths are covered by tests with a fake client, and a
  recording missing from S3 is reported rather than served.
- **Not verified here:** a real AWS bucket. Check the IAM policy, the encryption default,
  the lifecycle rule and the CSP media source against the real bucket with one test
  interview before relying on them.
- **The video policy** allows playback only from this bucket's own hostnames, derived from
  `RECORDINGS_S3_BUCKET` and its region. Nothing else is allowed. Confirm one playback
  works in the browser, because a wrong region in the variable blocks video silently.
