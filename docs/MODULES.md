# Modules

What every file is for, why it exists, and what breaks if you change it.

This is the index. [ARCHITECTURE.md](ARCHITECTURE.md) is the map and does not
repeat what is here; [DECISIONS.md](DECISIONS.md) holds the measurements behind
any constant named below, and [CHANGELOG.md](../CHANGELOG.md) holds the history.
Where a module rests on a measured number, the number is named and the entry is
pointed at rather than restated.

**Real-time** below means the module runs while a candidate is speaking and can
add latency they hear. Everything else is either request-time or offline.

---

## shared/

Everything crosses this package. Nothing in it imports from anywhere else in the
repo, which is what lets `bot/` stay free of FastAPI and `app/` stay free of
Pipecat.

### shared/contracts.py

**947 lines. The single source of truth for every shape that crosses a package
boundary.** Read this before adding a field anywhere.

Four contracts carry the product. `EvaluationSpec` is what the employer settled:
competencies with weights, seniority, tone, red flags, and the configured
duration. `InterviewBlueprint` is what this candidate will be asked, and it
**embeds a full copy of the spec** rather than referencing one. `SectionOutcome`
is what one topic produced. `FeedbackReport` is what a human reads.

The embedded spec is the load-bearing part. A candidate is scored against the
snapshot inside their own blueprint, never the profile's current spec, which is
what makes revising a job safe: an already-interviewed candidate is still
measured against what they were actually asked. Remove that copy and a revision
silently rescores people against competencies nobody put to them.

Three things here are computed in code and are not the model's to assert.
`DealbreakerState.not_reached` is the residue left after the fired and cleared
lists are matched back to the spec, so the only way to report coverage of a
dealbreaker is to produce a verified quote. `degraded` on each of the three
health classes is a `@computed_field` rather than a plain property, because a
property is invisible to anything reading the object as JSON, and the panel does
exactly that. `as_sentence` is likewise computed here so the wording exists in
one place: the panel once kept its own copy and the two drifted, so the scoring
model was told one thing and the employer read another.

Every model carries `schema_version`. `SCHEMA_VERSION`, `MIN_DURATION_MINUTES`,
`MAX_DURATION_MINUTES`, `DEFAULT_DURATION_MINUTES` and
`RECORDING_RETENTION_DAYS` all live here and are read by the API, the panel copy
and the retention sweep, so a promise made to a candidate and the code that
enforces it cannot disagree.

**If you change this:** everything. Adding an optional field is safe. Renaming
or tightening one breaks the bot, the scorer, the panel and every stored row at
once, because outcomes and reports are persisted as JSON and old rows are
re-read by `_validated_report`.

### shared/branding.py

**78 lines, constants only, no logic.** The product's name, tagline, logo URLs,
and `PROSE_STYLE`.

It exists because the name reaches a candidate through three separate channels,
the spoken introduction, the Daily participant list and the join page, and a bot
that calls itself one thing and appears as another is unsettling to someone
already nervous. `COMPANY_NAME` and `COMPANY_CREDIT` are deliberately separate
from `BOT_NAME`: Mitra is the interviewer, Appinventiv is who built it, and the
company name reaches no prompt.

`PROSE_STYLE` is appended to every prompt whose output a person reads. All four
of `clarify.py`, `generate.py`, `refine.py` and `score.py` do this with the same
idiom, a self-referencing reassignment (`SYSTEM = f"{SYSTEM}\n\n{PROSE_STYLE}"`).
That looks like a duplicate module-level name and is not.

**If you change this:** the page tokens are substituted in `app/main.py`, so a
renamed constant produces a page with a literal `{{TOKEN}}` in it. Tests assert
no `{{` survives rendering.

---

## backend/app/

FastAPI. Request-time, never real-time. Async work is `BackgroundTasks` plus a
status column, and is always scheduled the moment its input arrives rather than
the moment its output is needed.

| module | one-line job |
|---|---|
| `main.py` | uploads, the clarification chat, blueprint generation, serving both UIs |
| `interviews.py` | create, join, run, read and cancel an interview |
| `recordings.py` | optional recording, the retention sweep, playback |
| `db.py` | SQLAlchemy models and the additive migration |
| `auth.py` | one admin password, one signed cookie |
| `capacity.py` | how many bots may run at once |
| `meeting.py` | human-readable meeting IDs and passwords |
| `jd_templates.py` | starting-point job descriptions |

