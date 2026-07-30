# MITRA — Project Context

**MITRA** — Machine Interviewer for Talent Review & Assessment. The bot
introduces itself to candidates as "Mitra". The name lives in
`shared/branding.py` and is read from there by the spoken introduction, the
Daily participant list, and the candidate join page — never hardcoded in
more than one place.

## What this project is

A voice-based AI interviewer POC. An employer uploads a Job Description, answers AI clarifying questions to define what the interview should evaluate, then uploads a candidate's CV. The backend generates a candidate-specific interview blueprint. The candidate joins a branded web interview page using a meeting ID + password, and an AI bot conducts a ~40-minute voice interview. Afterwards, the panel shows a structured feedback report scored against the employer's evaluation spec.

This is a POC. Prioritize a working end-to-end flow over polish, but keep the data contracts clean — they are the long-term IP.

## Architecture decisions (already made — do not revisit without asking)

- **Voice framework:** Pipecat (self-hosted), NOT Vapi and NOT OpenAI Realtime speech-to-speech. Pipeline architecture: STT → LLM → TTS. Rationale: model control, transcript as auditable ground truth, cost at 40-min sessions.
- **STT:** OpenAI realtime/streaming transcription (existing API key). Build against OpenAI only — no Deepgram integration, no dual code paths, no runtime provider switch. Deepgram is a documented fallback and nothing more. Keep the STT service isolated behind its single adapter file so a future swap, if it ever happens, is a one-file change.
  - **STT vendor decision rule (data-driven, not preference-driven).** The `stt_lag` instrumentation in `bot/observers.py` measures, per segment, the time from the true end of candidate speech to the arrival of the transcript. Decide from the measured median:
    - **~300–500ms** → keep OpenAI. Set the finalization wait (`ttfs_p99_latency` in `bot/services/stt.py`) from the measurement plus margin.
    - **consistently ~1s or more** → OpenAI's finalization is structurally slow for conversational turn-taking. Run a Deepgram trial through the existing STT adapter (single-file change) and compare the same metrics on an equivalent session.
  - **Measurement outcome (session `dev-42b6ab2a`, 12 samples): median 1.04s, p90 1.08s, max 1.08s, min 0.83s.** A tight distribution, not a noisy average — OpenAI's finalization is a structural ~1s floor. **This lands in the "~1s or more" bucket, so the rule fires: run the Deepgram trial.** Interim action taken meanwhile: `ttfs_p99_latency` lowered from Pipecat's generic 1.66s default to a measured 1.25s, cutting ~0.4s from every turn. Blocked on obtaining a `DEEPGRAM_API_KEY`.
  - Consequence worth stating plainly: with OpenAI STT the endpointing floor is ~1.05s, and observed generation (LLM + TTS) is ~1.3s. Best achievable median TTFA is therefore ~2.4s — **the < 1.5s DoD target is unreachable on OpenAI STT regardless of tuning.**
  - Context: Pipecat's turn-stop strategy waits for a finalized transcript before ending a turn. OpenAI's Realtime STT never sets `finalized=True`, so every turn falls back to a fixed safety-net timeout (`OPENAI_REALTIME_TTFS_P99` = 1.66s, minus VAD `stop_secs`). That constant is a generic default, not a measurement of our deployment — hence the rule above.
