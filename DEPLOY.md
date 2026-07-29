# Deploying to Railway

The backend and the bot processes ship as **one image**. FastAPI serves the
employer API and the candidate join page, and spawns `python -m bot.run_bot` as a
child process for each interview. Same dependencies, same container.

## What you need to do

These steps need your Railway account, so they are yours rather than mine.

### 1. Create the project

```bash
npm i -g @railway/cli     # if you don't have it
railway login
railway init              # from the repo root
```

Region: **US**, per CLAUDE.md.

### 2. Add Postgres

In the Railway dashboard: **New → Database → PostgreSQL**.

**Then link it to the app service.** Railway does *not* share a database's
`DATABASE_URL` with other services automatically — the Postgres service gets it,
the app service does not. On the **MITRA service → Variables**, add:

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

(the `Add Variable` link in Railway's "Trying to connect a database?" hint does
exactly this).

Miss this and the app refuses to start, deliberately. It would otherwise fall
back to local SQLite and write every interview to a container filesystem that
the next deploy erases — green health checks, working interviews, and silent
total data loss.

The code rewrites Railway's `postgresql://` scheme to `postgresql+asyncpg://`,
so nothing else is needed.

### 3. Set the environment variables

```bash
railway variables --set OPENAI_API_KEY=sk-...
railway variables --set ELEVENLABS_API_KEY=...
railway variables --set ELEVENLABS_VOICE_ID=oO7sLA3dWfQXsKeSAjpA
railway variables --set DAILY_API_KEY=...
```

Optional, defaults shown — see the model tiering policy in CLAUDE.md:

```bash
railway variables --set OPENAI_LLM_MODEL=gpt-4.1-mini
railway variables --set OPENAI_BLUEPRINT_MODEL=gpt-4.1
railway variables --set OPENAI_FEEDBACK_MODEL=gpt-4.1
```

### 4. Turn off anything that sleeps the service

**This is the setting that matters most**, and it is a settings check rather
than a soak test (CLAUDE.md, Milestone 6).

In **Settings → Deploy**, confirm there is no *App Sleeping*, *serverless*, or
scale-to-zero option enabled. A 40-minute interview is long stretches of a
process sitting quietly between spoken turns; anything that reaps idle
containers will kill a live interview mid-sentence.

Keep **replicas at 1**. A bot is a child of one specific container, so more
replicas means a redeploy or a rebalance can orphan a live interview for
reasons that are hard to see.

### 5. Deploy

```bash
railway up
```

Then check:

```bash
curl https://<your-app>.up.railway.app/health
```

## Running an interview on the deployed app

There is no employer UI yet, so bookings go through the API:

```bash
# Book an interview for a candidate whose blueprint is ready
curl -X POST https://<your-app>.up.railway.app/candidates/<cand_id>/interviews
```

That returns a meeting ID and password. The candidate then goes to:

```
https://<your-app>.up.railway.app/join
```

## What to watch on the first deployed interview

This deploy exists to answer questions we have been carrying since the first
day of the project and still have no evidence for:

1. **Does a bot subprocess survive a full 40-minute session?** The whole
   architecture assumes yes. Nothing has tested it.
2. **Does WebRTC hold up from another network?** Every session so far has been
   on one laptop.
3. **What does memory look like?** Each bot loads Silero VAD and the smart-turn
   ONNX model. Concurrent interviews mean concurrent copies.
4. **Is latency worse from Railway US** than from a laptop next to the same
   APIs? `ttfa_median_ms` in the session record answers this directly.

## Known constraints

- **No deploys during a live interview.** A redeploy kills every in-flight bot
  subprocess. Accepted for the POC; the mitigation is operational (CLAUDE.md).
- **Session JSON files are ephemeral.** The database is the record of truth;
  the files under `backend/sessions/` vanish with the container.
- **Image size ~2.5GB.** Down from what it would have been: the
  `local-smart-turn` extra was dropped because it pulls torch, torchaudio,
  transformers and coremltools — around 570MB, none of it loaded at runtime,
  since smart-turn v3 runs through onnxruntime.
