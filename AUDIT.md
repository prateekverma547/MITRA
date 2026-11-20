# System audit

**Scope.** Every mechanism in the repo, read at `stg` with the working tree as
it stands: five files carry uncommitted work (`brain.py`, `prompting.py`,
`score.py`, `contracts.py`, `test_feedback.py`) plus `CHANGELOG.md`. Nothing was
changed, run against a model, or tidied. Line numbers are as of that tree.

**Method.** Static reading, plus stored artefacts: 16 real interviews in
`backend/interviewer.db` and `backend/sessions/`, and ~300 scripted competency
sections across the comparison arms in the scratchpad. Where a claim rests on
inference rather than observation it says so. Where it needs something run, it
says what.

**One correction up front.** The brief says the plan sums to 1320s against 1200s.
It does not. `allocate_minutes` reserves the opening and closing on its first
line (`blueprint/generate.py:325`) and every blueprint sums exactly to its
duration: 1200/1200, 2400/2400, 3600/3600, and 1200/1200 for the real generated
one. That was checked in the previous task and re-checked here.

---

# Part 1 — Findings, worst first

## S1. `questioning_health` is computed, capped on, and never shown to anyone

**NOT WORKING — the half that faces the user does not exist.**

`QuestioningHealth` is derived (`feedback/health.py:252`), attached to the report
(`shared/contracts.py:774`), passed to the scoring model, and caps the
recommendation. Then it stops. `frontend/admin/admin.js:931-934` reads
`conversation_health` and `judgment_health` and nothing else, under a comment
that says:

> `// Two independent degradations: the recording, and our own analysis of it.`
> `// Both render, in that order, and neither hides the other.`

That comment was true when there were two. The third was added this week and the
panel was never extended. `banner()` (`admin.js:1018`) is already generic over
any health object, so the omission is not structural.

**Consequence.** The whole point of the mechanism was that a recruiter reading a
thin report should see the interviewer repeated itself. The recruiter cannot see
it. The scoring model can, and the recommendation cap fires, so the report is
*fairer* — but the sentence written to explain why is never displayed.

**Verify:** open a completed interview in the panel and look for the third
banner. Inference: I have not run the panel; this is read from the source.

---

## S2. The interviewer fabricates candidate speech from the CV, and it reaches the transcript

**NOT WORKING.**

Measured on the current 10-run set: **7 fabricated attributions across 6 of 10
interviews**, every one traceable to `claims_to_verify` or `candidate_summary`.
Hand-read, with the paraphrase cases excluded.

Worst instance, run 9 turn 4 — the second substantive interviewer turn:

> **Interviewer:** "Okay. You said you built six production voice agents on
> LiveKit Agents. How do you manage deployment on shared Ubuntu VMs?"
>
> **Everything the candidate had said:** "Right now I'm building an AI
> interviewer. It's a voice agent. It reads a candidate CV…"
>
> **`claims_to_verify[0]`:** "Built six production voice agents on LiveKit
> Agents with OpenAI Realtime, deployed on a shared Ubuntu VM…"

The CV bullet, read back as the candidate's speech.

**Where it comes from.** `candidate_context` (`bot/brain/prompting.py:1199-1204`)
renders `ABOUT THIS CANDIDATE` and `Claims from their CV worth testing` in
**every section including the opening**, with **no guard**. The seed block one
level down does carry a guard and it demonstrably worked (0.90 → 0.40, and the
"statistically flat" case is now unattributed). This block never got one.

**Why it is the worst thing here.** `CLAUDE.md` calls fabricating a candidate's
words the single worst failure the product has, and `feedback/score.py` exists
largely to stop it. But the interviewer's fabrications *pass* that check: by
scoring time the sentence really is in the transcript, attributed to the
interviewer.

**Not a guard.** The block's own header invites the failure — "Claims" invites
"you claimed". Restating the material's status ("From their CV. They have not
said any of this here") is a different move from adding a fifth prohibition to a
prompt already four mandatory blocks deep and over its token bound (S4).

---

## S3. `unexplained_terms` on the outcome is written, persisted, and read by nothing

**WRONG PLACE — the record exists and never reaches the reader.**

`Section.unexplained_after_asking` (`bot/brain/state.py`) is the reportable fact
the term machinery exists to produce: *asked twice, never explained*. It is
copied into `SectionOutcome.unexplained_terms` by `to_outcome`.