**`main.py`** (902 lines) owns the employer half. `_render_page` reads a UI file
and substitutes the branding tokens, cached with `lru_cache(maxsize=8)`, which is
one more than the number of pages: at 2 it thrashed and re-read disk on every
request. It also strips the half of the consent notice that does not apply, so
the page cannot promise a recording that is switched off. Imports
`EvaluationSpec` and `InterviewBlueprint`. Changing the token list here without
changing `branding.py` leaves literal braces on a candidate's screen.

**`interviews.py`** (561 lines) owns the interview lifecycle. `join_interview` is
the only public route that does anything: it validates the candidate's meeting ID
and password, mints a short-lived Daily token, **and spawns the bot at that
moment**, not at scheduling. `create_interview` mints the room and credentials.
`_validated_report` re-reads stored report JSON through the contract, so an old
row that no longer validates degrades rather than crashing the panel. Imports
`FeedbackReport`. Changing the join contract changes what the candidate page
sends, and there is no JS test to catch it.

**`recordings.py`** (425 lines) is off by default. `sweep_forever` collects
finished recordings from Daily and deletes them there, then deletes local copies
past `RECORDING_RETENTION_DAYS`. **The sweep deliberately does not read the
recording flag**: recordings made before it was switched off still have to be
collected and deleted, and a flag that stopped that would strand them and keep
billing. `sweep_enabled` reads its own separate switch, and reads it as
"disabled unless truthy" so a half-set `RECORDING_SWEEP_DISABLED=false` does not
disable it.

**`db.py`** (442 lines) holds four tables: `jobs`, `clarification_turns`,
`candidates`, `interviews`. Transcript, section outcomes, session metrics and the
feedback report are JSON columns on the interview row. Migration is additive
only: columns are added, never dropped or retyped, because the deployed database
is the record of every interview that has happened. Without `DATABASE_URL` it
falls back to SQLite so the whole product runs locally.

**`auth.py`** (94 lines) is a single shared password and a signed cookie.
`require_admin` is the dependency every route carries except a listed few. There
is a sweep test over the route table that fails on any new endpoint that is
neither guarded nor in `PUBLIC_ROUTES`, so adding a route without a guard cannot
merge.

**`capacity.py`** (112 lines) caps concurrent interviews at
`DEFAULT_MAX_CONCURRENT`, and **refuses rather than degrades**: one bot too many
gets the container OOM-killed, which ends every interview on it rather than just
the new one. `BotRegistry` counts live child processes and never interview rows,
because a crashed bot never writes `completed` and a status-based count drifts
upward until the cap refuses everything forever.

**`meeting.py`** (71 lines) generates credentials a person can read down a phone.
`CONFUSABLE_PAIRS` removes characters that sound or look alike, and
`normalise_meeting_id` maps them back, so a candidate typing `O` for `0` gets in.

**`jd_templates.py`** (80 lines) loads six Markdown files from
`backend/jd_templates/`. `templates()` is cached and reads each title from the
file's own `#` heading rather than a second list, so the panel cannot label a
template one thing while the text says another. A slug in `ORDER` with no file is
skipped rather than raised on. **Text only**: nothing here builds an
`EvaluationSpec`, and a test asserts the templates contain none of the words
"competency", "weight" or "dealbreaker".

---

## backend/blueprint/

JD and CV in, `EvaluationSpec` and `InterviewBlueprint` out. Reasoning-tier
model, entirely offline, latency irrelevant. This is where the product's
intelligence is front-loaded so the live model can stay small.

**`documents.py`** (248 lines) pulls text out of PDF, DOCX, txt and Markdown.
`extract_text` is the only entry point. `MAX_UPLOAD_BYTES` and `MIN_USABLE_CHARS`
bound it at both ends: a file too large is refused, and a file that yields almost
no text is refused rather than being passed to a model that will invent a role
from nothing. `MAX_TABLE_DEPTH` bounds how far the
walk follows tables nested inside table cells: real CVs nest one or two deep, so
anything deeper is a malfunctioning builder or a pathological hand-made file.
Stopping at the top level was losing most of the document, and one sample had 99
characters there against 363 in two nested tables.

**`clarify.py`** (369 lines) is the multi-turn chat that fills an
`EvaluationSpec`. `ClarificationChat.next_turn` takes the JD text and the history
and returns a reply, a `done` flag and possibly a spec.

The important behaviour is the one that stops it putting words in the employer's
mouth. The model must emit an `inferred` list of everything it assumed but was
not told, marked inline in the summary, because a prompt rule alone did not hold:
an employer dodged a question about technical depth twice, and the assistant
wrote a position into the summary as the employer's view, got a skim-read "looks
right", and turned it into a weighted competency. Approving a plausible summary
is not the same as expressing a preference.

