# The interview brain

`backend/bot/brain/` is the part of this system that is actually novel, and the
part most likely to be broken by a well-meaning change. Read this before
touching it.

---

## What it is, and what it is not

**The brain is a context planner and a state machine. It is not a text
generator.**

That distinction is the whole design, and it is easy to misread. "Text in, text
out" does not mean the brain produces the interviewer's words. It means:

- `plan_turn()` assembles **what the model sees** this turn: which section we are
  in, the persona for it, the claims carried from earlier, the recent verbatim
  exchange.
- `observe()` records what was said and decides **whether to move on**.
- **Generation stays in the streaming pipeline.** The LLM writes the words and
  they stream token by token into TTS.

If the brain returned finished text, the first token could not reach the voice
until the whole response existed, roughly doubling time to first audio. The
brain shapes *context*; it never intercepts the *response*.

**It is pure and synchronous.** It never awaits, never performs I/O, never
blocks a spoken turn. `bot/brain/` does not import Pipecat at all. That is what
makes the text-mode harness possible: the same object, driven by a different
loop.

---

## The shape of an interview

`build_sections()` turns a blueprint into an ordered list:

```
opening -> competency -> competency -> ... -> closing
```

Each `Section` carries its own time budget, turn counts, coverage level, claims
and contradictions. The brain walks them in order and never goes back.

```python
brain = InterviewBrain(blueprint, time_of_day="afternoon")

plan = brain.plan_turn()          # what the model should see now
brain.observe(bot_text=..., candidate_text=...)   # what actually happened
brain.outcomes()                  # SectionOutcome per section, for the report
```

### Section-scoped context

`TurnPlan.messages` holds **only the current section's recent turns**, not the
whole interview. Cross-section memory arrives as *carried claims* in the system
instruction instead.

This is why a mini-tier model is viable on the live path. The prompt stays flat
at roughly 800-1700 tokens and resets each section, where the Milestone 1 design
sent ~1800 tokens of prompt plus an ever-growing chat log that reached ~10,000
by the end of a forty-minute interview.

`tests/test_brain.py::test_section_prompt_stays_small_and_bounded` guards it.
If that bound needs raising again, the honest question is what to cut.

---

## The invariants

Break one of these and the failure will be subtle and about a real person.

### 1. Never claim history the model cannot see

At a section boundary the verbatim window is empty. Telling the model to
"continue naturally" there made it **invent the conversation**: a live session
opened a section with *"you mentioned earlier that you had a hand in shaping a
key product strategy"* to a candidate whose only words had been "I don't want
to."

The fix was to hand it the truth rather than an instruction. The plan carries
**one bridging exchange** from the previous section, and the prompt states
plainly when the candidate has not yet said anything substantive.

Fabricating a candidate's words is the worst failure this product has. It puts
invented claims into an auditable hiring record.

### 2. Judgement never blocks a spoken turn

Depth is decided by heuristics, a floor and ceiling of turns per section, with
an off-path LLM judgement refining within that band. The judgement is *requested*
by the brain (`pending_judgment_request()`) and *delivered* by its driver
(`apply_judgment()`). If a verdict has not arrived, heuristics rule that turn.

Nothing in the brain awaits anything. Ever.

### 3. Declining is not the same as answering badly

`declined_turns` is recorded separately from low coverage. "Declined to answer"
and "answered shallowly" say different things about a person, and only one is
about their ability. A report must never blur them.

The same principle extends to two more cases, both added later after live
sessions:

- **Could not be heard** is a third thing again, and was being scored as the
  second. See `ConversationHealth`.
- **Chose to end the interview** is a decision, not a failure, and must never be
  written up as running out of time.

### 4. Coverage must never flatter

A live session recorded the opening as `sufficient` for a candidate who refused
to speak. Non-competency sections now report `insufficient` when nothing
substantive was said.

### 5. Contradictions are recorded always, probed at most once

Neutrally phrased, curious rather than prosecutorial. The bot never voices a
verdict. Precision is weighted above recall: a false contradiction becomes a
written claim about a real person's honesty, while a missed one can still be
caught by the human reading the transcript.

---

## Deterministic detectors

Four things are detected in plain Python rather than by asking a model, and they
share a design: **match the whole utterance, and prefer to miss rather than
misfire.**

| module | detects | the error that matters |
|---|---|---|
| `refusal.py` | "I'd rather not answer that" | reading a real answer as a refusal |
| `repair.py` | "sorry, what?" vs "what do you mean?" | throwing away an answer |
| `withdrawal.py` | "just end this interview" | ending an interview someone wanted |

Whole-utterance matching is the safety property. "No" is a refusal; "no, that's
not how it went, what we did was…" is an answer, and confusing the two would
have the bot abandon a topic the candidate was engaging with.

**Withdrawal is the exception, and the exception is instructive.** Whole-utterance
matching failed live against everything a person actually says, and even
rewritten to search inside the utterance it catches none of "can we wrap this
up", "this isn't for me", "bas, ab band karo". Sixteen out of sixteen natural
phrasings missed. A phrase list only ever contains what somebody thought of in
advance.

So withdrawal is now **semantic**: the live model has an `end_interview` tool it
calls itself, and the patterns remain as an instant path. The two corroborate,
because the model has recall and the patterns have precision. Both agree and the
interview ends at once; only the model agrees and the candidate is asked once.
See `bot/tools.py`.

### The three repairs

Conflating these is what makes a voice bot feel stupid:

```
"sorry, what?"        they did not hear it     -> say the SAME thing again
"what do you mean?"   they did not follow it   -> say it in SIMPLER words
"I don't know"        they heard and followed  -> that is an ANSWER
```

The third is the one to be careful about. It is neither confusion nor refusal,
and treating it as either punishes candour or loops forever.

---

## Writing prompts here

`prompting.py` assembles the system instruction per turn. Three lessons are
baked into how it is written, and all three were measured.

**Structure beats emphasis.** An instruction that leaves the natural alternative
available gets ignored in favour of it. The contradiction callback went 0% → 80%
→ 100% compliance across three phrasings, and what finally worked was *removing
the alternative* and supplying the sentence, not asking more firmly.

**Mandatory blocks go at the top and forbid the alternative explicitly.** See
the contradiction block, the repair blocks, and `OFFER_TO_STOP`. They read
strangely if you expect polite prose. They are written that way on purpose.

**Prompt examples teach habits.** A sample question containing an em dash
produced em dashes in the transcript. What the examples do, the model does.

Re-measure after editing any of these:

```bash
PYTHONPATH=. uv run python scripts/contradiction_rate.py
PYTHONPATH=. uv run python scripts/compare_models.py
```

---

## Testing the brain

The harness runs a scripted candidate through a full interview with no audio at
all (`brain/harness.py`). Scripted candidates exist for contradiction, thin
answers, refusal, off-topic, being unheard, and withdrawal.

```python
run = await run_interview(brain, interviewer=..., candidate=thin_answer_candidate())
```

Two suites, never mixed:

- **deterministic**, fake LLM, fast, CI-gating. Transitions, budget arithmetic,
  carryover, `SectionOutcome` construction.
- **tolerant**, real model calls, slower, run deliberately with
  `RUN_BEHAVIOR_TESTS=1`. Contradiction detection, thin-answer probing,
  off-topic redirect, instruction adherence.

And one rule learned the hard way: **if a change affects how the bot behaves,
run a real conversation through it.** Green unit tests have hidden broken
features here repeatedly, because the fixtures agreed with the same wrong
assumption the code did.