- **Turn-taking:** Smart-turn / semantic endpointing enabled alongside VAD. This is one listening configuration in Pipecat, not a second implementation — do not build a parallel turn-detection path.
- **LLM (interview brain):** OpenAI text model via Pipecat's OpenAI LLM service. Must remain swappable to Anthropic in one place.
- **Model tiering is explicit policy.** Three model roles, all env-configurable, never collapsed into one:
  - `OPENAI_LLM_MODEL` — **live conversation.** Default `gpt-4.1-mini`. Rationale: the sectioned-brain design front-loads intelligence into the blueprint and keeps live context small, so a mini-tier model should be viable — we are betting on our own architecture.
    - **Guardrail:** once the sectioned brain passes its suites on mini, run the scripted-candidate behaviour tests (shallow-answer probe, cross-section contradiction, off-topic redirect, instruction adherence over a long scripted session) against **both** `gpt-4.1-mini` and `gpt-4.1` through the text harness, and report both transcripts side by side. If mini complies lazily — accepts hollow answers, drifts, softens redirects — the default shifts to `gpt-4.1`, no debate. **Quality of probing outranks speed and cost.**
  - `OPENAI_BLUEPRINT_MODEL` — **blueprint generation (M2).** Reasoning-tier; latency irrelevant. This is the product's IP: deep CV analysis, per-claim verification targets, questions designed against the CV's thin spots — not merely reformatting the JD into sections.
  - `OPENAI_FEEDBACK_MODEL` — **feedback scoring (M4).** Reasoning-tier, off-path.
  - **Never put a reasoning model on the live conversation path.**
- **TTS:** ElevenLabs, low-latency Flash model family, single configured voice ID from env.
- **Transport:** Daily (WebRTC rooms). The bot joins a Daily room as a participant from the server. Candidates connect via Daily's client SDK embedded in our own frontend. Milestone 1 may use Daily's prebuilt room UI for testing.
- **No Google Meet / Zoom integration in the POC.** Own branded join page with meeting ID + password. (Zoom Meeting SDK is a possible post-POC integration; never build Meet browser-bot hacks.)
- **Hosting:** Railway, US region. One repo, deployed as: backend service (FastAPI + spawned bot processes), Postgres add-on. Frontend served by FastAPI initially; may split into its own Railway service later.
- **Bot lifecycle:** one bot process per interview, spawned by the FastAPI backend, lives for the session, exits after saving the transcript. Bots die on redeploy — accepted for the POC; the mitigation is operational (see Conventions), not architectural.
- **Async jobs:** FastAPI `BackgroundTasks` plus a status column on the owning record. No Celery, no Redis, no queue add-on. Work is scheduled at the moment its input arrives, never at the moment its output is needed.

## Repo structure

```
interviewer/
├── backend/
│   ├── app/            # FastAPI: routes, auth (meeting ID+password), uploads,
│   │                   # clarification chat, bot spawning, feedback jobs
│   ├── blueprint/      # JD+CV parsing and interview blueprint generation
│   ├── bot/            # Pipecat pipeline + interview brain
│   ├── feedback/       # post-interview scoring: runs once, at the end, from
│   │                   # the complete transcript. Never on the live path.
│   ├── shared/         # Pydantic contracts (see Data contracts)
│   └── requirements.txt
├── frontend/
│   ├── candidate/      # join page, consent gate, in-call interview UI
│   └── admin/          # admin panel: JD, clarification, CV, blueprint, interviews
│                       # Plain HTML/JS for the POC; React + Vite when it settles.
│                       # Served by FastAPI; the Docker image mirrors this layout.
├── CLAUDE.md
└── railway config files
```

Rules:
- `shared/` is the single source of truth for data shapes. `app/`, `blueprint/`, `bot/` all import from it. Never duplicate schema definitions.
- **The interview brain is a context planner and state machine, NOT a text generator.** This is the precise meaning of "text in / text out" — it must never be re-read as brain-owns-generation.
  - `plan_turn()` assembles what the model sees this turn: section persona, carried claims, in-section verbatim turns.
  - `observe()` updates coverage, extracts claims, and decides section transitions.
  - **Generation stays in the streaming Pipecat pipeline.** If the brain returned finished text, the first token could not reach TTS until the whole response was generated, which roughly doubles time-to-first-audio. The brain shapes *context*, never intercepts the *response*.
  - The brain is a pure synchronous state machine: it never awaits, never blocks a spoken turn. Off-path LLM work is requested by the brain and fulfilled by its driver (`pending_judgment_request()` / `apply_judgment()`).
  - `bot/brain/` never imports Pipecat. The text-mode harness wraps `plan_turn` with a single non-streaming LLM call — same object, different driver.