The second thing worth knowing is the ordering fix: `reply` used to be checked
before `done` and `spec`, so a completing turn that had nothing left to say threw
away the spec the whole conversation existed to produce. The check now happens
after the verdict. Imports `EvaluationSpec` and the three duration constants.

**If you change this:** the spec is the input to blueprint generation and the
yardstick the scorer uses. A field this stops filling is a field the report
cannot score.

**`generate.py`** (398 lines) turns a CV plus a spec into an
`InterviewBlueprint`. Two things here are deliberately not the model's job.

`allocate_minutes` computes time budgets **in code**. The model supplies a
relative `emphasis` per competency and minutes are `weight x emphasis`,
normalised to the available time after `OPENING_MINUTES` and `CLOSING_MINUTES`
are taken out, with `MIN_SECTION_MINUTES` as a floor. Models cannot be relied on
to make a set of numbers sum exactly, and the contract validator rejects a
blueprint that overruns.

`verify_vocabulary` and `build_register` borrow the field's language rather than
inventing it. Every term in the `InterviewRegister` is checked against the JD and
the CV in code, with trailing-`s` tolerance only, and capped at
`MAX_VOCABULARY`. A specialist who hears one misused term stops trusting the
interview, so borrowing only what the documents already say makes that impossible
rather than unlikely. An empty register is a fine outcome and means plain
language.

**`refine.py`** (224 lines) changes a generated plan from a description of the
change. Used by the panel when an employer wants a different emphasis without
regenerating. A hand-refined plan is flagged stale rather than silently
regenerated when the spec is revised, because regenerating would discard the
employer's own edits.

**`simulated_employer.py`** (147 lines) supplies scripted employers for tests and
demos, including `DistractedEmployer`, which answers a different question than
the one asked. That exists because an earlier harness fired canned answers
positionally, which produced nonsense transcripts and, worse, masked the
attribution defect above.

---

## backend/bot/

The live interview. **Everything here except `brain/` and `persistence.py` is on
or beside the real-time path.**

**`run_bot.py`** (615 lines) wires the Pipecat pipeline and owns the session
lifecycle: build transport, STT, LLM, TTS, attach the observers and the silence
ladder, run, then save. One OS process per interview. `GREETING_SETTLE_SECONDS`
exists because the first word of the greeting was being clipped before the
transport had settled. On exit it commits the transcript and **then** scores,
outside the try block, so a scoring failure can never take the transcript down
with it.

**`brain_director.py`** (355 lines) is the Pipecat adapter and the only thing
that bridges the pure brain to the async world. It rewrites the LLM context
before each turn from `plan_turn()`, feeds `observe()` afterwards, and fulfils
`pending_judgment_request()` by calling the judge as a background task. It counts
judgement attempts, successes and failures, which is what `JudgmentHealth` later
reads. `FILLER_COOLDOWN_TURNS` bounds the spoken acknowledgement, which is off by
default.

**`turn_taking.py`** (153 lines) decides when the candidate has finished
speaking. `build_vad_analyzer` and `build_turn_strategies` combine Silero VAD
with a smart-turn analyser. `SMART_TURN_STOP_SECS`, `VAD_STOP_SECS` and
`TURN_END_CONFIDENCE` are all measured and are listed as open items in
DECISIONS.md; `SMART_TURN_STOP_SECS` is the largest remaining latency and needs a
listening session rather than a code change. **Never tune this to fix
transcription.** Wrong words are an STT problem and they feel identical.

**`tools.py`** (192 lines) defines the one function the live model can call,
`end_interview`. The description is written to be easy to call correctly and hard
to call by accident, and the handler corroborates the model against the
deterministic patterns in `withdrawal.py`: both agree and the interview ends at
once, only the model agrees and the candidate is asked once. A third outcome was
added after a live interview read "can we skip it" as leaving: if the utterance
is a request to skip the question, the offer to stop is vetoed and it becomes a
declined turn. **The handler must return something truthy**, or Pipecat runs no
follow-up completion and the candidate gets silence.

**`ending.py`** (108 lines) hangs up. It is an **observer, not a processor**, and
that is not a style choice: a processor only sees frames passing its own
position, so a check placed before the output transport never sees the bot finish
speaking. It also must not be a plain `@dataclass`, because Pipecat keeps
observers in a set and a generated `__eq__` removes the default hash, so
registration dies and the call never ends. Both failures shipped.