Then: **zero readers** in `feedback/`, `app/`, or `frontend/`. Confirmed by
grep across all three trees.

`_render_sections` (`feedback/score.py`) hands the scoring model only
`section_id`, `coverage`, `turns`, `declined_turns` and `shortfall_reason`. The
term record is not among them.

**Consequence.** A candidate can be asked the same term twice, fail to explain it
both times, have the interviewer say "let's leave that one there" out loud — and
the report never mentions it. Two weeks of term-probing work terminates in a
field nobody reads.

---

## S4. The prompt is over its own token bound, and the guard test cannot see it

**NOT WORKING — the test asserts the wrong fixture.**

`MAX_SECTION_PROMPT_TOKENS = 2400` (`tests/test_brain.py:365`). Measured
worst-case system instruction:

| fixture | worst case |
| --- | --- |
| `pm_senior` (what the test loads) | under bound |
| **`ml_senior`** | **2445 tokens before this week's dealbreaker block, ~2490 after** |

`test_section_prompt_stays_small_and_bounded` calls `load_blueprint()` with no
argument (`tests/test_brain.py:381`), which resolves to the default fixture. The
fixture that breaches is never exercised.

This is the same shape as the two tests that pinned a false consent notice: the
assertion is real, the input is not representative.

**Note.** The docstring on that test already records being fixed once for
exactly this reason — it was "passing on a prompt it was not rendering". It was
made to populate every optional block, but not to vary the fixture.

---

## S5. `ceiling` decides 91% of sections and nothing downstream can tell

**WRONG PLACE.**

`_advance_reason` returns six reason strings. Only two are ever consumed:

| reason | produced | consumed | fires |
| --- | --- | --- | --- |
| `hard_stop` | yes | `brain.py:1236`, `:1319` | rarely |
| `candidate_disengaged` | yes | `brain.py:1236` | refusals only |
| `declined_topic` | yes | **nowhere** | refusals only |
| `depth_reached` | yes | **nowhere** | 13% of sections (measured) |
| `ceiling` | yes | **nowhere** | **87–91% of sections** |
| `budget_exhausted` | yes | `brain.py:1319` | 0/300 scripted, 2/23 real |
| `closing_delivered` | direct call | **nowhere** | every interview |
| `candidate_withdrew` | direct call | **nowhere** | withdrawals |

`coverage_shortfall` is set only when `reason in ("hard_stop",
"budget_exhausted")` (`brain.py:1319`). Since `ceiling` is not in that set and
`budget_exhausted` never fires in the harness, **`coverage_shortfall` is set on
0 of 120 measured competency sections**. The flag exists, the report contract
carries it, and it is always false.

**Also:** `_render_sections` labels `shortfall_reason` to the scoring model as
`SQUEEZED:` (`feedback/score.py`). Nothing that sets `shortfall_reason` is the
squeeze. The model is being told a section was squeezed when it ran out of time
or the candidate declined.

---

## S6. The squeeze measures against the wrong wall

**WRONG PLACE — the third mechanism this week checked where it can only agree.**

`_rebalance_remaining` (`bot/brain/brain.py:1310`) computes
`time_left = hard_stop_seconds - elapsed_seconds` and returns early if
`available >= planned`. `hard_stop` is duration **plus grace**, while the plan is
built to the duration. The 300s of grace is counted as time available to
competencies, so it is spent silently before the mechanism engages.

Real interview `int_7ffd1be01f56`, at every boundary:

| after | elapsed | available | planned | |
| --- | --- | --- | --- | --- |
| opening | 88.6s | 1291.4s | 960s | no squeeze |
| applied_llm_depth | 309.7s | 1070.3s | 780s | no squeeze |
| evaluation_reasoning | 527.1s | 852.9s | 600s | no squeeze |
| ml_service_deployment | 667.3s | 712.7s | 450s | no squeeze |

The margin never drops below 260s. It has never fired in any interview on record
(`original_budget_seconds` is `None` everywhere).

**Judgement:** this is misplaced but *not* urgent. Firing it at the configured
duration would shrink later sections on nearly every interview, to prevent an
overrun that has cost nothing yet (S12).

---

## S7. `OPENAI_BLUEPRINT_MODEL` drives four things, one of them live

**WRONG PLACE — the name understates the blast radius.**

`settings.blueprint_model` is consumed by:

1. `ClarificationChat` — `app/main.py:178, 234, 634, 840`
2. `BlueprintGenerator` — `scripts/register_run.py:50`, `scripts/build_blueprint.py:146`
3. **`OpenAIJudge` in the live bot** — `bot/run_bot.py:271`
4. telemetry — `bot/run_bot.py:504`

(3) is the one that matters. The judge runs *during* the interview, off the
critical path but shaping the next question. `run_bot.py:501-503` already
acknowledges this in a comment — "a bad value here degrades the interview while
looking like an offline-only setting" — which is the finding written down but
not acted on. An operator setting this to a cheap model to save on blueprint
generation silently changes live interview behaviour.

---

## S8. Cost telemetry does not exist

**NOT WORKING — a documented Definition of Done is unmet.**

`CLAUDE.md:216-217` requires per-interview LLM tokens in/out, TTS characters, STT
minutes and call minutes persisted to the interview record, and makes
"per-interview cost figures readable from the DB" part of the Milestone 3 DoD.

`session_metrics` as actually stored for the real interview contains: `latency_summary`,
`turns`, `silence_events`, `disconnects`, `peak_others`, `present_at_end`,
`brain_events`, `judgments_attempted/succeeded/failed`, `repairs_requested`,
`llm_model`, `judge_model`, `stt_provider`.

**No token, character, minute or cost key.** There is no cost arithmetic anywhere
in the codebase.

The comparison harness *does* track tokens (`interviewer.prompt_tokens`,
`judge.completion_tokens` in `scripts/compare_models.py`), so the plumbing exists
one directory away from where it is needed. Cost per interview is currently
arithmetic done by hand, from scripted runs, against a live path that reports
nothing.

---

## S9. `RECORDING_SWEEP_DISABLED` has inverted-truthiness semantics

**NOT WORKING for any value other than unset.**

Three flags parse through `_TRUTHY` (`bot/config.py:146, 155, 165`). The fourth
does not:

```python
def sweep_enabled() -> bool:            # app/recordings.py:326
    return not os.environ.get(SWEEP_DISABLED_ENV)
```

Bare truthiness on the raw string. `RECORDING_SWEEP_DISABLED=0` and
`=false` both **disable** the sweep, because `not "0"` is `False`. An operator
setting it to `false` to keep the sweep running turns it off.

Recordings are billed by Daily and the sweep is what deletes them.

**Thirty-second fix. Deliberately not made.**

---

## S10. `depth_rationale` — the judge's stated reason — is written and never read

**WRONG PLACE.** Set in `apply_judgment` (`brain.py`), carried on
`SectionOutcome`, and read by nothing in `feedback/`, `app/` or `frontend/`. The
judge explains its coverage verdict in one sentence per section and the sentence
is discarded.

---

## S11. Coverage is overwritten after a section ends

**WRONG PLACE — a value refreshed at the wrong moment, the shape asked about.**

`_advance` queues a `section_end` judgment *after* `_carry_claims_from` has
copied that section's claims into `_carried_claims`. The judge then receives the
section's own claims labelled "claims from earlier sections", re-judges the same
transcript, and `apply_judgment` writes `section.coverage` with **no check that
the section is still open**.

Measured with the `_advance_reason` reorder in the tree: of 8 sections that ended
*because* the judge said `sufficient`, only **2 still say `sufficient`** in the
record. Five say `partial`, one says `insufficient` — including one that ended at
the 2-turn floor and is now written up as a topic the candidate failed.

Pre-existing and harmless while nothing ended on a verdict. The uncommitted
reorder makes it consequential.

---

## S12. Sections overrun by one boundary turn

**RIGHT PLACE, working as designed — recorded so it is not rediscovered.**

The budget is checked at a turn boundary, so a turn in progress completes. In
`int_7ffd1be01f56`: `applied_llm_depth` stood at 175s of 180s after five
exchanges and the sixth ran 36s to 221s; `evaluation_reasoning` stood at 174s and
the seventh ran 33s to 217s. One boundary turn each, explaining all 80 seconds.

**It is one slow interview, not a systemic fault.** Across 16 real interviews and
23 competency sections, **2 sections have ever exceeded budget, both in this
one**, which ran at 30.5s/turn against a 20.0s median. Projected to completion it
would have finished at 20.7 minutes against a configured 20 and a 25-minute wall.

The harness **cannot** reproduce this: at a flat 22s/turn the ceiling always binds
first (8 turns × 22s = 176s of a 180s budget). 0 of 300 scripted sections have
ever gone over. Any fix here is verifiable only on a real interview.