- Keep each external provider behind one small adapter/service class so any vendor can be swapped in one file.

## Data contracts (define in `backend/shared/` in Milestone 2)

- **EvaluationSpec** — produced by the JD upload + employer clarification chat. Competencies with weights, seniority/depth expectations, red flags, language/tone preferences, plus:
  - `duration_minutes` — **employer-configured, required, set via the admin panel.** POC range 20–90, default 40. No code may treat 40 as anything but a default.
  - `overrun_grace_minutes` — optional, default 5, settable to 0.
- **InterviewBlueprint** — produced from CV + EvaluationSpec. Candidate summary, claims to verify, per-competency question banks with target depth, suggested opening, time budget per section.
  - Carries explicit `opening_minutes` and `closing_minutes` allocations (~2 min each). Competency budgets sum to the remainder of the configured duration — never to a hardcoded 40.
  - Blueprint generation must budget sections against the spec's configured duration.
- **SectionOutcome** — produced by the brain, one per interview section. Coverage level reached, key claims extracted (each anchored to a transcript turn), contradictions observed, time and turns spent, and whether the section was squeezed below its floor.
- **TranscriptTurn / Transcript** — speaker, text, timestamps. The transcript is the auditable ground truth of the interview; persist fully.
- **FeedbackReport** — per-competency score with evidence quotes (verbatim from transcript with timestamps), red flags observed, overall summary, recommendation signal. Framed as evidence for a human decision-maker, never as an automatic accept/reject.

Exact fields: propose them in Milestone 2 and iterate; keep them versioned (a `schema_version` field on each).

## Environment variables

```
OPENAI_API_KEY=
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
DAILY_API_KEY=
DATABASE_URL=          # Railway Postgres
APP_BASE_URL=

# Model tiering (see Architecture decisions). Optional; defaults shown.
OPENAI_LLM_MODEL=gpt-4.1-mini      # live conversation — never a reasoning model
OPENAI_BLUEPRINT_MODEL=gpt-4.1     # blueprint generation — reasoning tier
OPENAI_FEEDBACK_MODEL=gpt-4.1      # feedback scoring — reasoning tier
```

Never hardcode keys. Never commit `.env`. Provide `.env.example`.

## Build milestones

Work strictly in this order. Do not start a milestone until the previous one's Definition of Done is met.

### Milestone 1 — Voice hello-world
Pipecat bot that joins a Daily room and conducts a spoken interview for a fixed role.
- Local run: script creates a Daily room via REST API, spawns the bot, prints the room URL; developer joins via Daily prebuilt UI in a browser.
- Pipeline: Daily transport in → OpenAI STT → OpenAI LLM → ElevenLabs TTS → Daily transport out. All streaming. Interruption (barge-in) enabled.
- Listening config: VAD plus smart-turn / semantic endpointing.
- **Static interview role.** The bot interviews for a hardcoded target role — Senior Product Manager, 10–11 years — held in a static data file (`bot/fixtures/pm_senior.json`) that is a real `InterviewBlueprint` instance, not a bespoke format. It covers: product strategy and vision, prioritization and trade-offs, stakeholder and conflict management, metrics/experimentation fluency, execution and shipping track record, leadership without authority. Depth expectations befit 10+ years (concrete examples from shipped products; push past frameworks to actual decisions made). 3–4 seed questions per competency.
  - This fixture is a **stand-in**. Milestone 3 replaces it with a generated blueprint with **zero changes to consuming code** — the seam is `bot/blueprint_source.load_blueprint()`. Nothing may reach past that function to the fixture file.