**`silence.py`** (300 lines) is the three-rung escalation ladder for dead air:
gentle nudge, rephrase plus an audio check, then a graceful close. The first rung
scales with what was just asked, because "tell me about a decision you regret" is
someone searching their memory and prompting into that is an interruption
arriving slowly. Later rungs are about the channel rather than about thinking, so
they stay put. The timer arms only on `BotStoppedSpeaking` while no user turn is
in progress, which is what keeps it out of a mid-answer pause.

**`presence.py`** (150 lines) tracks who is actually in the room. Without it the
bot nudged empty rooms and then closed sessions blaming a silence that was really
a dropped call. Disconnects flow into `ConversationHealth`, so a dropped call
reads as a bad connection rather than an uncooperative candidate.

**`observers.py`** (534 lines) carries the instrumentation.
`TurnLatencyObserver` produces the latency record; `TranscriptObserver` builds
the transcript and stamps each turn with the section it belongs to, via a
`section_id_now` callable supplied by `run_bot.py`. **That stamp matters more
than it looks**: `assess_questioning` skips turns with no section, so before this
existed it was scoring nothing at all on every stored interview.
`FALSE_TURN_END_SECONDS` filters turn ends that were not real.

**`persistence.py`** (186 lines) writes the finished interview. `build_transcript`
and `save_interview_result` are the only writers. Imports `Transcript`,
`TranscriptTurn`, `Speaker` and `RECORDING_RETENTION_DAYS`. Not real-time: it
runs after the call.

**`blueprint_source.py`** (98 lines) is the seam that loads a plan, from the
database for a real candidate or from `bot/fixtures/*.json` otherwise. Nothing
reaches past `load_blueprint()` to a fixture file. That seam is what let the
brain be built against a static fixture before the generation pipeline existed.

**`greeting.py`** (64 lines) resolves the candidate's timezone and returns a time
of day. The server runs UTC and candidates are in India, so the bot greeted five
and a half hours behind the person it was talking to. The browser sends its own
timezone, every path falls back rather than raises because that string is
untrusted input, and outside `GREETING_HOURS` there is no time-of-day greeting at
all, because "good morning" at three in the morning is worse than "hello".

**`persona.py`** (80 lines) builds the system instruction for the non-brain path.
**`config.py`** (210 lines) resolves environment and model tiering, and holds the
three feature switches. Read the polarity note there before adding a fourth:
`RECORDING_ENABLED` (the default for new interviews, each of which then carries its
own switch) and `FILLER_ENABLED` are off unless set,
`TERM_PROBING_DISABLED` is inverted because term probing has shipped in every
measurement ever taken here and absent has to mean today's behaviour.

### backend/bot/services/

One small adapter per vendor, so a swap is a one-file change.

**`stt.py`** (180 lines). `DEEPGRAM_API_KEY` is the entire switch: present means
Deepgram, absent means OpenAI. The two are not equivalent and the difference is
structural rather than accuracy: Pipecat's OpenAI service never calls
`confirm_finalize`, so every turn waits out a fixed timer however fast the words
arrived, while Deepgram's calls it once. **Do not try to fix that by tuning
timers on the OpenAI path.** `DEEPGRAM_FINALIZATION_WAIT_SECONDS` is set above
the observed maximum on purpose: a timer firing on a fragment means the
interviewer answers half a sentence.

**`tts.py`** (113 lines). ElevenLabs, low-latency model, one configured voice.
`verify_voice` fails at startup rather than mid-interview, because a bot that
dies four minutes in is far worse than one that never starts.

**`llm.py`** (44 lines). The live conversation model, and the swap point to
Anthropic. **Never put a reasoning model here.** Thinking tokens sit on the
time-to-first-audio path and the candidate hears them as dead air.

**`daily.py`** (350 lines). Rooms, meeting tokens, transport, and the recording
API. `create_room` mints private rooms with a TTL; candidates never see a raw
room URL.

---

## backend/bot/brain/

**Pure and synchronous. Nothing here is `async`, nothing awaits, and nothing
imports Pipecat.** That is what lets the text-mode harness drive the identical
object. See [BRAIN.md](BRAIN.md) for the invariants.

### bot/brain/brain.py

**1548 lines. The state machine. A reader who does not understand the advance
ordering will break an interview.**

In: `observe(bot_text=..., candidate_text=...)` and `apply_judgment()`. Out:
`plan_turn() -> TurnPlan`, `pending_judgment_request() -> JudgmentRequest | None`,
and `SectionOutcome` per section. Imports `InterviewBlueprint`, `SectionOutcome`,
`SectionKind`, `CoverageLevel`, `KeyClaim`, `Contradiction`, `UnexplainedTerm`.