---

## S13. Two validators that cannot fail on things they should catch

**WRONG PLACE (partial).**

The blueprint validator is **fine** and was checked directly — it rejects an
overcommitted plan down to 30 seconds, including the exact "competencies get the
full duration, opening and closing on top" case. It is one of the ones that works.

What it does not catch: `planned > duration` only. A plan summing to **10 of 20
minutes is accepted** (verified). Generated blueprints cannot hit this because
`allocate_minutes` settles drift exactly, but a hand-authored fixture can, and it
would silently hand back a third of the interview.

`EvaluationSpec` has **no validator at all**:
- weights summing to 1.8 → accepted (verified)
- **zero competencies → accepted** (verified)

`CLAUDE.md` requires four to seven competencies and weights summing to 1.0. Both
are enforced only in the clarification prompt and a normalisation step
(`blueprint/clarify.py:275`), not in the contract. Every consumer that divides by
the weight total normalises (`feedback/score.py:666`, the squeeze), so this is
latent rather than live.

---

## S14. `DEFAULT_SECONDS_PER_TURN = 45.0` is a known-wrong default kept alive by every caller overriding it

**RIGHT PLACE, fragile.** `bot/brain/harness.py:25`. The measured figure is 22s
(`SECONDS_PER_COMPETENCY_TURN`), and all seven current callers pass it explicitly
— verified one by one. The 45.0 default is reachable only by a new caller that
forgets, and it is the same wrong clock that invalidated a week of measurement.

---

# Part 2 — Inventory

## `_advance_reason`, in order, with whether each can fire

Order as of the uncommitted tree (`bot/brain/brain.py:1159`):

| # | check | can fire | evidence |
| --- | --- | --- | --- |
| 1 | `hard_stop` | yes, rarely | never in 16 real interviews |
| 2 | `candidate_disengaged` (≥4 refusals) | yes | refusal scenarios only |
| 3 | `declined_topic` (≥2 refusals) | yes | refusal scenarios only |
| 4 | `depth_reached` (verdict + floor) | **yes, 13%** | 8/60 measured after the reorder; **3%** before it |
| 5 | `ceiling` | yes, 87% | 52/60 measured |
| 6 | `budget_exhausted` | scripted **never**; real 2/23 | ceiling binds first at 22s/turn |

Before the uncommitted reorder, (4) sat below (5) and (6) and fired on ~3% of
sections — the flagship "checked where it can only agree" case.

## Environment variables

| variable | controls | measured or chosen | read by |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | all model calls | n/a | `bot/config.py:65` |
| `ELEVENLABS_API_KEY` / `_VOICE_ID` | TTS | n/a | `config.py:66-67` |
| `DAILY_API_KEY` | transport | n/a | `config.py:68` |
| `OPENAI_LLM_MODEL` | live conversation | **measured** (mini vs 4.1 table) | `config.py:68` |
| `OPENAI_BLUEPRINT_MODEL` | clarification, generation, **live judge**, telemetry | chosen | 4 sites — see S7 |
| `OPENAI_FEEDBACK_MODEL` | scoring | chosen | `config.py:70` |
| `DEEPGRAM_API_KEY` | **presence switches STT vendor** | **measured** (1.04s → 0.27s) | `bot/services/stt.py:109` |
| `DATABASE_URL` | Postgres, else SQLite | n/a | `app/db.py:52` |
| `ADMIN_PASSWORD` | panel auth, no default | chosen | `app/auth.py:35` |
| `MAX_CONCURRENT_INTERVIEWS` | capacity cap, default 6 | **partly measured** (memory yes, CPU no) | `app/capacity.py:47` |
| `RECORDING_ENABLED` | recording on | chosen | `config.py:165` |
| `RECORDING_SWEEP_DISABLED` | sweep off — **inverted, see S9** | chosen | `app/recordings.py:327` |
| `FILLER_ENABLED` | spoken filler | chosen | `config.py:155` |
| `TERM_PROBING_DISABLED` | term probes off (inverted polarity, deliberate) | chosen | `config.py:146` |
| `DEFAULT_TIMEZONE` | greeting fallback | chosen | `bot/greeting.py:35` |
| `LOG_LEVEL` | logging | n/a | `bot/run_bot.py:585` |
| `RAILWAY_*` | environment detection | n/a | `app/db.py:32-34` |