- **The opening greeting runs on the candidate's clock, not the server's.** `datetime.now()` on Railway is UTC while candidates are in India, so the bot greeted five and a half hours behind the person it was talking to: right through the morning, wrong every afternoon and evening. The join page sends `Intl.DateTimeFormat().resolvedOptions().timeZone` and the bot greets on that (`bot/greeting.py`). The string is untrusted browser input, so every path falls back rather than raises, to `DEFAULT_TIMEZONE` and then UTC. Outside 05:00-22:00 local there is no time-of-day greeting at all: "good morning" at three in the morning is worse than "hello".
- **Proactive silence handling.** A silence timer measured from the end of the bot's last utterance with no candidate speech started, driving a three-stage escalation ladder (all timings configurable, in `bot/silence.py`):
  - ~15–20s: gentle nudge ("Take your time, there's no rush.")
  - ~40s: rephrase the question plus an audio check ("If you can hear me, I may not be receiving your audio — you might want to check your microphone.")
  - ~2 min cumulative silence: graceful close and clean session end.
  - Any pending nudge cancels the instant candidate speech starts.
  - **The timer must respect turn-taking state.** It runs only when the candidate has not started answering — never during a mid-answer thinking pause the smart-turn layer is already tolerating. A nudge firing mid-pause would reintroduce the interruption problem. This is enforced structurally: the idle timer arms only on `BotStoppedSpeaking` while no user turn is in progress.
  - All silence events and nudges are logged to the session JSON.
- **DoD:**
  - Developer has a fluid spoken conversation in the browser; interrupting the bot works.
  - Bot does not interrupt a 3-second mid-answer thinking pause.
  - Per-turn time-to-first-audio, measured from the true end of candidate speech, is logged. Median target < 1.5s.
  - Bot demonstrably interviews for the configured role, including at least one on-topic redirect when the candidate strays.
  - Bot recovers a stalled conversation with a nudge, and ends an abandoned session cleanly.
  - Transcript of the session prints to console/file on exit.
- Tune in this priority order: **(1) doesn't interrupt, (2) feels fast.** Never trade (1) for (2). Set latency parameters from measured data with margin, never below a value that risks firing on a truncated transcript — truncated-answer correctness beats latency.

### Milestone 2 — Contracts + blueprint pipeline (text only, no audio)
- Define the four Pydantic contracts in `shared/`. (`EvaluationSpec` and `InterviewBlueprint` were drafted early in Milestone 1 because the static role fixture consumes them; extend rather than redefine.)
- JD upload endpoint (PDF/DOCX/text) → parsed → clarification chat endpoint: multi-turn Q&A with the employer that fills the EvaluationSpec; the AI asks about competency weights, depth expectations, dealbreakers, then confirms a summary.
- **The clarification chat must never attribute a position to the employer they did not state.** Observed failure: asked twice about technical depth, dodged twice, the assistant then wrote "Technical depth means being able to whiteboard payment flows" into the summary as the employer's view — the opposite of their actual position — got a skim-read "looks right", and turned it into a weighted competency. An employer approving a plausible summary is not the same as an employer expressing a preference.
  - A prompt rule alone did not hold. The fix is structural: the model must emit an `inferred` list of everything it assumed but was not told, mark those items inline in the summary, and the panel/script surfaces them prominently. Same lesson as the contradiction imperative — removing the option beats adding emphasis.
  - `blueprint/simulated_employer.py` provides `SimulatedEmployer` and `DistractedEmployer` (answers a different question than the one asked) for demos and tests. Note: an earlier harness fired canned answers positionally, which produced nonsense transcripts *and* masked this defect.
- CV upload endpoint → parsed → InterviewBlueprint generated from CV + EvaluationSpec and stored in Postgres. Generation is kicked off **at CV upload time** as a BackgroundTask: the employer gets an immediate upload response, and the blueprint is finished and persisted long before anyone joins an interview. Never generate a blueprint at interview start.
- **DoD:** running a script with a sample JD + sample CV produces a complete, sensible InterviewBlueprint in the DB, viewable as JSON. Include 2–3 sample JD/CV fixture files and pytest tests over the generation logic.
- **Time budgets are computed in code, not by the model** (`blueprint/generate.py`). The contract validator rejects a blueprint whose sections overrun the configured duration, and models cannot be relied on to make a set of numbers sum exactly. The model supplies a relative `emphasis` per competency (0.5–2.0); minutes are `weight × emphasis`, normalised to the available time. Employer `weight` says what matters for the role; `emphasis` says where *this candidate* needs probing — so a heavily-weighted competency the CV already evidences yields time to a lighter one it does not.
- Local development falls back to SQLite when `DATABASE_URL` is unset, so the blueprint pipeline can be exercised without Postgres running.