**The advance ordering is load-bearing and is checked in exactly this order** in
`_advance_reason` (`bot/brain/brain.py:1273`):

| # | reason | condition |
|---|---|---|
| 1 | `hard_stop` | the whole interview is out of time |
| 2 | `candidate_disengaged` | consecutive refusals hit the closing threshold |
| 3 | `declined_topic` | declined this topic twice |
| 4 | `depth_reached` | floor met **and** the judge said sufficient |
| 5 | `ceiling` | turns spent hit the derived ceiling |
| 6 | `budget_exhausted` | the section's seconds are gone |

**Positions 4 and 5 were the wrong way round and it made the judge decorative.**
The ceiling is derived from the section's own allocation, so a section reached it
at the moment it had spent everything it was given, and the verdict was consulted
at a point where the only thing it could do was agree with what had already
happened. Across roughly 320 stored competency sections, "sufficient" ended a
section 8 times. The floor moved with the verdict and is now a condition on that
branch rather than a separate gate above it, because a verdict promoted above the
ceiling without a floor could end a competency on its first answer. Positions 1
to 3 stay above the verdict deliberately: an interview out of time ends whatever
the judge thinks, and a candidate declining is a fact about the person that must
not be read as a section that went well.

**A turn is an exchange, not an utterance.** Only the first candidate utterance
after an interviewer turn spends a turn; the rest are the same answer still
arriving. Counting fragments separately once ran a 40-minute interview to its
closing in 291 seconds with every section marked insufficient.

The term machinery lives here too. `MAX_TERM_ATTEMPTS`,
`MAX_TERM_PROBES_PER_SECTION` and `MAX_TERM_PROBES_PER_INTERVIEW` bound how much
of an interview can be spent unpacking vocabulary, and the per-interview budget
is the only one that actually bounds it, because the judge always has something
unexplained to nominate. `SECONDS_PER_COMPETENCY_TURN` derives every section's
turn ceiling. All four are measured; see DECISIONS.md.

**Dormant:** `_rebalance_remaining` (`bot/brain/brain.py:1485`) is called on
every advance but has never squeezed anything. It measures time left against
`hard_stop_seconds`, which includes the grace period, while the plan is built to
the duration alone, so the grace is counted as available and spent before the
mechanism engages. Checked against every stored interview: 104 sections, zero
with `coverage_shortfall`. It is S6 in [AUDIT.md](../AUDIT.md) and deliberately
left. What *does* set `coverage_shortfall` is a declined section, one that ran
out of time, or one never reached.

**If you change this:** the outcomes it produces are what the scorer reads and
what the panel renders. Changing when a section ends changes how much evidence
every competency has.

### bot/brain/prompting.py

**1324 lines. Block order decides behaviour, and it has been measured
repeatedly.** `render_section_prompt` is the only public entry point besides
`reflect_back`.

The full render order for a competency turn is in
[ARCHITECTURE.md](ARCHITECTURE.md). The parts to know before editing:

`OFFER_TO_STOP` and the repair block **return early and replace everything
else**. Somebody who has asked to leave must not be asked to repeat themselves,
and nothing else is worth doing if they could not hear the question.

**The mandatory slot is split in two and that split is the measurement.** A
contradiction renders above the section's material, because below the seed
questions it competed with a page of "here is what to ask about" and compliance
fell to 27 percent against 80 percent from above. The unexplained-term half
renders below. They were separated rather than moved together precisely so that
moving one would not silently demote the other into the position already measured
at 27 percent.

A mandatory block names one item and supplies the sentence to say. An advisory
block is a preference among options, and an advisory block naming a specific item
measured at roughly no effect. This is where the repo's rule comes from,
**removing the alternative beats adding emphasis**. The compliance table is in
DECISIONS.md.

Two headings here were rewritten because the model read them back as speech. The
CV block used to open "ABOUT THIS CANDIDATE" and list "claims from their CV worth
testing", and the interviewer said "you said you built six production voice
agents" to a candidate who had said nothing of the sort. The word "claims"
supplies a claimant, and the only person available is the candidate. Both
headings now state what the material is and forbid attributing it.

`VOICE_RULES` holds the spoken-form rules, including exactly one question mark
per reply. Note that the term block's own first rung is an imperative and carries
none, which is a live inconsistency, harmless now but worth knowing before you
trust that rule elsewhere.