No constant in the brain, feedback or blueprint layers is unreferenced — all 22
checked have live consumers.

## Constants whose value is measured vs chosen

**Measured:** `SECONDS_PER_COMPETENCY_TURN = 22` (21.6s median, nine sessions;
re-confirmed here at 20.0s median over 16), `REPEAT_OVERLAP = 0.6`,
`CONFIDENT_SIGNAL_REQUIRES_WEIGHT = 0.75`, `MAX_TERM_PROBES_PER_INTERVIEW = 8`
(6.7 observed), `ECHO_RUN_FRACTION`, `FRAGMENT_WORDS`.

**Chosen and defensible:** `MAX_TERM_ATTEMPTS = 2`, `MAX_REPAIR_ATTEMPTS = 2`,
`MAX_CLOSING_TURNS = 2`, `MIN_COMPETENCY_TURNS = 6`, `MIN_SECTION_MINUTES = 3.0`,
`MAX_VOCABULARY = 12`, `_MAX_CLAIM_CHARS = 110`, `MAX_REFLECTION_CHARS = 70`.

**Chosen, and the one worth revisiting:** `OPENING_MINUTES = CLOSING_MINUTES =
2.0`. Fixed, so they are **20% of a 20-minute interview and 4% of a 90-minute
one**. At the minimum duration with six competencies the generator already logs
that each competency gets 2.7 minutes, below its own 3.0 floor.

## Report contract: populated and rendered

| field | populated | rendered in panel |
| --- | --- | --- |
| `competency_scores` | yes | yes |
| `red_flags_observed` | yes | yes (`admin.js:977`) |
| `contradictions` | yes | yes |
| `summary` | yes | yes |
| `recommendation` | yes | yes |
| `coverage_gaps` | yes | yes (`admin.js:998`) |
| `conversation_health` | yes | yes (`admin.js:931`) |
| `judgment_health` | yes | yes (`admin.js:934`) |
| **`questioning_health`** | yes | **no — S1** |
| **`dealbreakers`** | yes (uncommitted) | **no** |
| **`interview_duration_seconds`** | yes | **no** |

`SectionOutcome.unexplained_terms`, `.coverage_shortfall` and `.depth_rationale`
reach neither the scorer nor the panel (S3, S5, S10).

## Documentation drift

**Documented, not implemented:** cost telemetry and its Milestone 3 DoD
(`CLAUDE.md:216-217`) — S8.

**Implemented, not documented in DECISIONS.md:** the interview-wide term probe
cap; `QuestioningHealth`; the seed rotation and its CV guard; the
`_advance_reason` reorder; `DealbreakerFinding`. All are in `CHANGELOG.md`; none
have entries in `docs/DECISIONS.md`, which is where measured constants are
supposed to live.

**Stale comment:** `admin.js:931-933` says "Two independent degradations"; there
are three.

**Accurate:** every number spot-checked in `DECISIONS.md` (the STT table, the
compliance table, the turn-cost table) matches what the code and stored runs say.

## Cost of an interview, by component

| component | what is recorded | status |
| --- | --- | --- |
| live LLM tokens | nothing on the live path | **arithmetic only**, from `compare_models` |
| judge tokens | nothing on the live path | **arithmetic only** |
| feedback tokens | nothing | **arithmetic only** |
| TTS characters | nothing | **not measured** |
| STT minutes | nothing (provider name only) | **not measured** |
| Daily call minutes | nothing | **not measured** |
| recording storage | `recording_bytes` on the interview row | **measured** |
| latency | `latency_summary`, per-turn | **measured** |

From the scripted arms, a 20-minute competent interview costs roughly 98k live
prompt tokens + ~1k completion, and ~95k judge prompt + ~9k completion. That is
the only per-interview figure available and it comes from the harness, not from
a real session.

---

# What I could not assess

- **The panel, the API surface and the recording path end to end.** Read
  statically only. Confirming S1 and S9 behaviourally needs the app running.
- **Live turn-taking, VAD, smart-turn endpointing.** `CLAUDE.md` says these are
  judged by ear and there is no stored artefact that captures them.
- **Whether `gpt-realtime`-specific behaviour differs from the text path.** Out
  of reach without a voice session.
- **Blueprint generation quality.** Would need model calls, which were excluded.
- **Whether the 7 fabrications reproduce at other temperatures or on `gpt-4.1`.**
  Would need `scripts/compare_models.py --models gpt-4.1 --scenarios competent`.