### Milestone 3 — Blueprint-driven interview (end to end)

> **Re-sequenced:** the sectioned brain was pulled forward and built against the static PM fixture, in parallel with Milestone 2. The M2→M3 dependency was never on the blueprint *pipeline*, only on a blueprint *instance* conforming to the contract — which `bot/fixtures/` plus the `load_blueprint()` seam already provide. Milestone 2 adjusts contracts as brain-driven learnings emerge: **the brain wins contract disputes**, because the consumer defines the shape.

- `POST /interviews` creates interview record + Daily room + meeting credentials; endpoint to start/spawn the bot with the blueprint injected.
- **Sectioned interview brain** — state machine over the blueprint:
  - **Section-scoped context assembly.** The model sees the current section's turns verbatim, not the whole interview. This is the answer to unbounded context growth, and it is why a mini-tier live model is expected to be viable.
  - **Key-claims carryover.** Claims are extracted at section end and carried forward, so cross-section callbacks and contradiction detection work without replaying raw history.
  - **Depth judgment.** Heuristics set floor and ceiling turns per section; an off-critical-path LLM judgment decides within that band. **The judgment must never block the next spoken turn** — if it has not returned, heuristics rule that turn.
  - **Never claim history the model cannot see.** At a section boundary the verbatim window is empty. Telling the model to "continue naturally" there made it invent the conversation — a live session opened a section with *"you mentioned earlier that you had a hand in shaping a key product strategy"* to a candidate whose only words had been "I don't want to." The fix is to hand it the truth, not an instruction: the plan carries **one bridging exchange** from the previous section, and the prompt states plainly when the candidate has not yet said anything substantive. Fabricating a candidate's words is the single worst failure this product has — it puts invented claims into an auditable hiring record.
  - **Refusal handling.** A candidate declining is disengaged, uncomfortable, or done; the bot is not entitled to an answer and must never interrogate. Detection is deliberately conservative and deterministic (`bot/brain/refusal.py`) — matched against the whole utterance, so "no" is a refusal but "no, that's not how it went, what we did was…" is an answer. Treating a real answer as a refusal is the worse error.
    - 1 refusal → acknowledge briefly, ask a different, easier question on the same topic. Never re-ask the declined question.
    - 2 consecutive → change topic entirely, and offer to move on or to stop.
    - 4 consecutive → close the interview gracefully.
    - **`declined_turns` is recorded separately from low coverage.** "Declined to answer" and "answered shallowly" mean different things about a person, and only one is about their ability. A report must not conflate them.
  - **Coverage must never flatter.** Opening and closing are not scored, but a live session recorded the opening as `sufficient` for a candidate who refused to speak. Non-competency sections now report `insufficient` when nothing substantive was said.
  - **Contradictions.** Record always; probe at most once, neutrally phrased, curious rather than prosecutorial ("how does that fit with the earlier X?"). Never voice a verdict.
    - **Callback raise rate is measured, not assumed** (`scripts/contradiction_rate.py`). Instruction strength is the whole story, and it is not intuitive — a *stronger* model complied less. Measured over 10 trials per model:
      | prompt version | gpt-4.1-mini | gpt-4.1 |
      | --- | --- | --- |
      | "raise it once, only if it fits naturally" | 0% | 0% |
      | "your next question MUST be about it" | 80% | 27% |
      | forbids the alternative question + supplies the sentence + placed at top of prompt | **100%** | **100%** |
      Every failure at the middle version looked identical: the model asked the natural conversational follow-up instead. Removing the alternative beat adding emphasis. Re-measure after any edit to this block.
    - **Judge precision is measured too** (`tests/test_judge_precision.py`, 12 labelled cases). Genuine contradictions must be separated from hedges, non-answers, and legitimate opinion revision — revision is candour and flagging it punishes honesty. Precision went 0.50 → 1.00 (recall 1.00) once the judge prompt listed the exclusions explicitly. Precision is weighted above recall: a false contradiction becomes a written claim about a real person's honesty, while a missed one can still be caught by the human reading the transcript.
  - **Over-run policy.** Weighted squeeze against configured duration + grace. Squeeze remaining sections proportionally by weight; if a section would fall below its floor, shrink the lowest-weight section first and log a **coverage-shortfall event** so the feedback report can state which competencies got insufficient signal. Silently blowing past the time limit is worse than honestly reporting a gap.