**If you change this:** run `scripts/compare_models.py` and
`scripts/contradiction_rate.py`. Block edits here have moved measured behaviour
more than any other kind of change in the repo.

### bot/brain/drivers.py

**363 lines. The judge's instruction is the difference between asking a senior
engineer to define their own field's vocabulary and asking about a claim that
does not stand up.**

`OpenAIInterviewer` is the text-mode conversation driver. `OpenAIJudge.assess`
takes a `JudgmentRequest` and returns a `Judgment`. It runs on the reasoning tier
and off the critical path, but it runs **during** the interview, so it is not
offline in the way `feedback/` is. It handles its own errors and reports them
rather than raising, because a failed judgement must not end a call.

`JUDGE_SYSTEM` asks for five fields: `coverage`, `rationale`, `claims`,
`contradictions` and `unexplained_terms`. The last one carries the weight. A term
earns its place by a two-step test, and step one is the one that matters: **is
the claim standing on this word?** Without it the judge nominated every undefined
noun, and the interviewer asked a senior ML engineer what "LLM" and "fine tuning"
meant while the actual load-bearing choices in the same answer went unprobed.
Sufficient is defined against the section's stated target depth, and a fluent
answer that never names a concrete decision is partial at best.

Contradiction precision is weighted above recall, because a false contradiction
becomes a written claim about a real person's honesty while a missed one can
still be caught by a human reading the transcript. `tests/test_judge_precision.py`
holds 12 labelled cases and went from 0.50 to 1.00 precision once the exclusions
were listed explicitly: hedges, non-answers and legitimate opinion revision are
not contradictions, and flagging revision punishes candour.

**If you change this:** `claims` become carried claims and the raw material of
the report; `unexplained_terms` drive every term probe; `coverage` decides when a
section can end early. `rationale` is written to the outcome and read by nothing.

### bot/brain/state.py

210 lines. `Section` is the mutable per-topic state, `BrainConfig` the tunables,
`build_sections` turns a blueprint into the ordered list. `Section.to_outcome()`
is the conversion to the persisted contract, and it is where a section fact
becomes a record. Note that the persisted `unexplained_terms` comes from
`unexplained_after_asking`, meaning asked and still unexplained, not everything
the judge nominated.

**Dormant:** `SectionOutcome.depth_rationale` and `SectionOutcome.unexplained_terms`
are written, persisted, and rendered by nothing. Verified: no reader outside the
brain and its tests. These are S3 and S10 and were left dead on purpose, because
measured against real runs they record the wrong thing more often than the right
one. Do not wire them up without reading those findings.

### The deterministic detectors

Three small modules, all whole-utterance or pattern based, all on the real-time
path, all written to fail towards doing nothing. A model call here would add
latency and unpredictability to every turn.

**`refusal.py`** (128 lines). `looks_like_refusal` matches the **whole**
utterance, which is the entire safety property: "no" is a refusal, "no, that's
not how it went, what we did was..." is an answer, and reading the second as the
first abandons a topic the candidate was engaging with. `is_substantive` and
`MIN_SUBSTANTIVE_WORDS` separate a real answer from a filler.

**`repair.py`** (136 lines). Three things a candidate means when a question
misfires, and three different answers. "Sorry, what?" means say it again,
verbatim, because the words were never the problem. "What do you mean?" means say
it in simpler words. "I don't know" is an answer and is neither. Conflating them
is what makes a voice bot feel stupid. A repair does not spend a turn, or the
depth ramp advances on nothing.

**`withdrawal.py`** (260 lines). Leaving, as distinct from declining a question.
`classify` returns `EXPLICIT`, `SOFT` or `NONE`; explicit ends the call at once,
soft earns one offer and repeating it in any form counts as accepting. Intent is
searched for **inside** the utterance rather than matched as the whole of it,
because a live transcript contained "I don't want to continue this interview.
Just end this interview." and whole-utterance matching caught none of four
requests in under a minute. Every pattern names the interview, call or session,
so "we had to end that project early" stays an answer.

`asked_to_skip_question` is a negative guard on the same path, added after a
candidate asking to skip a question was asked whether they wanted to end the
interview. It refuses to call anything a skip if the utterance names the
interview, carries an end verb, or holds any stop phrasing at all, and it is only
consulted when the patterns did not corroborate. Being wrong costs one unwanted
question; being wrong the other way keeps somebody in a room they asked to leave.