- Brain must be testable in text mode: a pytest harness that runs a scripted mock candidate through the brain without any audio, including a candidate who **contradicts himself across sections** to exercise callback detection.
- On session end: full transcript persisted to Postgres against the interview record; interview status → `completed`.
- Cost telemetry: log per interview — LLM tokens in/out, TTS characters, STT minutes, call minutes. Persist against the interview record so real per-interview cost replaces estimates.
- **DoD:** a full mock interview conducted by voice against a real blueprint; transcript in DB; text-mode brain tests pass; per-interview cost figures readable from the DB.

### Milestone 4 — Feedback report
- On `completed`, a BackgroundTask scores the transcript against the EvaluationSpec → FeedbackReport stored in Postgres. Same pattern as blueprint generation: kicked off the moment the interview completes, not when someone opens the report.
- Every score must cite transcript evidence (quotes + timestamps). No evidence → mark "insufficient signal," never invent.
- **The channel is evidence about the connection, never about the candidate** (`feedback/health.py`, `ConversationHealth`). Repairs, dead air, echo and disconnects all belong in one place, because without one they silently become evidence about the person: a cheap headset reads as an incoherent answer inside a hiring record. Same distinction the brain already makes by keeping `declined_turns` apart from low coverage — "could not be heard" is a third thing, and it was being scored as the second. A degraded recording is stated at the top of the report and caps the recommendation at `limited_evidence`.
  - Derived from the stored transcript rather than recorded live, so rebuilding an old report picks up improved heuristics.
  - **Calibrated against real recordings, and the first version was wrong.** Counting short turns as broken audio flagged five of eight real interviews, healthy ones included: OpenAI's STT emits "uh", "so" and "okay" as separate turns in every session. Fillers are now excluded, and `fragmentary_turns` is reported but never decisive on its own. **A signal that fires on everything cannot be the one that decides anything.** All eight real sessions now read clean.
- **Scoring runs once, at the end, against the complete saved transcript.** Never incrementally, never between turns, never on the conversational path. It is triggered in the bot's shutdown path *after* the transcript commit and outside its try block — the transcript is the thing that must survive, and a scoring failure must never be able to take it down with it. `POST /interviews/{id}/feedback` is a retry for when the process was killed mid-scoring, not the trigger.
- **Quotes are verified in code, not trusted** (`feedback/score.py`). A model asked for verbatim evidence will occasionally produce a plausible sentence the candidate never said, and a fabricated quote inside a hiring record is the worst failure this product has. So:
  - Every quote is matched against the transcript on lowercase alphanumerics — loose enough to survive the model tidying punctuation, strict enough to catch invention.
  - A quote found at a different turn than claimed is **re-anchored**, not dropped: the model miscounted, but the words were said.
  - A quote found nowhere is dropped. If that leaves a score with no evidence, the score is **downgraded to insufficient signal** rather than published on the strength of something invented.
  - Only candidate turns count. Otherwise the bot's own question becomes the candidate's answer.
  - An unevidenced red flag is dropped entirely — it is an accusation, not a finding.
  - Verified against a real transcript: 6/6 quotes independently confirmed, and a deliberately poisoned report had its invented quote, its invented red flag ("Admitted to falsifying metrics") and its inflated `strong_evidence_for` all removed.