**`terms.py`** (186 lines). `asked_about(term, question)` answers whether a
question actually put a term to the candidate, which is what makes the raise
count a count of questions rather than of offers. Counting offers once told a
candidate they had been asked twice when they had been asked once. Two branches:
naming the term, and pointing at it deictically, where the second is only
consulted for a term offered on that turn. `_asks_for_something` accepts a
question mark **or** a sentence-initial imperative, because the term block's own
first rung is "Walk me through how that actually works." and requiring a question
mark meant the brain could not see its own probes.

### bot/brain/harness.py

970 lines, test and measurement only, never in production. Runs a scripted
candidate through the real brain with a real or fake model, which is how every
behavioural claim in the changelog was produced. `ScriptedCandidate` and the
named candidates (`contradicting_candidate`, `thin_answer_candidate`,
`jargon_candidate`) are the fixtures.

**Known wrong:** `DEFAULT_SECONDS_PER_TURN = 45.0` is a stale default that every
caller overrides with the brain's measured `SECONDS_PER_COMPETENCY_TURN`. It is
S14 and is kept alive only by those overrides, so a new caller that forgets one
gets sections roughly twice as long as production.

---

## backend/feedback/

Transcript in, `FeedbackReport` out. **Runs once, at the end, off the
conversational path entirely.**

### feedback/score.py

**734 lines. Quote verification is what makes the report defensible.**

`build_report` and `FeedbackScorer` do the model call; `verify_quote` and
`QuoteAudit` do the checking. Imports most of the report contract:
`FeedbackReport`, `CompetencyScore`, `EvidenceQuote`, `DealbreakerFinding`,
`DealbreakerState`, and all three health classes.

A model asked for verbatim evidence will occasionally produce a plausible
sentence nobody said, and a fabricated quote inside a hiring record is the worst
failure this product has. So `verify_quote` matches on lowercase alphanumerics,
loose enough to survive the model tidying punctuation, strict enough to catch
invention. **A quote found at a different turn than claimed is re-anchored, not
dropped**: the model miscounted, but the words were said. A quote found nowhere
is dropped, and if that leaves a score with no evidence the score is downgraded
to insufficient signal rather than published on the strength of something
invented. Only candidate turns are searched, or the bot's own question becomes
the candidate's answer. An unevidenced red flag is dropped entirely, because it
is an accusation rather than a finding.

`CONFIDENT_SIGNAL_REQUIRES_WEIGHT` caps confidence by **weighted** coverage
rather than by count: covering one of two competencies means something different
depending on whether it was the 60 percent one or the 40 percent one, and
counting cannot tell them apart. Each of the three health classes can cap the
recommendation independently.

Verified against a real transcript: 6 of 6 quotes independently confirmed, and a
deliberately poisoned report had its invented quote, its invented red flag and
its inflated evidence all removed.

**If you change this:** this is the last thing between a model's output and a
written record about a real person. Loosening the match is how a fabricated quote
gets published.

### feedback/health.py

318 lines. Three assessments, all derived from the stored transcript or metrics
rather than recorded live, so rebuilding an old report picks up improved
heuristics. `assess` produces `ConversationHealth` (the channel), `assess_judgment`
produces `JudgmentHealth` (our own analysis), `assess_questioning` produces
`QuestioningHealth` (the interviewer).

They exist for one reason: **a technical failure must never be written down as a
fact about a person.** A cheap headset reading as an incoherent answer, a failed
extraction reading as a candidate who said nothing specific, an interviewer
repeating itself reading as a candidate who repeats themselves.

The calibration lesson is worth carrying: the first version counted short turns
as broken audio and flagged five of eight real sessions, healthy ones included,
because STT emits "uh" and "okay" as separate turns in every session. **A signal
that fires on everything cannot be the one that decides anything.** `REPEAT_OVERLAP`
and `ECHO_RUN_FRACTION` are the thresholds; `_verbatim` is the exact-match test
that fires on a single word-for-word repeat with no rate applied, because an
identical question is not a matter of degree.

`assess_questioning` **skips interviewer turns with no `section_id`**, which is
why it read as healthy on every interview recorded before `TranscriptObserver`
started stamping them.

### feedback/run.py

163 lines. `generate_feedback` orchestrates and persists. Triggered in the bot's
shutdown path after the transcript commit and outside its try block.
`POST /interviews/{id}/feedback` is a retry for when the process was killed
mid-scoring, not the normal trigger.

---

## backend/scripts/

Twelve scripts, no production role. **Measurement is how this project makes
decisions**, so these matter more than their status suggests.