- **Confidence is capped by weighted coverage, not by count.** A confident signal requires ≥75% of the spec *by employer weight* to have been assessed; below that the recommendation is capped at `limited_evidence`. Covering one of two competencies means something different depending on whether it was the 60% one or the 40% one, and counting cannot tell them apart.
- **A candidate is scored against the spec snapshot inside their own blueprint**, not the profile's current spec. This is what makes the M5 revision rule safe: revise a profile and an already-interviewed candidate's report still measures them against what they were actually asked.
- Report layout is scores-and-evidence first, written summary last (employer's call). A reader who only looks at the top should see what the transcript supports, not a paragraph telling them what to think.
- **DoD:** report generated for the Milestone 3 mock interview; JSON retrievable via API; report reads as fair and grounded when checked against the transcript by a human.

### Milestone 5 — Frontend
- Employer panel: upload JD → clarification chat UI → upload CV → interview list with statuses → feedback report view.
- **Single admin, no multi-tenancy.** One shared `ADMIN_PASSWORD`, no default — a known password on a public URL is worse than no panel. Every route is guarded except the candidate's own join, `/join`, `/health` and the login screen itself. `test_every_route_is_guarded_unless_it_is_deliberately_public` sweeps the route table so a new endpoint cannot be merged without one.
- **Panel shape** (employer's own spec): profiles list → profile → candidate → session. A profile carries an employer-typed `title` and `business_unit`, taken at creation because the spec's `role_title` does not exist until the clarification chat finishes — without them a profile is unidentifiable in the list for its whole setup, and two openings for the same role are indistinguishable forever. Settled discussions collapse to one line; what the interview tests is the prominent thing; candidates sit at the bottom carrying their `interview_status`.
- **Navigation is real.** Every screen has its own URL in the hash and is reached by an ordinary `<a href>`, so back, forward, refresh, bookmarking and middle-click all work. Self-refreshing screens take a ticket that a scheduled re-render checks before firing, otherwise navigating away from a waiting screen lets its timer redraw over whatever was opened next.
- **One live session per candidate.** At most one interview in `scheduled` or `in_progress`; a new one only once the last is completed, expired or failed. Two sessions meant two Daily rooms and two credential pairs, and whichever set the candidate was sent, the other sat paid for and empty. A scheduled session can be cancelled; a live one cannot (someone is mid-sentence in that room), and a completed one is a record, not something to cancel.
- **A revised spec must not reach backwards.** The spec can be reopened after it is locked (`POST /jobs/{id}/reopen`), and `Job.spec_version` increments when the revision is confirmed. Propagation, by employer decision:
  - **already interviewed → never touched.** Their blueprint is the record of what they were actually asked. Rewriting it would let a later feedback report score them against competencies that did not exist while they were being interviewed — a written judgement about a real person, measured against a yardstick nobody applied to them.
  - **hand-refined → left alone, flagged stale.** Regenerating would silently discard the employer's own edits, so the panel surfaces `plan_is_stale` and the choice stays theirs.
  - **everyone else → regenerated** in the background against the new spec.
  - Every blueprint already embeds a full spec snapshot, so this needs no separate versioning of the spec itself; `Candidate.spec_version` records which version a plan was built from.
- Candidate flow: join page (meeting ID + password → backend validates → short-lived Daily meeting token) → consent screen → branded interview page using Daily client SDK (mic check, in-call UI with timer) → thank-you page.
- Consent screen, shown pre-join and blocking: plain-language notice that the interview is conducted by an AI and is recorded, and that the transcript and recording are shared with the employer for hiring evaluation. Explicit accept button — no pre-ticked box, no implied consent by joining. Acceptance is timestamped in the DB against the interview record.
- Candidates must never see Daily branding or raw room URLs; rooms are private, access only via tokens minted by our backend.
- **DoD:** full flow works locally end to end through the UI with no manual API calls.

### Milestone 6 — Railway deployment
- Deploy backend + Postgres to Railway (US region); frontend served statically by FastAPI or as a second service. See `DEPLOY.md`.
- Confirm no Railway idle / scale-to-zero / auto-sleep setting can kill a long-running process, and disable any that is present. This is a settings check, not a 45-minute soak test.
- **Keep replicas at 1.** A bot is a child process of one specific container; more replicas means a redeploy or rebalance can orphan a live interview in ways that are hard to observe.
- **Concurrency is capped, and the cap refuses rather than degrades** (`app/capacity.py`, `MAX_CONCURRENT_INTERVIEWS`, default 6). One bot too many gets the container OOM-killed and restarted — which ends *every* interview running on it, not just the new one. Turning one candidate away costs one reschedule; accepting them costs all of them. The count comes from live child processes, never from interview rows: a bot that crashes never writes `completed`, so a status-based count drifts upward until the cap refuses everything forever.
  - Sizing on the 8GB / 8 vCPU replica: at ~400MB per bot under load, memory allows ~19. **The default is 6 because per-bot CPU is unmeasured** — Silero VAD runs continuously and smart-turn fires at every turn end, sustained rather than bursty, so CPU is expected to bind well before memory. Raise the limit from what the Metrics tab shows during concurrent interviews, not from the memory arithmetic.
- **Do not install the `local-smart-turn` extra.** It exists for the v2 CoreML/PyTorch turn analysers and pulls torch, torchaudio, transformers and coremltools — ~570MB, none of it loaded at runtime, because `LocalSmartTurnAnalyzerV3` runs a bundled ONNX model through onnxruntime. Dropping it took the virtualenv from 1.1GB to 537MB. `test_turn_detection_does_not_pull_in_torch` guards the regression.
- **DoD:** a pilot interview conducted entirely on the deployed URL by someone on a different network/device.

## Conventions

- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy or SQLModel, pytest. Frontend: React + Vite, TypeScript.
- Before implementing against Pipecat, Daily, or provider APIs, check the installed package versions and read the current docs/examples — these APIs change fast; do not code from memory.
- Interview logic changes must be verifiable via text-mode tests. Never make the audio loop the only way to test brain behavior.
- **Two test suites, never mixed.** Mixing them yields a flaky suite nobody trusts, which wastes the speed advantage text-mode testing exists to buy.
  - **Deterministic brain-logic suite** — fake LLM, fast, CI-gating. Transitions, budget arithmetic, carryover assembly, `SectionOutcome` construction.
  - **Tolerant behaviour suite** — real LLM calls, slower, run deliberately. Contradiction detection, thin-answer probing, off-topic redirect, instruction adherence.
- Log every interview event (session start/end, errors, provider failures) with the interview ID.
- Voice quality judgments (naturalness, latency feel, turn-taking) are made by the developer by ear — flag when a live listening session is needed rather than assuming.
- **Diagnosing voice problems — name the layer before touching anything.** Wrong-moment interruptions (bot cuts in on a pause, or waits too long after the candidate is clearly done) are *endpointing* problems: tune turn-taking config. Wrong words in the transcript are *STT* problems: a vendor concern, not something to fix by adjusting turn-taking. Never "fix" one by tuning the other.
- **No deploys during live interviews.** A redeploy kills every in-flight bot subprocess. Check for active sessions before shipping.

## Out of scope for the POC (do not build unless asked)

- Google Meet / Zoom / Teams integration
- Proctoring (browser signals, video analysis)
- OpenAI Realtime speech-to-speech mode (planned as a post-POC A/B experiment)
- ATS integrations, multi-tenant auth, billing
- Automatic hiring decisions of any kind — reports inform humans, nothing auto-rejects