| script | what it answers |
|---|---|
| `compare_models.py` | the main harness: scripted candidates, the whole signal set |
| `contradiction_rate.py` | does the callback instruction actually fire |
| `tool_call_check.py` | does the model reach for `end_interview`, and only then |
| `withdrawal_run.py` | does a candidate who asks to leave actually leave |
| `unheard_candidate_run.py` | what happens when nothing can be heard |
| `silence_walkthrough.py` | what the escalation ladder would do, printed |
| `deepgram_check.py` | the STT vendor comparison |
| `build_blueprint.py`, `register_run.py` | the offline pipeline end to end |
| `dev_interview.py`, `run_interview.py` | drive one interview locally |
| `ending_run.py` | the hang-up chain |

**`compare_models.py`** is the one to read. It carries the signal definitions, and
**an instrument's scope matters as much as its logic**. Two cue sets in this file
were both named `_MECHANISM_CUES`, so one silently shadowed the other for as long
as both existed; and a 2-cue probe detector was once compared against the 19-cue
`probe_mechanism` as though they measured the same thing, producing a confident
wrong prediction. Before believing a number from here, check what produced it.

`seed_overlap_rough` and `probe_terms_specific` are still defined and are of
doubtful value; both were flagged for removal and neither has been removed.

---

## backend/tests/

Two suites, never mixed. The deterministic suite is 754 tests against a fake LLM
and runs in seconds; the tolerant suite makes real model calls and is opted into
with `RUN_BEHAVIOR_TESTS=1`. Mixing them produces a flaky suite nobody trusts.

**The tests are documentation**, and several encode specific live failures with
the actual sentences in them.

| test | what it records |
|---|---|
| `test_brain.py` (2232 lines) | the state machine, the term accounting, every advance reason |
| `test_withdrawal.py` | the verbatim transcripts where withdrawal detection failed, and the skip read as leaving |
| `test_ending.py` | four separate ways the call failed to hang up, driven through a real pipeline |
| `test_health.py` | why a heuristic that looked right flagged healthy sessions |
| `test_repair.py` | the four sentences nothing detected |
| `test_judge_precision.py` | 12 labelled contradiction cases |
| `test_copy_style.py` | user-facing copy must not read as machine-written |
| `test_interviews.py` | the route guard sweep, and the whole interview lifecycle |

`tests/fixtures/documents/` holds sample JDs and CVs, including CV layouts that
broke extraction. `bot/fixtures/*.json` holds three static blueprints, which are
real `InterviewBlueprint` instances rather than a bespoke format.

**Green unit tests have hidden broken features here more than once**, most
recently a counting bug that survived nine attempts at the same subsystem because
every test of it wrote a probe with a question mark that production never emits.

---

## frontend/

Plain HTML, CSS and JavaScript. React and Vite are specified in CLAUDE.md and
were deferred while the shape was still moving. **There is no JavaScript test
harness**, so everything here ships unverified apart from `test_copy_style.py`,
which scans the files for machine-written tells.

Both surfaces are served by `app/main.py`, which substitutes branding tokens on
read. A token that survives substitution appears literally on screen, and a test
asserts none do.

**`frontend/candidate/index.html`** (1173 lines) is the whole candidate journey
in one file: credentials, consent, device check, the call, and the thank-you. The
call screen is two panels, the interviewer on the left and the candidate on the
right, each with its own surface. The orb **breathes rather than spins**, because
rotation is the language of loading and a shape going round in circles for forty
minutes says "still thinking". Captions are an accessibility feature and are the
one thing a candidate reads while answering, which is why nothing is allowed to
overlap them. Below 900px the panels drop out with `display: contents` rather
than `display: none`, because a fixed-position element inside a hidden parent is
not rendered at all and that would take the self-view with it.

**`frontend/admin/index.html`** (231 lines) is the shell and all the styling.
**`frontend/admin/admin.js`** (1335 lines) is the entire panel: profiles, the
clarification chat, candidates, plans, sessions and reports. Every screen has its
own URL in the hash and is reached by an ordinary link, so back, forward, refresh
and bookmarking work. Self-refreshing screens take a ticket that a scheduled
re-render checks before firing, or navigating away from a waiting screen lets its
timer redraw over whatever was opened next. `dealbreakerCard` and the three
health banners render the parts of the report that are about the interview rather
than about the candidate.

**`frontend/assets/`** holds the artwork. The Mitra lockup is transparent so it
sits on any surface. The company logo is transparent too but is dark ink, so the
dark call screen paints it white with a CSS filter, which costs the blue in the
mark. A light-on-dark cut is the only real fix and has not been supplied.
