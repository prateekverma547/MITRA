# Changelog

A running record of every change made to this codebase, newest first.

The point is that anyone can see what was touched and why without reading
diffs. Each entry says what changed in plain language, what problem it solved,
which files moved, what was deliberately left undone, and the exact commands to
confirm it works.

Write for someone who has never seen this repository. Keep jargon out of
**What** and **Why**; file names belong in **How**.

---

## 2026-08-11 — The list of what the candidate said earlier had become a list of things to ask about

**What**

The interviewer was handed, on every single turn, a list of things the candidate
had said in earlier parts of the interview. It now gets that list only before
they have answered anything in the current topic. After that it has their actual
words in front of it and does not need a summary of older ones.

**Why, and this corrects five earlier diagnoses**

Five times this month a failure was written up here as the model imitating its
own earlier turns. That was wrong, or at best half of it. A replay of one stored
turn, five samples per cell, settles it:

| | its own turns in front of it | its own turns removed |
| --- | --- | --- |
| **the list is shown** | **5/5 repeat the same probe** | 0/5 repeat |
| **the list is not shown** | 0/5 repeat | 0/5 repeat |

Only the shipped combination fails, and the two halves do different jobs. The
list supplies **the target**: it is frozen for the whole of a topic, so an
instruction to ask about "a technique they named without saying what it does"
reads off the standing list rather than off the answer, and picks the same term
every turn. Its own earlier turns supply **the licence** to keep doing it.

**"Walk me through how that actually works" was never the problem.** In the same
replay, with its own turns removed, the model still said that sentence, about
the thing the candidate had just been talking about, which is exactly the
question that topic existed to ask. The failure was never the sentence. It was
asking it about something from twenty minutes ago.

That also explains four things previously blamed on imitation: probes exceeding
the two-per-topic allowance, the drift where later probes had no instruction
behind them, the jump to eight probes when the turn ceiling rose, and the
reordering that did not help. All four are one unmetered instruction pointed at
a frozen list that the two-probe counter never sees and the reordering never
touched.

**How**

`backend/bot/brain/prompting.py`, one condition at one call site. The block
renders when the topic is not a competency, or when the candidate has not yet
answered in it.

**The boundary is "they have not answered yet", not "nothing has happened yet".**
Those differ when the interviewer has spoken and the candidate has not replied,
which is what a silence nudge or a repair looks like. There the model still has
none of their words for this topic and the risk the block exists for is exactly
what it was a moment earlier, so the block keeps rendering. The counter used
counts answers, so it stays put through that.

Nothing else changed: not the block's wording, not what goes into it, not how it
is carried between topics, not the term block, the caps or the judge.

**What this gives up**

Late in a topic the interviewer can no longer call back to something from an
earlier one. That is a real loss and it is accepted, because the risk the block
was built for is at the boundary, where it still renders.

**In the runs it turned out to be smaller than expected**, and that is worth
recording rather than claiming as a win. Cross-topic callbacks did not stop:
17 of 62 questions still referred to something said in an earlier topic. Two
other paths carry that material and neither was touched, the unexplained-term
block and the closing, which is not a competency and so is unaffected.

**Measured**

Competent-candidate scenario, three runs, `gpt-4.1-mini`, before and after.

**The bar was set in advance and it did not hold.** It was: the evaluation topic
asks at least one of its four planned questions in at least two of three runs.
It asked **zero of four in three of three, before and after. No movement at
all.**

**The bar was the wrong thing to hold this change to, and that was established
afterwards rather than argued away here.** It is left standing above because a
failed bar should not be quietly deleted, but it measured something the system
does not want:

- `shared/contracts.py` defines seed questions as *"Openers. The brain adapts
  and follows up; it does not read these verbatim."*
- There are two consumers. The prompt renders them as *"questions you may draw
  on… do not work through them in order"*, and the turn ceiling uses them as a
  unit of budget, `3 × seed_questions + MAX_TERM_PROBES_PER_SECTION`.
- The judge scores coverage against `target_depth`. The report scores against
  the spec, with verified quotes. **Neither has ever seen a seed question.** No
  consumer in this system treats an unasked seed as a gap.

**And the count itself is unsound.** Hand-checked across all 144 seed/section/run
pairs in these six runs: a person finds **55 asked where the matcher finds 45**,
an 18 percent undercount, mostly from having no stemming. Worse, three of its 45
are false positives, and the mechanism matters: it scores the whole turn, and a
turn is an acknowledgement followed by a question. One topic asked *"How did you
explain the observability stack to a non-technical client?"* three times in a row
with different acknowledgements in front of it, and scored **3 of 4**, because
the restatements shared words with two other seeds. **The metric rewarded an
interviewer that was stuck.** It is renamed `seed_overlap_rough` and is a
diagnostic from here on, never a bar.

What did move, in the same topic:

| | before | after |
| --- | --- | --- |
| questions about something from an earlier topic | 2, 4, 4 | **1, 1, 1** |
| and all three of those are | | the topic's first turn, where the block still renders by design |

The repetition is gone. Before, the same stale term was put to the candidate
four and five times running. After, the question follows the answer: in every
run it now reaches KL divergence, the candidate's strongest evaluation material,
which it never once reached before.

Across the whole interview, questions quoting the current topic against
questions quoting an earlier one: **39 vs 20 before, 45 vs 17 after.**

Other numbers, before to after:

| | before | after |
| --- | --- | --- |
| planned questions asked | 7, 8, 10 of 24 | 6, 9, 5 of 24 |
| topics with a coverage shortfall | 1, 2, 0 | **0, 1, 0** |
| sanctioned term probes | 8 | 11 |
| median interviewer turn | 15, 20, 16 words | **15, 16, 15** |
| live prompt tokens per run | 75.1k, 74.9k, 71.7k | **69.7k, 69.8k, 67.3k** |

**Nothing invented.** This was the risk worth checking, because it is what the
block exists to prevent. Every "you said X" in all six runs was matched against
everything the candidate actually said: **one unsupported attribution before the
change, none after.** The one before was a paraphrase rather than an invention
("sharing a GPU caused risks" for "they share a GPU, so a job that takes more
memory than expected is felt by all of them").

**Two things moved the wrong way and neither is dismissed as noise.**

- **The let-go is spoken less.** Three queued and one said before; five queued
  and **none said** after. Both are bad and the numbers are too small to tell a
  real regression from sampling. It needs its own measurement.
- **One mechanism question appeared in the communication topic**, taking it from
  9 against 0 across three runs to 8 against 1. The section-aware wording itself
  still works: both sanctioned probes in that topic asked how they explained it.
  The stray one came from the depth ramp after the allowance was spent, and the
  depth ramp is not section-aware. That gap predates this change.

**Latency: no measurable effect.** Time to first token, streaming, eight samples
per arm, interleaved: 749ms median with the block, 727ms without, a 22ms
difference against ranges of 651-1164 and 642-1118. Directionally favourable and
inside the noise. The prompt is 355 tokens smaller on the turns where it is
suppressed, about 160 tokens per call averaged over a run.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 677 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent \
        --models gpt-4.1-mini --runs 3

**The verdict, on measures that are not the failed bar**

| | before | after |
| --- | --- | --- |
| stale-term probes in the evaluation topic | 10 | **3** |
| questions quoting the current topic vs an earlier one | 39 vs 20 | **45 vs 17** |
| unsupported attributions | 1 | **0** |
| prompt tokens | — | **−6.8%**, latency unchanged |
| claims reached, coverage, let-gos | — | flat or slightly worse |

**A specific defect removed, nothing measurable cost, and no outcome measure
moved. A change worth keeping, and a modest one.** It is not an improvement to
interview quality and should not be recorded as one: claims reached went 15/18
to 14/18, coverage flattened rather than rose, and the let-go went from 1 spoken
of 3 to 0 of 5. What it did do is stop the interviewer putting the same stale
term to a candidate five times running, which is what it was built for.

**Not done**

- The evaluation topic still asks none of its four planned questions. The
  repetition it was stuck in is gone; what replaced it is a chain of follow-ups
  on the answer, which is better and is still not the plan.
- Removing the interviewer's own turns from the window. It fixes the failure in
  every single-turn test and removes the only record of what it has already
  asked, and a replay cannot tell whether that is safe across a sequence.
- `verbatim_turns`. It truncated 0 of 92 turns, so changing it changes nothing.
- The depth ramp's example, the term block, the caps and the judge.
- The judge still nominates terms from earlier topics, so a stale term can reach
  the candidate through the term block even now. Langfuse was probed twice in
  the communication topic in one run this way.

---

## 2026-08-11 — The section's own questions now come before the term block, and it did not help

**What**

The prompt used to put the mandatory "ask about this unexplained term" block
above the section's purpose and its seed questions. The section now comes first.

**The measurement does not support the change.** Seed questions asked did not go
up, and on one scenario went down. It is recorded here as a result rather than
an improvement, and the numbers are below so the next person does not repeat it.

**Why it was tried**

The same conclusion was reached twice from independent evidence. The term block
is mandatory, opens with "Do not choose your own question this turn", forbids the
alternatives by name, and sat at roughly character 3,354. The section's purpose
sat at 5,386, and its questions were introduced with "Questions you may draw on…
do not read them out verbatim, and do not work through them in order."

A mandatory instruction that names one item and supplies the sentence beats a
list the prompt itself calls optional, and every case in this project has been
decided that way.

The cost, on a real interview with a senior ML engineer: **4 of 12 seed
questions asked**, and 1 of 4 in `applied_llm_depth`. The three that named the
work were never asked. The interviewer spent the section asking him to define
"LLM" and "fine tuning" while the choice of Sarvam-M over GPT-4o, the LoRA setup
and the GPTQ quantisation tradeoff sat unused in the same prompt.

**And there is a circularity, which is why this looked worth doing.** The judge
can only report on words that were said. Sarvam-M, LoRA, GPTQ and 97.08 percent
never appeared in its term list because they never appeared in the transcript,
and they never appeared in the transcript because the seeds that would have
elicited them were never asked.

**How**

`bot/brain/prompting.py`, the assembly order in `render_section_prompt`.

**Something beyond the order had to change, and it matters.** The mandatory slot
handled contradictions and unexplained terms together, and the contradiction
block's high placement is measured: below the seed questions its compliance fell
to 27%, against 80% from up there. Moving the block wholesale would have demoted
the contradiction into exactly the position already measured at 27%, trading a
measured result for an unmeasured one without saying so.

So the slot is split. `_decided_question_early` renders a contradiction above the
section; `_decided_question_late` renders a term or a let-go below it, and
returns nothing when a contradiction has already taken the slot, so at most one
block still decides the turn.

No wording changed. Token count is unchanged: 1912 for a competency prompt with
a term open, 2341 worst case against the 2400 bound.

**Measured, three runs each, gpt-4.1-mini**

| | seeds asked, before | after |
| --- | --- | --- |
| deflecting | 12, 13, 13 of 24 | 10, 14, 12 |
| jargon | 16, 15, 15 of 24 | 12, 12, 16 |

Term probes went **up**, not down: `probe_mechanism` on deflecting was 7, 5, 9
before and 9, 16, 15 after. That signal has swung far more than this between
identical runs, so it is probably noise, but it is certainly not the improvement
the change was made to produce.

**Not done, and the honest reading**

- **The bar was not met.** Seeds asked did not rise. Whether the reordering is
  keeping or hurting is not resolved by this measurement, only that it is not
  helping on these scenarios.
- **The scenarios may be incapable of showing it.** `deflecting` and `jargon`
  both feature candidates designed to emit an unexplained term in every answer,
  so the term block fires every turn wherever it sits. The failure this change
  targets needs a candidate who answers substantively in field vocabulary, which
  is the real ML interview and not any script that exists.
- **The signal I proposed for this does not work**, and that is worth recording
  because it was my own design. Counting probed terms that overlap the seed
  questions cannot separate "LLM" from "Sarvam-M", because the seed questions are
  written in the field's vocabulary too. Scored against the real interview it
  called `agent`, `LLM do the reasoning` and `fine tuning` specific, and `KL
  divergence` generic, which is exactly backwards.
- Giving the judge the seed questions, and the specificity test, are both still
  held.

## 2026-08-11 — The panel reported a 2.6x latency regression that had not happened

**What**

The session page said "Median response 5.27s". It now says how fast the
interviewer was when it was ready to answer, how often it waited before
answering, and the overall figure. The word median is gone, because the number
was not describing what the label claimed.

Every interview already recorded carries the new view, computed from samples
that were always stored.

**Why**

**Time to first audio has two modes and almost nothing in between.** A turn ends
one of two ways: the smart-turn model was confident, and the interviewer answers
in about 1.7 seconds, or it was unsure and waits out the four second fallback,
answering in about 5.4. Across five real interviews:

| interview | ready | waiting | share waiting | median | mean |
| --- | --- | --- | --- | --- | --- |
| int_edd2a30a5ea7 | 1693ms | 4808ms | 36% | 1865 | 2934 |
| int_c9e7d27bd702 | 1570ms | 5200ms | 43% | 1931 | 2950 |
| int_0603d5dfc7d5 | 1815ms | 5404ms | 36% | 1914 | 3125 |
| int_3edf8d579d2c | 1611ms | 5398ms | 47% | 2044 | 3479 |
| int_7ffd1be01f56 | **1692ms** | **5469ms** | **64%** | **5272** | 4330 |

**Both modes are identical in all five.** Nothing got slower. What changed is
the mix, and it changed because a different candidate sat down: median
`model_probability` 0.765 against 0.85 to 0.92, and the fallback fired on 64% of
turns instead of 36 to 47.

Because that crosses one half, the median stepped from one mode to the other in
a single jump. 2.04s became 5.27s and the system was untouched. **A session of
work went into diagnosing a regression that had not occurred**, and the thing
that eventually settled it was that the fourteen fallback values spanned four
milliseconds, which is a timer expiring rather than a loaded event loop.

The mean tells the truth the median cannot: 3479 to 4330ms, +24%. Real, felt by
the candidate, and a quarter of what was reported.

**How**

`bot/observers.py`. `bimodal_latency()` is a pure function over the per-turn
sample dicts, so it can be computed when a session ends and again when an old
one is read back, and the two cannot drift. It returns the mean, the median
within each mode, the count of waiting turns, and the share.

The cut between the modes is `SMART_TURN_STOP_SECS * 0.9`, taken from the
constant rather than picked. The gap it sits in is enormous: measured ready
turns run 490 to 602ms and fallback turns 4197 to 4202ms, so nothing lands near
it.

`app/interviews.py` fills the fields in on read for interviews recorded before
they existed. `latency_summary` is computed at write time, so those five would
otherwise show nothing; their per-turn samples were always stored, so it is
derivable rather than lost. Nothing is written back.

`frontend/admin/admin.js`. The line reads
*"1.69s when ready, 4.33s overall"* with *"waited to be sure on 64% of turns,
5.47s on those"* underneath. The first number is the system, the second is the
candidate, and the old line could not tell them apart. A session that never
waits shows one number and no second line.

**Also added, because it was cheap and the next version of this question will
need it:** per-turn `prompt_tokens`, captured from the usage metrics the
observer already receives. A growing prompt and a busy event loop look identical
in a latency number, and this time it took a lucky observation about timer
precision to separate them. It is an observer, so nothing on the spoken path
changed and no new measurement was added.

**Not done**

- `SMART_TURN_STOP_SECS` is untouched. It is the largest real latency in the
  product and a deliberate trade, and this candidate's `false_turn_end_rate` was
  0.07, the lowest of the five: the four seconds bought exactly what they are
  there to buy.
- The fallback share is now visible but nothing acts on it. If it turns out that
  some candidates are systematically waited for, the answer is a listening
  session, not a smaller timeout.
- `prompt_tokens` is recorded and not yet reported anywhere. It is evidence for
  the next investigation, not a number for the panel.

## 2026-08-05 — A section's turn ceiling follows the time it was given

**What**

A competency's maximum number of questions is now derived from its own time
allocation instead of being a flat six for every section. A twelve minute
competency gets fourteen questions; a four minute one gets ten.

**Why**

**Of the three ways a competency section can end, one fired always and two had
never fired at all.** Across twenty competency sections in fifteen stored
interviews:

| ended by | sections |
| --- | --- |
| turn ceiling | **14** |
| time budget | **0** |
| the judge saying coverage was sufficient | **0** |

Sections used 2% to 58% of the time they were allocated, median 17%. Interviews
ran at **18 to 26% of their configured length**.

That is not a tuning preference. The employer sets twenty minutes, `allocate_minutes`
divides those twenty minutes across the competencies, the panel shows the number,
and **the bot says "about twenty minutes" out loud to the candidate** before
ending the interview after five. From the candidate's side an interview simply
stops, and the reasonable conclusion is that the clock ran out.

`ceiling_turns = 6` had no entry in `DECISIONS.md`. Its only justification was a
field comment: an over-running section "is eating another competency's time". No
section has ever eaten another's time, because the budget has never fired. And
the case a ceiling really does guard, a section that will not converge, is now
held by the two-probe term allowance and the let-go built yesterday. The ceiling
was charging every well-going section six turns to guard something already
guarded.

**How**

`SECONDS_PER_COMPETENCY_TURN = 22`, measured. Seconds per competency turn under
the current code: median 21.6, mean 23.4, p10 18.0, p90 32.9, over twelve
sections. The eight sections from before the turn-counting fix read 7.0s median
and are excluded, because that is the bug where fifty-two utterances counted as
twelve turns. **An earlier note in this changelog put the figure at ninety
seconds; that was wrong and this is the measurement.**

The floor is the old flat six, so no section can get fewer questions than it does
today. The cap is the material the blueprint actually planned:
`3 × seed_questions + MAX_TERM_PROBES_PER_SECTION`, since generation asks for
"3 or 4 questions" per competency and each carries the depth ramp's three stages.
It bounds a wrong allocation rather than a long conversation.

`ceiling_turns` becomes `int | None`. None derives it; an explicit integer still
pins it, which is how the deterministic tests drive a section to its end in two
turns. Every existing test kept working unchanged.

**Measured**

On the scripted runs, what ends a section:

| | before | after |
| --- | --- | --- |
| ceiling | all of them | **0** |
| budget | 0 | **30** |
| other | | 6 |

**The squeeze ran for the first time.** `coverage_shortfall` had never been set
in fifteen interviews; it now fires, which means the over-run policy is live
rather than dormant code. Seed questions asked went from 3 to 5 of 24, to
**11 to 15 of 24**. Sections reached went from 3 to 7.

Cost, from observed token counts: **$0.088 to $0.214** per run at
`gpt-4.1-mini` live and `gpt-4.1` for the judge. That is 2.4x on 2.5x the turns,
so **per-turn cost is flat**: $0.00078 to $0.00073 per live call, $0.00327 to
$0.00298 per judge call. Nothing per turn got slower or dearer; there are simply
more turns. No latency change, and none was expected: the ceiling is a counter,
not a call.

**Not done**

- **In production this will not flip the budget on for large sections.**
  Projecting the rule onto the four most recent real interviews, `technical_depth`
  goes from 6 to 14 turns and about 113s to 264s, against a 720s budget, so it
  still ends on the ceiling. The scripted runs flip because the harness charges
  40s a turn against the measured 22s. The real interview would go from 4.9
  minutes to roughly ten, of a configured twenty.
- **The blueprint does not plan enough material for the time it allocates.**
  `allocate_minutes` gives a competency twelve minutes; generation gives it four
  seed questions. Those two numbers have never been reconciled, and the cap is
  now where they meet. That is the next constraint, not this one.
- **The longer ceiling gives the imitation effect more room.** One deflecting run
  reached eight term probes in a single section against an allowance of two. The
  sanctioned count still holds at two; these are the unsanctioned probes the
  model copies from its own earlier turns, and a fourteen-turn section simply has
  more turns for them to happen in.
- `_render_time_pressure` still has not rendered, and the ordering of checks in
  `_advance_reason` is untouched. Both were deliberately left to be observed now
  that sections run longer.

## 2026-08-05 — A communication section asks about communicating

**What**

When the interviewer probes a term the candidate has not explained, the question
it asks now depends on what the section is assessing.

    technical:      "You said auto scaling. Walk me through how that actually works."
    communication:  "You said auto scaling. How did you explain that to them?"

Same term, same caps, same judge. One branch, chosen from what the section
already says about itself.

**Why**

In a `stakeholder_communication` section, **five of six turns probed the
mechanics of terms inside the candidate's analogy.** Not one asked about the
communication: who the stakeholder was, whether they followed it, what changed
afterwards. One of four seed questions was asked.

The employer named two dealbreakers for this role. "Cannot explain payments
architecture beyond a surface level" was assessed twice, once in the section for
it and again here. **"Struggles to explain reasoning clearly to non-technical
partners" was never assessed at all**, and the report then correctly recorded
that there was no evidence of it.

The cause is the shape difference that has decided every case today, and here it
is also a matter of position. In the rendered prompt the term block is
**mandatory, opens with "Do not choose your own question this turn", forbids the
alternatives by name, and appears at character 3,354**. The section's own purpose
appears at character 5,386, two thousand characters later, as descriptive prose
under "What a sufficient answer looks like" — and its questions are introduced
with **"Questions you may draw on... do not work through them in order"**, which
is explicitly permissive. A mandatory instruction naming one item beat an
optional list. It was never going to go the other way.

**How**

`bot/brain/prompting.py`, one branch, keyed on **the audience rather than the
act**. That distinction is the whole of it: "explain" appears in both
competencies on the real blueprint, in "able to explain in detail the
architecture and flows" and in "able to explain technical concepts to
non-technical stakeholders". Keying on the act fires on both. Only one of them
names who is being spoken to.

So a fixed vocabulary of words for the listener, read once out of the section's
own `target_depth`. **This is not matching a term to a topic**, which is the
shape that has misfired here three times: nothing is compared to the term, term
selection is untouched, there is no score and no threshold. It **fails towards
the technical sentence**, which is the existing behaviour, whenever it finds
nothing. Its failure mode is a technical competency whose stated depth happens to
mention a stakeholder, which asks a mildly wrong question about the candidate's
own term rather than a question about the wrong thing.

No new field was added. `target_depth` already carries this, written per
competency by blueprint generation.

The escalated wording and the let-go take the same branch, since "you have said
X twice now, what does it actually do" is the technical escalation and would be
equally off-topic. The prompt came in at 2341 tokens against the 2400 bound.

`scripts/compare_models.py` gains the two signals that would have caught this:
how many of a section's seed questions were asked at all, and in a
communication-shaped section how many turns ask about the communication rather
than the mechanism of a term. Seed matching is approximate and one turn counts
for at most one seed, because the prompt tells the interviewer to adapt these
rather than read them out.

**Measured**

Communication-shaped section, three runs per scenario, before and after:

| | before | after |
| --- | --- | --- |
| deflecting | 2v2, 2v1, 1v4 | **3v0, 3v0, 4v0** |
| jargon | 1v4, 3v1, 0v1 | **3v0, 4v1, 4v0** |

Mechanism questions in that section have effectively stopped. On the real stored
interview the same measurement reads **1 seed of 4, and 1 communication question
against 4 mechanism questions**, which is the harm this was built against.

**Not done**

- **This shapes the sanctioned probes and does nothing about imitation.** The
  model copies the shape of its own earlier turns out of the verbatim window,
  and **17 of 55 probes across six runs still exceed the two-per-section
  allowance**, firing with no block in front of the model. That effect has now
  decided three separate problems and no prompt instruction addresses it.
- Seed coverage improved but is not the point of this change and did not clear
  the bar everywhere: 1 of 4 in one run of six.
- Moving the section block above the term block is still on the list and was
  deliberately left out, so this measurement is attributable.
- The soft paraphrase problem is unchanged.

## 2026-08-05 — The acknowledgement is switched off, and the judge lets go of a term

**What**

The interviewer no longer says anything while it composes a question. The code
stays, behind a flag that is off.

And the judge now drops a term once the candidate has explained it, which it had
never done.

**Why**

**The acknowledgement was built to cover a gap that does not exist.** The twelve
seconds it was meant to fill were an artefact: transcript timestamps mark when a
turn *finished*, so the interval between two of them is the second one being
spoken. Composition never exceeded 1943ms.

What it did instead, on one real interview:

- **It interrupted.** At 83.5s the candidate began "The session management" and
  at 83.7s, two tenths of a second later, the interviewer spoke over them.
- **It was stale.** At 120s and again at 174s it reflected back a phrase the
  candidate had used ninety seconds earlier, while they were talking about
  something else.
- **It cost 2.5 to 2.8 seconds** on every turn it appeared on.

And then it reached the report. The candidate's answer, cut in half, arrived as
eight fragments. `stakeholder_communication` scored **2.0 out of 5**, quoting
"manages", "those people", "per sessions", "gets their own session" and "the
concurrency" — five pieces of the one sentence we talked over. **The interviewer
interrupted the candidate and the report then marked them down for
communicating in fragments.** `ConversationHealth` recorded the call as healthy,
because it looks for fragmentation caused by the channel and this was caused by
us. That is the failure it exists to prevent, arriving by a route it cannot see.

**The judge never dropped a term.** Across seven judgements not one left the
list; "management concurrency" was still being reported ninety seconds after the
interviewer stopped asking. Worse, at 195s the candidate gave a competent
account of idempotency keys, and the judge listed idempotency keys as unexplained
on that same judgement.

That is why the caps had to be invented. The design says a term leaving the
judge's list is how the interviewer learns to stop asking; in a real interview
that had never once happened, so the only brake was an arbitrary allowance.

**What was wrong with it.** The instruction to drop explained terms existed, as
one bullet under "Do NOT report", beneath three worked examples of terms to
report. The listing task was concrete and salient; the removal condition was a
negation with no test attached, and "has since explained" is a judgement with
nothing to check against. It is now the first step of an ordered procedure: look
for the sentence where they say what the thing does, anywhere in the transcript,
and only report the term if there is no such sentence. The list is stated to be
replaced rather than accumulated.

The discriminator that stops it dropping terms too eagerly is kept sharp, because
a candidate restating a term in different words has not explained it and that is
the whole failure this catches: naming an effect, defining a term with itself, or
attributing it to another unexplained thing all fail the test.

**How**

`bot/config.py`, `FILLER_ENABLED`, off unless set, read at the moment it is
needed. `bot/brain_director.py` consults it in the one place the acknowledgement
is built. Nothing else changed, and nothing was deleted.

`bot/brain/drivers.py`, `JUDGE_SYSTEM` restructured. Off the live path, so it
costs no latency.

**Measured**

Replaying the stored interview through the judge, the term the candidate
explained now leaves the list on the following judgement, where before nothing
ever left it. Two behaviour tests hold the pair: a term explained with a
mechanism drops, a term merely restated stays.

Time to first audio should now measure the question rather than the
acknowledgement. Samples where `generation_ms` read 173ms were the
acknowledgement being spoken, not a model composing anything.

**Not done**

- **The judge fix cannot be shown on the scripted scenarios**, because their
  candidates never explain anything. That is what those scripts are for. The
  evidence is the replay against the real transcript.
- **Probe counts per section did not move**: worst section was 3 to 5 before and
  2 to 5 after, on the same scenario and model. Expected, for the reason above.
- The prohibition once a section's allowance is spent, and the paraphrase problem
  where the interviewer smooths a candidate's wording and presents it as a quote,
  are both still open and deliberately not in this change.
- Time to first audio has not been re-measured on a live call.

## 2026-08-05 — The interviewer stops talking half the time

**What**

The interviewer now asks short questions. Twenty words for a whole turn,
counted, instead of fifteen words a sentence with two or three sentences
allowed.

Measured on the scripted runs, past the opening: the median turn went from 25 to
17 words, and the share of all words spoken by the interviewer went from 47% to
37% on one scenario and 55% to 44% on the other.

**Why**

**48 percent.** On a real interview the interviewer spoke almost half of every
word said. In an interview the candidate should be doing most of the talking,
and it was close to even.

That is what the candidate meant when they said it felt long, and it is not what
anybody assumed. The suspicion was latency, and there was a visible twelve
second gap between the interviewer speaking and speaking again. That gap turned
out not to exist: the timestamps in a transcript mark when a turn *finished*, so
the interval between two of them is the second one being spoken. Composition
never exceeded 1943ms. What the candidate was sitting through was five turns of
26 to 30 words, each about twelve seconds of continuous speech.

**The rule already existed and was being followed.** It said "roughly fifteen
words" per *sentence* and allowed "two or three short sentences per turn", which
permits forty-five. A twenty-seven-word turn broke nothing. The rule had never
put a bound on the thing a candidate actually sits through, and everyone
including this changelog had been reading it as though it had.

**How**

`bot/brain/prompting.py`. The rule is now a turn limit, written the way the
one-question rule is written, because that one holds: a number, and an
instruction to count it before answering.

The alternative is removed rather than discouraged, per the measurement recorded
at the contradiction block. The preamble is named as the thing that must not
happen: a sentence about what they just said, then why you are asking, then the
question. That preamble is what carries a reply past twenty words, and it tells
the candidate nothing they were not present for.

Nothing truncates the model's output. If it cannot write short questions the
answer is to say so, not to cut its sentences off after the fact.

Two supporting changes, both from findings made today:

- **The first question of a topic gets the rule again**, because the model
  imitates its own earlier turns out of the verbatim window. A long opening
  question makes every question in that topic long.
- **Our own supplied sentence was breaking the rule.** The escalated term probe
  ran to twenty-six words, so the block enforcing brevity was handing the
  interviewer the longest sentence it had. It is now twelve.

No worked example was added. Every example in this prompt is either the
interviewer's own words or interpolated from real candidate text, and this
morning's fabricated quote came from an example containing a plausible candidate
phrase.

`scripts/compare_models.py` gains `words_median`, `words_max`,
`words_over_limit` and `interviewer_share`, because nothing counted words.

**Not done**

- **The share target held on one scenario and not the other**: 36 to 38 percent
  on deflecting, 43 to 45 percent on jargon. The interviewer's own words fell by
  about a third on both. Share is a ratio, and the jargon candidate simply says
  less, so part of that number is not the interviewer's to fix.
- Long turns are rarer but not gone: the longest turn in a run is still 22 to 39
  words. The median moved; the tail did not.
- Soft paraphrase remains. The interviewer still says "you said session
  management *controls* users per session" where they said "handles".
- The mechanism signal came down slightly on one scenario, from 10, 10, 9 to
  9, 9, 6. That signal has swung far more than this between identical runs, so it
  is probably noise, and it is not being claimed as unchanged.

## 2026-08-05 — The interviewer quoted our own prompt at a candidate

**What**

Two things it was doing to candidates, both now changed.

It attributed a sentence to a candidate that they never said. That sentence came
out of our own prompt.

And it would not let go: four questions about one idea across ninety seconds,
never once saying it was moving on. A section now spends at most two questions
unpacking vocabulary, whatever that vocabulary is called, and says out loud that
it is leaving it.

**Why**

At 103 seconds into a real interview the interviewer said, word for word:

> "Okay. So you used session management and concurrency control."

The candidate had never said that. They had said "management concurrency" and
"the session management manages those people per session". The phrase put in
their mouth was the worked example inside our own praise ban:

>   To acknowledge, repeat their own words flatly, then ask:
>     "Okay. So you used session management and concurrency control."

It read the example and presented it as their words. **That is a fabricated
quote in a hiring record**, which is the thing `feedback/score.py` exists to
prevent: every quote in a report is checked against the transcript and dropped
if it cannot be found. This arrived through the front door instead. The
interviewer says it out loud, so it lands in the transcript those quotes are
checked against, and it passes verification because by then it is really there.

The cause is the lesson already written eight lines above the block that was
wrong: a concrete example beats an abstract instruction. We forbade the
behaviour in prose and then demonstrated it in a sample sentence, and the
sentence won.

The second problem was measured rather than guessed. Replaying the interview
through the judge, seven judgements returned "management concurrency", then that
plus "session management", then those plus "concurrency control": three strings
for one idea, all live at once, **not one ever dropped**. Each was tracked
separately, so each had its own two attempts. Six sanctioned questions about one
word. The candidate got four in ninety seconds and it stopped only because they
changed the subject themselves. `technical_depth` spent 132 seconds and hit its
turn ceiling with its own material unasked.

**A section allowance was chosen over matching the strings up**, for two
reasons. It bounds the harm directly, which is the interrogation the candidate
feels and the section spending itself on vocabulary, where matching only makes
six asks into two for this one transcript. And it does not depend on the judge
behaving: that same replay had the judge listing "idempotency keys" as
unexplained on the judgement where the candidate explained them competently, so
the stopping condition must not be "the judge stopped reporting it". Matching
would need a similarity rule, and a rule loose enough to join "session
management" to "concurrency control" is loose enough to join things that are not
the same at all. This repo has been caught by that shape twice.

**How**

`bot/brain/prompting.py`. The worked example is gone. The rule now names only
words that are safe to copy: say "Okay." or "Right.", repeat back only words
they actually used, never put a phrase in their mouth and never smooth it into
better wording than they used. Every other worked example in the prompt was
audited: they are either the interviewer's own words or built from real
candidate text, and none presents a plausible candidate phrase.

`bot/brain/brain.py`, `MAX_TERM_PROBES_PER_SECTION = 2`, counted on the section
so variants of one idea share one allowance. The per-term cap of two stays
underneath it so one term cannot take both attempts while another is open.

**The let-go was queued and never spoken in that whole interview.** It was
consumed on read, so a prompt that was rendered and discarded emptied the queue
without anybody hearing anything. It now survives any number of renders and is
cleared only once the interviewer has actually spoken, which is the event that
was being confused with reading a prompt.

`bot/brain/terms.py`. A statement is not an ask. The spoken acknowledgement
repeats the candidate's own term back at them, matched every word test, and was
burning an attempt with no question having been put. `VOICE_RULES` requires
exactly one question mark in a real reply, so requiring one here is the same
rule read from the other end.

**Measured**

Three runs, deflecting scenario, `gpt-4.1-mini`:

- **The let-go is now spoken**: 2, 3 and 2 times per interview, in every section
  where the allowance ran out. It was zero before.
- **The specific fabricated sentence is gone** and cannot recur: it is not in
  the prompt for the model to copy.
- **The two-probe allowance did not hold in behaviour**: sections reached 3, 5
  and 3 probe-shaped questions. See below.

**Not done**

- **The allowance bounds what the brain instructs, not what the model does.**
  Once the block stops rendering, the model keeps asking questions of the same
  shape, because its own earlier turns are sitting in the verbatim window and it
  imitates them. Every excess probe measured came *before* the let-go, from
  questions the counter never saw: they were about a term other than the one
  offered, so nothing incremented. This is the worked-example lesson a third
  time, with the model's own transcript as the example. Counting the behaviour
  rather than the instruction, or forbidding the shape once the allowance is
  spent, are the two candidate fixes and neither is in this change.
- **Soft misquotation remains.** The interviewer still says "you said
  concurrency control *lets* multiple users access" where they said "allows",
  and "session management *controls*" where they said "handles". The instruction
  against smoothing their wording is there and is not reliably followed.
- Question length, TTFA measuring the filler, whether the filler earns its cost,
  `SMART_TURN_STOP_SECS`, and the judge never dropping explained terms are all
  untouched and all still open.

## 2026-08-05 — Two asks and then let it go, and something to say while it thinks

**What**

The interviewer asks about an unexplained term twice, and then says so and moves
on. It no longer asks a third time.

While the next question is being composed, it says one short sentence made only
of the candidate's own words: "Okay. So you used session management and
concurrency control." Not every turn, and never anything that rates the answer.

**Why**

The change that landed an hour earlier over-corrected. Uncapped, gpt-4.1 asked
the identical escalated sentence **four times in a row** while the candidate gave
the identical non-answer. For a candidate who genuinely cannot explain something,
that is worse than the failure it replaced: it went from moving on too easily to
not letting go at all.

Repairs already cap at two attempts, and the reasoning written there applies
unchanged: a third time is an interrogation, and recording that the ground was
never covered is more honest than grinding at somebody who has already said all
they have. **Two, not three**, because in every run where the candidate could
explain, the first ask was enough; the second ask is what caught the deflection;
and nothing in any transcript was ever resolved by a third.

It is said out loud rather than dropped silently. A candidate pushed twice on one
word who then hears the subject change without comment is left working out
whether they failed something. One sentence is the difference between an
interviewer that has decided to move on and one that appears to have given up on
them.

**The filler is built on one person's experience of a live call, not on a
measurement, and that is worth stating plainly.** The harness cannot measure
time to first audio, and the numbers it can produce say composition takes 1082
to 1268ms at the median and 3746ms at worst. That gap is real and it is
composition, not the judge, which never blocked anything. But the reason this
was built is that somebody sat through an interview and felt the silence.

**How**

`bot/brain/brain.py`, `MAX_TERM_ATTEMPTS = 2`, mirroring `MAX_REPAIR_ATTEMPTS`.
A term that reaches it is closed permanently, not just for the section, because
two asks is the whole allowance and a third somewhere else is still a third. It
is queued once for the interviewer to let go of out loud, and consumed by the
turn that does it.

**The count is of questions asked, not terms offered.** It was the other way
round for an hour and it produced a live falsehood: the model is handed a term
and may ask about something else entirely, so a candidate could hear "you have
said session management a couple of times now, and I still do not know what it
actually does" after being asked exactly once. That is the interviewer telling
somebody they failed to answer a question that was never put to them. It also
spent the two-attempt allowance on turns where nothing was asked, so a term
could be let go with "let's leave that one there" after one real ask, or none.

`bot/brain/terms.py` decides whether the question the model produced was about
the term. Deterministic, in code, like `repair.py` and `refusal.py` and for the
same reason: this is bookkeeping, not judgement. Two ways a question counts,
because a question can be about something without naming it: it uses a
distinctive word of the term, or it points back at what was just said with a
cue like "how does that actually work". The second is only readable as an ask
because it is only ever checked on a turn where a term was put in front of the
model.

**It errs towards not counting**, deliberately. Undercounting means a term takes
an extra turn to be let go, which is mildly annoying. Overcounting means telling
a candidate something untrue, which is the bug. Words shorter than four letters
and words like "system" and "platform" identify nothing, so a term made only of
those is never matched on its words alone.

The count now moves when the brain hears what the interviewer actually said, on
the following frame, rather than when the prompt is rendered. A term offered and
not used stays open and unspent and comes back with the same first-ask wording.

**Where it lands in the report.** `SectionOutcome.unexplained_terms` carries the
terms that were asked about to the cap and never explained. That is evidence in
its own right: "never asked" and "asked twice and could not say" are different
facts about a candidate, and a transcript makes the second easy to miss. A term
explained before the cap never reaches the report, because being asked once and
answering is not a finding.

`bot/brain/prompting.py`, `reflect_back()`. **A template, never a model call**,
and that is the whole safety property. The praise ban works; a filler generated
by asking a model for something friendly to say is a route straight around it,
and this morning a model volunteered "that's a clear technical approach" about an
answer that explained nothing. A template that can only emit their own noun
phrase cannot do that. If there is nothing concrete of theirs to repeat it
returns nothing and the silence stands.

`bot/brain_director.py` speaks it. A `TTSSpeakFrame` goes straight past the LLM
to the synthesiser, the same route the opening greeting takes, so it is already
being said by the time generation starts. Nothing awaits anything. The system
instruction is told what was said, or the model acknowledges the same thing again
and the candidate hears it twice.

It fires at most every third turn, only inside a competency, and only when the
judge has pulled a phrase out of what the candidate just said, so it cannot
reflect back something they did not say.

**Measured, and the model comparison that followed**

Four models, three runs each, on the two scenarios the bar is about. The bar
holds when the question after a deflection goes back at the term, escalates, or
lets it go out loud:

| model | bar held | menu fallbacks | TTFT median / max / spread | live $ |
| --- | --- | --- | --- | --- |
| gpt-4.1-mini | 5 of 6 | 1 to 3 | 868 / 1213 / 444 ms | $0.016 |
| gpt-4.1 | 6 of 6 | 0 to 4 | 825 / 1171 / 405 ms | $0.077 |
| gpt-5.4-mini | 6 of 6 | 2 to 11 | 649 / 791 / 172 ms | $0.030 |
| gpt-5.4-nano | 5 of 6 | 5 to 11 | 619 / 687 / 129 ms | $0.009 |

**The mechanism signal fell from about 17 in 21 to about 7 in 21, and that is
the cap working rather than a regression.** The old number was counting the
repetition this change removes. The signal cannot be compared across the cap.

**The judge costs more than the conversation.** Per 20-minute interview, live
gpt-4.1-mini is $0.016 and the judge on gpt-4.1 is **$0.069**, four times more,
and it is not affected by which live model runs. Since this morning the judge
fires after every answer rather than once per section, which is 21 calls against
a large prompt. Nobody had looked.

Prompt caching partly works and was not designed for: 5,000 to 7,000 of about
41,000 live input tokens come back cached, varying run to run. The stable prefix
is the header and voice rules at about 956 tokens, just under the 1,024-token
threshold, so the cache only engages on the turns where more of the prompt
happens to repeat.

**Not done**

- **If generation fails, the candidate hears the acknowledgement and then
  silence.** The filler is one sentence and cannot be the whole turn by design,
  but nothing stops it becoming the whole turn if the model never answers. The
  silence ladder is the backstop and nudges at fifteen to twenty seconds. It is a
  real gap, one sentence wide, and it is not covered by a test.
- The filler has not been heard out loud. It is unverified in the only way that
  matters for something spoken.
- Nothing predicts a slow composition, so the filler sometimes covers a gap that
  was not there. The cooldown bounds how often that can happen; it does not
  prevent it.
- The cap is on asking, not on the candidate. Somebody who uses six unexplained
  terms is asked about all six, two asks each.

## 2026-08-05 — An unexplained term is tracked like a contradiction

**What**

When a candidate uses a term and does not say what it does, the interviewer is
now handed that term and the sentence to ask about it, exactly the way it is
handed a contradiction. The term stays open until the candidate actually
explains it, not until it has been asked about once. Asked a second time, the
interviewer says so out loud: "you have said session management a couple of
times now, and I still do not know what it actually does."

The coverage-verdict block added earlier today was deleted. It did not work.

The prompt token bound moved from 1800 to 2400, on a measurement rather than a
feeling, and the test that guards it was rewritten because it had been passing
on a prompt it was not rendering.

**Why**

**A mandatory block that names a specific item is followed. One that describes a
state is not.** Both numbers were already in this repository and nobody had put
them side by side.

The contradiction block was measured at 80% compliance on the mini-tier model.
It says: here is a specific thing they said, your next question must be about
it, and here is the sentence. The coverage block written this morning says:
coverage is insufficient, do not move on. That is a state, and it leaves the
model to work out what to do about it. It was measured at no effect, and the
model moved on anyway.

It is also why persistence worked for one and not the other. A contradiction is
held as an item in a list until it has been raised. "Insufficient" is a fact
about the section that has no memory of what was already asked, so there was
nothing to escalate and nothing to come back to.

So an unexplained term is now an item in a list, with one difference that
matters. A contradiction closes when it has been **raised**, because raising it
twice is prosecutorial. A term closes when it has been **explained**, which is a
different event, and conflating them is precisely this morning's failure: the
interviewer asked once, received a restatement, and treated having asked as
having found out.

**How**

`bot/brain/drivers.py`. The judge returns `unexplained_terms`. The instruction
is careful about what it is asking for, because "jargon" would fire on ordinary
technical speech: the test is whether the candidate said what the thing does,
not whether the word is technical. "We used Postgres" names a choice and is not
reported. "We implemented session management concurrency, which allows multiple
users to pay at the same time" states an outcome and not a mechanism, and is.
Anything defined with itself is a restatement, and a restatement is never an
explanation.

**How a term is decided to be explained, and what that costs.** The judge
decides, because nothing else can. It is asked on every answer for the terms
that are still unexplained *as of now*, and the brain replaces its list wholesale
rather than merging. A term dropping off the judge's list is how the interviewer
learns it can stop asking. The cost is that the judge is the single point of
failure for closing a term: if it keeps reporting one the candidate has
explained, the interviewer keeps asking, which is worse than the reverse. It
costs no extra call, because the judge already runs on every answer. The count
of how many times a term has been put in front of the interviewer survives the
replacement, which is what lets the wording escalate.

`bot/brain/prompting.py`. One mandatory slot, not two. Two blocks each saying
"your next question is already decided" are not twice as strong; they are two
instructions pointing at different things, and the model resolves that by
choosing, which is the discretion these blocks exist to remove. A contradiction
wins the slot: it is rarer and it decays, while a term will still be there next
turn.

`bot/brain_director.py` records which term was offered. The same call is missing
for contradictions and has been since they were built, so in a live interview a
contradiction is re-rendered every turn instead of being raised once. That is a
real bug, it is the same shape as this one, and it is noted rather than fixed
because it changes when contradictions stop being asked about.

**The token bound: raised to 2400, with the reason.** 1800 was a proxy for
keeping a mini-tier model viable in real time and the proxy was never checked.
Streaming `gpt-4.1-mini` at four input sizes, eight interleaved rounds each,
time to first token: 778ms at ~1400 tokens, 757ms at ~1800, 820ms at ~2300,
742ms at ~2800. **1800 to 2800 is -15ms on the median, against a spread within a
single size of up to 358ms.** Prompt length in this range does not measurably
move time to first token, so holding 1800 was buying nothing and costing content.
The bound is kept, at a level that covers the true worst case, because the thing
worth guarding is growth nobody notices. Flatness is asserted separately and is
the property the sectioned design actually rests on.

The test that guards it was rewritten. It drove a brain with no judge, so the
verdict block, the carried claims and the contradiction block were all empty and
it was measuring a prompt no interview ever sees: the real worst case was 2296
tokens while the test asserted under 1800 and went green.

**Measured**

`compare_models`, three runs per model, ceiling raised from three turns to six
so a term can be asked about, deflected and asked about again. Questions that
echo the answer and ask what the thing is or how it works, out of 21
opportunities:

| scenario | model | before today | this morning | now |
| --- | --- | --- | --- | --- |
| jargon | mini | 0/18 | 4/18 | **17, 17, 18 /21** |
| jargon | gpt-4.1 | 0/18 | 7/18 | **16, 16, 17 /21** |
| deflecting | mini | n/a | 6/18 | **17, 16, 19 /21** |
| deflecting | gpt-4.1 | n/a | 4/18 | **17, 18, 18 /21** |

Questions asking for a trade-off, a metric or the bet behind it are now 0 to 2
per run, from 3 to 4. The spread across runs is 1 to 3 against an effect of 17,
which is the first time this session the effect has been larger than the noise.

**Not done**

- **It has become a broken record.** In one gpt-4.1 run the interviewer asked
  the identical escalated sentence four times in a row. Repairs are capped at
  two attempts for exactly this reason and nothing caps this. A person asks
  twice, then says they will come back to it and moves on. This does not, and
  the fix has over-corrected into the opposite failure.
- The bar was not fully met. On the immediate question after a deflection it
  held in three of three gpt-4.1 runs and two of three on mini, which is the
  production model. The one miss moved to a different unexplained term rather
  than back to the first, then returned on the next turn.
- The judge is the only thing that closes a term. An unstable judge means an
  interviewer that keeps asking about something already explained.
- The contradiction block is still never marked raised in production.
- In-progress claims are still not rendered.
- Nothing measured on a live call. The time-to-first-token numbers are from the
  API directly, not from the voice pipeline, so they exclude the transport.

## 2026-08-05 — The interviewer is shown its own verdict, and stops praising answers

**What**

Three changes, in the order they were measured.

The interviewer now sees what its own judge made of the answers so far: the
coverage verdict and the sentence explaining it, rendered into the live prompt as
an instruction rather than a note. The judge now runs after every answer instead
of once per topic. And the interviewer is forbidden from saying an answer was
good; to acknowledge one, it repeats the candidate's own words back flatly.

**The stated bar was not met.** After a candidate deflects the same term twice,
the next question still moves to something else. What did change is reported
below with numbers.

**Why**

The interviewer asked what a term meant, got a circular non-answer, and accepted
it. **It had not forgotten the exchange.** Eight turns are carried verbatim, so
its own question and the empty answer were both in front of it as real messages.
It read them and judged the non-answer to be an answer. The failure was
judgement, not memory.

And the judgement already existed. The judge is told that "a fluent answer that
never names a concrete decision is 'partial' at best", writes the sentence
explaining its verdict into `depth_rationale`, and until now that field went to
the report and nowhere else. The reasoning was produced, written down and thrown
away on every turn of every interview.

The praise ban started as a footnote and turned out to matter most. In a scripted
run the interviewer said *"You explained how concurrency control and session
management work"* about an answer that explained nothing, and moved on. It also
produced "That sounds like a solid technical approach", "That makes sense" and
"Very cool" about non-answers. That is not decoration: **the false acknowledgement
is the mechanism by which it talks itself into moving on**, and it lands in a
hiring record that is meant to be neutral. It also tells a candidate they did
well, so they stop elaborating.

**How**

`bot/brain/prompting.py`. A `_render_verdict` block, placed directly under the
contradiction block for the salience reason recorded there. An unmet verdict is
written as mandatory: do not move on, do not ask a listed question, go back at
the weakest thing they said. A `NEVER SAY AN ANSWER WAS GOOD` block in
`VOICE_RULES`, which forbids the evaluative forms by name and supplies the
neutral one, per the measured lesson that removing the alternative beats adding
emphasis.

`bot/brain/brain.py`. `_maybe_request_judgment` no longer stops after one verdict
per section. Nothing awaits it and the single-flight guard stays, so a slow judge
degrades to the old behaviour rather than to none.

**Which turn's verdict the model actually sees.** On the frame for question N the
brain observes the answer to question N-1, renders the prompt, and only then
spawns the judgement covering that answer. It lands about 1.2 seconds later,
while question N is being spoken. So when composing question N+1 the freshest
verdict available describes **the answer before last**. It is one answer stale
and should not be described as current. Before this change it described the
section's first two turns and was applied to the whole of it.

`bot/brain/harness.py`, a `deflecting_candidate` built from `int_b3d0a830156b`
and `int_a96da5ddb0be`: asked what session management does, it answers that it
manages sessions. `scripts/compare_models.py` gains that scenario.

Two blocks are now clipped. The judge writes prose and the prompt has a ceiling,
so `depth_rationale` is capped, and so are carried claims, which have never been
bounded at all. Measured against the 26 real claims in the local database:
median 102 characters, longest 237.

**Not done**

- **The bar was not met, and the prompt token ceiling is now exceeded.** With a
  verdict rendered the worst case is 1888 tokens against a bound of 1800, and
  1990 once carried claims are present. The deterministic suite passes only
  because `test_section_prompt_stays_small_and_bounded` never populates a
  verdict, which means that bound has been nominal for some time. The pacing
  example in `VOICE_RULES` was cut to make room and it was not enough.
- **Stage 3's filler was not built.** It was gated on time-to-first-audio moving,
  and no delay could be demonstrated: the text harness cannot measure TTFA, and
  the live path never awaits the judge. A judgement takes 1.2s median, 2.2s max.
  Whether it contends for the event loop under a real call is unmeasured.
- The signal is too noisy at one run per model to attribute small changes.
  Deflecting on mini read 4/18, then 1/18, then 6/18 across three runs of the
  same code path at temperature 0.6.
- In-progress claims are still not rendered. They were dropped for space.
- Nothing was measured on a live call.

**Verify**

```bash
cd backend && uv run pytest tests/ -q
cd backend && uv run python scripts/compare_models.py --scenarios jargon deflecting thin_answer
```

## 2026-08-05 — The interviewer asks what a term means instead of taking it as read

**What**

When a candidate uses a technical term and does not say what it does, the
interviewer now asks. "You said you implemented session management concurrency.
Walk me through how that actually works."

It used to accept the term and ask about something attached to it: what the
trade-off was, how it was measured, what the bet behind it had been. All of
those sound like probing and none of them establish that anything is there.

A prompt change and nothing else. No new model call, no new state, no change to
the judge or to when sections move on.

**Why**

**The model was following the prompt, not ignoring it.** This is the whole
finding, and it took reading the prompt rather than the transcripts to see it.

The block that tells the interviewer how to go deep offered a closed menu of
four things to ask for: the trade-off they made, the thing that went wrong, a
decision they would defend, or a number and how it was measured. There was no
entry for "they used a word and did not say what it means", so the model picked
the nearest fit. Against a candidate saying *"we have implemented a session
management concurrency, which allows multiple users to make the payments at the
same time"*, it asked what trade-off that involved. It even quoted the term
back, which is why this looked like a follow-up to anyone reading quickly.

The earlier stage had a second problem. It said *"follow their thread rather
than switching to a different question"*, which states a preference between two
options while a bulleted list of the other options sits directly below it. This
repository had already measured that exact phrasing at **0% compliance**, and
recorded the finding in a comment eight lines above the block that was wrong.
The fix that worked there was to remove the alternative rather than argue for
the right one, and that is what has been done here.

Measured over the six substantial interviews now in the local database, all with
a candidate answering in confident jargon: of 39 interviewer questions that
followed an answer, **2 asked what a term meant and 10 asked for a trade-off, a
metric or a bet.** The employer's own criterion for that competency was depth
"not just at a conceptual level". The report scored 3.0 out of 5 on evidence
that was entirely conceptual.

**How**

`backend/bot/brain/prompting.py`, two edits inside `_render_depth_ramp`, which
is the only file this change touches on the live path.

The first follow-up turn is now written as a mandatory block: do not ask a new
question, do not move to any listed question however well it fits. The demanding
turn keeps its four options and gains a fifth that takes precedence: if the last
answer named a technique or system without saying what it does, that term is the
question, and asking for its trade-off is explicitly forbidden because it accepts
the term as given. A model sentence is supplied, the way the contradiction block
supplies one.

The block grew, and `test_section_prompt_stays_small_and_bounded` caught it at
1810 tokens against a 1800 bound. That test's own comment says the honest
response is to ask what to cut, so the wording was tightened rather than the
bound raised. It now renders at 1646.

`backend/bot/brain/harness.py`, a `jargon_candidate` built from real transcripts
rather than invented. It sounds specific and explains nothing, and when pushed it
defines each term with itself: *"the concurrency allows the multiple users to
access it concurrently, while the session management manages those people per
sessions."* That is verbatim.

`backend/scripts/compare_models.py`, a `jargon` scenario and a new signal.
The signal exists because the obvious one does not work: the failing question
quoted the candidate's own term, so "did it reference what they said" scores it
as a probe. It counts three shapes instead, and the one that matters is a
question that echoes the answer **and** asks what the thing is or how it works.

`backend/tests/test_behavior.py`, one test added alongside the existing
thin-answer one, which was left exactly as it was. The old test joins every
interviewer turn and looks for words like "specific" or "which" anywhere in the
run, so a reworded planned question clears it, and it passed throughout all six
interviews where this went wrong.

**Not done**

- **Option B is untouched: the judge's rationale and its within-section claims
  still do not reach the conversation.** The judge is already told that "a fluent
  answer that never names a concrete decision is partial at best", writes a
  sentence saying so into `depth_rationale`, and nothing ever reads it. Wiring it
  in would cost no live model call, but it needs a `JUDGE_SYSTEM` edit so a claim
  can be marked unexplained, and the judge currently runs once per section at the
  floor turn, so it would not see a term used later. That is a separate change
  with its own measurement.
- **The fix holds for the first unexplained term and not for the rest.** On the
  jargon scenario both models ask what session management is, get another
  circular answer, and drift back to trade-offs. Mechanism questions went from
  0 in 18 to 2 in 18 on mini and 3 in 18 on gpt-4.1. That is the change working,
  not the problem solved.
- One transcript shows mini answering the hash-table non-explanation with "that's
  a clear technical approach". It is not, and nothing here teaches it to notice.
- Nothing was measured live. Both runs are the text harness against scripted
  candidates; the numbers from real interviews are the before picture only.
- The prompt is 1646 tokens against a 1800 bound. The next block that lands here
  has less room than the last one did.

**Verify**

```bash
cd backend && uv run pytest tests/ -q
cd backend && uv run python scripts/compare_models.py --scenarios jargon thin_answer
```

Then read `backend/sessions/model_comparison/jargon__*.json`. The turn to look at
is the one straight after "we have implemented a session management concurrency".

## 2026-08-05 — Recording is switched off, and the consent notice says so

**What**

Interviews are no longer recorded. Everything that was built for recording is
still here and still works: the storage path, the job that collects and deletes
files, the ten day retention, the delete button, playback in the panel, and all
of the tests. What stops is the act of recording.

One environment variable turns it back on. Set `RECORDING_ENABLED=1` and
restart, and the whole feature returns with no other edits.

The consent notice changes with it. With recording off a candidate is told the
interview is not recorded and that the transcript is what is kept. Turn the
switch on and they are told they are recorded, that someone may watch it, and
that it is kept for ten days. Both versions of the notice live on the page and
the server serves the one that matches.

The camera stays on.

**Why**

Money, and a promise.

Daily charges for every recorded minute and then charges again to store the
result. The job that pulls recordings down and deletes Daily's copy is not
actually running, so storage has been accruing with nothing removing it.
Recording goes off until that is fixed and somebody has decided they want it on.

The promise is the more important half. The notice a candidate agrees to said
they were being recorded, that a person might watch it, and that it would be
kept for ten days. With recording off all three are false, and this is a consent
gate: the candidate is being asked to agree to it. This project has now recorded
six separate occasions where a screen described something that was not
happening, and every previous one was a report or a status. This is the first
where somebody is agreeing to the description.

So the notice is tied to the switch rather than edited to match today's setting.
A notice that only matches one setting is a trap for whoever flips it: they
would silently start recording people who were told they would not be. Both
versions sit next to each other in the page so that whoever edits one sees the
other.

**How**

`backend/bot/config.py`. The switch: `RECORDING_ENABLED`, absent means off, and
only `1`, `true`, `yes` or `on` count as on, so a half-set variable fails towards
not spending money. It lives here rather than beside the sweep's own switch in
`app/recordings.py` because two processes have to agree on it, the bot deciding
whether to record and the web app deciding which notice to show, and this is the
only configuration module both already import.

`backend/bot/run_bot.py`. One check, first thing in `begin_recording`. Off means
Daily is never asked, so no minute is billed and nothing is stored. The interview
runs exactly as before and its record keeps the `not_recorded` state the panel
already has a sentence for.

`backend/app/main.py` and `frontend/candidate/index.html`. The page carries both
notices wrapped in `<!--RECORDING ON-->` and `<!--RECORDING OFF-->` markers, and
rendering keeps the half that matches. The copy stays in the HTML rather than
moving into Python strings, so it is still where somebody editing the wording
would look and `test_copy_style.py` still reads it. The device check screen
follows the same switch.

`backend/tests/test_recordings.py`, eleven new tests, and three in
`test_video.py` rewritten to read the served page rather than the file, since
the file now holds both versions and reading it proves nothing.

Two things deliberately do **not** read the switch, and both have a test:

- **The sweep.** Recordings made before it was switched off still have to be
  collected and Daily's copy deleted. A sweep that stopped when recording
  stopped would strand them and keep the storage bill running, which is the
  thing switching recording off was meant to end.
- **The room.** Rooms are created when an interview is scheduled and used
  whenever the candidate turns up, sometimes a day later. Withholding permission
  at booking time would mean turning the switch on did nothing for interviews
  already scheduled. Permission is not billed; starting a recording is.

**Not done**

- **Why the sweep is not running has not been investigated.** That is separate
  work and it is the thing that has to be fixed before recording goes back on.
- **There is at least one recording sitting in Daily right now.** Switching
  recording off does not remove it. The sweep collects and deletes it, so it will
  only go when the sweep runs.
- Nothing was measured. The per-minute figures here are Daily's list prices, not
  an invoice, and nobody has added up what has been spent so far.
- The panel still shows the recording card and the delete button for interviews
  that have a recording, which is correct, and shows "this interview was not
  recorded" for every new one.
- Turning the switch on requires a restart. The rendered page is cached for the
  life of the process, which is right for a setting that comes from the
  environment, but it does mean this is not a runtime toggle.

**Verify**

```bash
cd backend && uv run pytest tests/test_recordings.py tests/test_video.py -q
cd backend && uv run pytest tests/ -q
```

By eye: open the join page and read the notice, which should say the interview is
not recorded. Set `RECORDING_ENABLED=1`, restart, and read it again.

## 2026-08-04 — Interviews are recorded, and a person can watch them back

**What**

An interview is now recorded, video and sound, and the recording appears on the
session page in the panel. A reviewer can play it, and clicking any line of the
transcript jumps the video to that moment.

They can delete a recording, which asks first and then actually removes the
file. Every recording is deleted automatically ten days after the interview,
whether anyone touches it or not.

If there is no recording, the page says so in a sentence and says why. It never
shows a player with nothing behind it.

The consent notice was rewritten again. It now tells the candidate they are
recorded, that someone at the employer may watch it, and that it is kept for ten
days.

**Why**

The recording exists for one reason: so a person can watch an interview back.
A transcript says what was said and not how. Hesitation, whether someone is
reading, whether a voice is theirs, whether somebody else is in the room feeding
them answers, none of that survives into text. That last one is the case that
motivated it, and it is worth being plain about: this is here so a reviewer can
judge whether a candidate had help.

It is for a person and nothing else. Nothing scores the video, nothing measures
a face or a gaze, and no model is given it. Proctoring stays out of scope, and
the notice tells the candidate that in as many words.

The other half is the consent notice, which now describes what happens for the
first time in this project's history. It has been wrong in both directions: it
promised a recording that was never made, then, once the camera went on in Phase
1, it said the interview was voice only. The number of days is now substituted
into the page from the same constant the deletion job reads, so the promise and
the thing that keeps it cannot drift apart.

**How**

Before any of it was built, the Daily account was checked rather than assumed.
Cloud recording is available on it; a real ten second recording was made,
downloaded as an mp4, and deleted, and the deletion was confirmed by asking for
the recording again and getting a 404.

Two findings from that shaped the design. Daily returns a **stream id** from
`start_recording` which is not the **recording id** in its list, so a reference
stored from the return value would have fetched nothing; recordings are found by
room name instead. And a recording left un-stopped by leaving the room abruptly
still came back `finished` within five seconds, which is what actually covers a
crash, a redeploy or capacity teardown.

`backend/bot/services/daily.py`. Rooms are created with recording permitted.
Four new operations: start and stop in the call, both of which swallow every
failure and return a reason rather than raising, and find, download and delete
over REST. The download streams to a `.part` file and renames only when the
whole body has arrived. The delete checks Daily's own response and raises if it
is not confirmed.

`backend/bot/run_bot.py`. The recording starts when the bot joins the room, not
when the candidate connects: the candidate connecting is immediately followed by
the greeting, and a network round trip in front of that makes every candidate
wait longer. It stops in the `finally` block that already protects the
transcript. Starting is a named function rather than the body of an event
handler so its failure path can be tested.

`backend/bot/persistence.py`. The reference is written while the call is still
live, unlike everything else the bot saves. A killed process never reaches its
`finally`, and a recording nothing knows about is one nothing collects and
therefore one nothing ever deletes.

`backend/app/db.py`, nine columns on the interview and a six-value status.
Separate states for never recorded, still arriving, stored, could not be got,
deleted by a person and aged out, because each is a different sentence to show a
reviewer.

`backend/app/recordings.py`, new. Playback, deletion, and a sweep every five
minutes that does two jobs: pull down recordings Daily has finished compositing,
then delete anything past ten days. It is an asyncio task started by the app's
lifespan, no queue, per CLAUDE.md.

`frontend/admin/admin.js` and `index.html`, the player, the delete button, and
seeking from the transcript. `frontend/candidate/index.html`, the notice.

`backend/tests/test_recordings.py`, new, 31 tests. Three tests in
`test_video.py` were asserting Phase 1 copy that this change makes false,
including that the notice must not mention a recording. They were updated with
the reason written next to them.

**Not done**

- **Access control is unchanged, and this is the one to read.** Anyone with the
  panel password can watch any recording of any candidate, exactly as they can
  already read any transcript. That is the auth model this product has, and a
  video of somebody's face raises the stakes on it. Deferred deliberately, not
  overlooked.
- **On Railway the container filesystem is ephemeral, so a redeploy loses stored
  recordings.** The decision was to store locally for now, and Daily's copy is
  deleted once ours is verified, so there is no second copy to fall back on. The
  transcript is in Postgres and unaffected. This is the strongest argument for
  moving recordings to object storage, and it is the obvious next change.
- Nobody has checked what recording costs per interview, in Daily minutes or in
  disk. Forty minutes of 640x480 video is not large, but it is not measured.
- The offset that makes transcript seeking work is measured by the bot to about
  a second. Good enough to find a moment, not good enough to quote from.
- The sweep assumes one replica, which CLAUDE.md already pins. Two replicas
  would run two sweeps.
- No recording has yet been made by an actual interview end to end. The Daily
  operations were verified individually against the live API, including a real
  recording downloaded and deleted, but the whole path through a real bot session
  has not been run.

**Verify**

```bash
cd backend && uv run pytest tests/test_recordings.py -q
cd backend && uv run pytest tests/ -q
```

By eye: run an interview, then open the session in the panel. The recording
appears within a few minutes of it ending, clicking a transcript line jumps the
video, and the delete button asks before removing it.

## 2026-08-04 — The camera is on, and the consent notice now describes what actually happens

**What**

Candidates are on camera for the interview. They see themselves in a small
picture in the corner and can turn the camera off at any point. There is no
picture of the interviewer, because it does not have one, and no grid of
participants: what is on screen is still the orb and the captions.

Nothing is stored yet. The video goes to the room and stops there.

The notice a candidate agrees to before joining has been rewritten, and so has
the copy on the device-check screen.

**Why**

The notice was telling candidates two things that were not true.

It said "The conversation is recorded and transcribed", and that "The recording
and transcript are shared with the employer". No recording has ever been made by
this system. Only the transcript is written, kept and shared. A person was being
asked to consent to something that does not happen, which is worse than asking
them to consent to too little, because the one document they are given to
understand what is being done to them was wrong.

Recording is coming, in a separate change. That is not a reason to leave the
sentence sitting there: consent is given at a moment, on the basis of what the
page says at that moment, and every candidate who joins between now and then
would be agreeing to a description of a system that does not exist. The notice
is made true now, and it will be changed again when recording is real.

The device-check screen had the matching problem from the other direction. It
said the camera "is switched off again before you join. This interview is voice
only." Four more strings on that screen said the same thing in different words.
All of them are now describing a camera that stays on.

**This is the sixth thing in this codebase found to be reporting something that
was not happening.** The others: judge failures counted as successes, an echo
warning on the greeting, a disconnect logged for every candidate who hung up
normally, Word CVs read as complete when whole sections were missing, and a
health sentence rendered from a value nobody recomputed. The pattern is that the
description and the behaviour are written at different times and nothing checks
them against each other. This entry adds tests that read the page copy, which is
the only check available for a file with no executable coverage.

**How**

`backend/bot/services/daily.py`. The room is created with `start_video_off` set
to false. The bot's transport now states `video_in_enabled=False` rather than
leaving it to a default, because it is load-bearing: the bot subscribing to
video would mean decoding a stream for forty minutes on a container whose
concurrency limit is already set by CPU nobody has measured, and spending it on
a picture nothing looks at. The interviewer works from the transcript.
`camera_out_enabled` stays false. Audio settings are untouched.

`frontend/candidate/index.html`, the only frontend file. The call object asks for
video, capped at capture to 640x480 at 15 frames a second, and sends a single
200 kbps layer rather than a simulcast ladder, since nothing subscribes to the
video at all. The local track is attached to a new self-view in the corner, with
a "Camera off" button beside "Mute". The consent notice and five strings on the
device-check screen were rewritten.

`backend/tests/test_video.py`, new, 14 tests. The room and transport settings are
asserted from source, and so is the page copy, the way `test_copy_style.py`
already does.

`backend/tests/test_interviews.py`. One test was pinning the false copy: it
asserted the page contained "recorded and transcribed" and "voice only". It now
asserts the candidate is told what happens to what they say, which stays true
however the wording changes.

**Not done**

- **No video is stored.** Nothing records, nothing is written to disk, nothing is
  uploaded. The camera picture exists only for the duration of the call.
- **Retention is not decided.** How long a recording would be kept, who can open
  it, and how a candidate asks for it to be deleted are all open. None of that is
  answered here, which is the other reason the notice must not promise it.
- The frontend has no executable coverage and none was invented. The page is
  checked by reading it, which catches wrong words and cannot catch wrong
  behaviour.
- The camera quality caps have not been checked on a real call over a real
  connection. They are chosen to be modest, not measured.
- `CLAUDE.md` still describes the Milestone 5 consent screen as telling
  candidates the interview "is recorded". That is the specification the false
  copy came from. It is left as written because it describes the intended end
  state, and the recording change is where it becomes accurate.

**Verify**

```bash
cd backend && uv run pytest tests/test_video.py tests/test_copy_style.py -q
cd backend && uv run pytest tests/ -q
```

By eye, which is the only way to check the rest: join an interview and confirm
the self-view appears in the corner, the camera button turns it off and back on,
the orb and captions are unchanged, and no participant grid or Daily branding is
visible.

## 2026-08-04 — Word CVs were losing whole sections, including the candidate's name

**What**

Uploading a CV as a Word document read only part of it, depending on how the
page was laid out. Two gaps in our own reading of the file, both now closed.

If the name and contact details were in the page header, which is a normal place
to put them, none of it was read. If the CV was built from tables inside tables,
which is how some templates are made, everything below the first level was
invisible. A third, smaller problem: where a table was used to lay out columns,
the sidebar was being glued onto the end of whatever sentence sat beside it.

**Why**

The header case is the one to lead with, because it reached the candidate.

A CV with the name in the header extracted 867 characters with no error and no
sign anything was missing. The text simply began at EXPERIENCE. Downstream, the
step that builds the interview plan asks for the candidate's name and is given
text that does not contain one, so it returns nothing, and **the interview opens
with "Good afternoon." to somebody whose name we had been given.** The first
thing that happens is the interviewer not knowing who it is talking to.

The nested-table case lost most of the CV: 99 characters were readable at the top
level and 363 more were sitting in tables we never opened. That one at least
failed loudly, because so little text survived that it tripped the minimum-length
check. It was the only one of the two anybody would have noticed, and it noticed
for the wrong reason.

None of this was reported by a user. It came out of laying the same CV out eight
different ways and reading what came back.

**How**

- `backend/blueprint/documents.py` — headers and footers are now read, headers
  placed at the top where the identity block belongs and footers at the end.
  Identical blocks are kept once, so a CV in several sections does not repeat the
  name. Tables inside table cells are followed, to a bounded depth. Row cells are
  joined by a line break rather than a pipe when the row is page layout rather
  than data. The too-short-to-use message now matches the format that was
  actually uploaded.
- `backend/tests/test_document_layouts.py` — new. The eight layout samples become
  regression tests for the Word cases, plus three shapes the samples do not
  cover: a real data table, a document in several sections, and nesting past the
  depth limit.

**Telling a layout table from a data table**

The pipe join is right for a table of values and wrong for a table used as page
furniture, where it produced:

> Rebuilt retry logic after duplicate charges. Introduced idempotency keys end to
> end. | EDUCATION

A heading welded onto the end of an achievement, reading as one sentence.

**A cell containing a line break is holding a region of the page, not a value.**
One value is one line; a CV section is several. That is a statement about what
the two kinds of table are, rather than a threshold tuned to these files, and it
is decided per row: a table whose cells are all single values keeps its pipes and
is untouched. A test builds a real skills table and asserts `Python | 5 years`
still comes out that way, because none of the samples contained one and a wrong
rule here would break the case that already worked.

**Not done**

- **The PDF failures are untouched.** Two of the four PDF layouts extract in the
  wrong order, with sentences broken across unrelated headings, and neither
  raises. That is `pypdf` reading the content stream in the order it was written,
  which it cannot correct because it does not model where text sits on the page.
  Fixing it means a layout-aware library and a dependency, which is a separate
  evaluation. The samples are in place for it.
- No test pins the broken PDF output. Asserting today's wrong text would lock the
  bug in and make the real fix look like a regression.
- **No detection was added.** The obvious signal was already measured and it
  fires on clean documents as often as broken ones, so a bad extraction still
  passes silently unless it is short enough to trip the length check.
- **Paragraphs are still read before tables**, rather than in document order, so
  a Word CV that alternates between them can come out grouped rather than
  sequential. No sample shows it, and changing the traversal risked moving the
  cases that now work.
- **These samples are synthetic.** Eight layouts of one fictional candidate,
  written to exercise specific shapes. They show what can happen. Nobody knows
  which templates real candidates actually use, so how often any of this happens
  in practice is still unknown.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                              # 567 passed, 19 deselected
uv run pytest tests/test_document_layouts.py -q
```

---

## 2026-08-04 — The candidate can read what the interviewer says

**What**

The interviewer's questions now appear as text on the candidate's screen while
they are spoken, one sentence at a time. What is on screen is **the current
question and nothing else**: it stays there for the whole time the candidate is
answering, and is replaced when the interviewer next speaks. There is a button
to turn captions off, and they are on unless somebody does.

The candidate's own speech is never shown. That is deliberate and explained
below.

**Why**

**A candidate who is deaf or hard of hearing cannot take this interview at all.**
It is a spoken conversation with nothing on screen. Several client contracts
treat accessibility as a requirement rather than a preference, and this fails
that plainly.

The second reason is for everybody. When audio is rough, reading the question is
the difference between answering it and asking for it again. Two of the stored
interviews had answers arrive in pieces, and a candidate who can see the question
does not have to spend a turn recovering it.

**Why only the interviewer**

Nobody needs to read what they just said. Watching your own speech appear while
you are still forming a thought is a distraction, and this product works harder
than most to avoid creating one: the endpointing is tuned around not interrupting
somebody mid-thought, and the silence ladder waits longer when the question was a
hard one. Putting a live feed of the candidate's own half-formed sentences on
screen would undo that with the other hand.

There is a sharper reason too. Our transcripts still fragment under some
conditions. Showing a candidate their own answer breaking up mid-sentence would
be unkind and would tell them nothing they could act on.

**How**

- `backend/bot/run_bot.py` — an `RTVIProcessor` in the pipeline and an
  `RTVIObserver` alongside the existing observers. Every user-side signal is
  switched off explicitly, because RTVI sends all of them by default.
- `frontend/candidate/index.html` — a captions panel under the interviewer's
  orb, a toggle, and an `app-message` handler. Each sentence owns a line and is
  updated in place rather than appended, and the panel holds one turn, cleared
  by `bot-started-speaking`. No history cap and no conditional scrolling: with
  a single turn on screen there is no earlier text to preserve, so both were
  removed rather than left as dead code.
- `backend/tests/test_captions.py` — new. The candidate's words are never sent,
  the defaults would have sent them, only whole sentences go out, the processor
  is in the pipeline and the observer is not, and the stored transcript is not
  touched.
- `backend/tests/test_ending.py` — one existing test matched the observers list
  as an exact string and broke as soon as another observer was added. It now
  asserts what it meant: that the session ender is registered as an observer and
  is not in the pipeline.

**When the caption clears, and why not sooner**

The first version kept every sentence, which turned the panel into a running
transcript of the interview. After the greeting a candidate was reading five
lines of what had already been said. Correct content, wrong shape: this is a
caption, not a record.

It now shows one turn and clears when the **next** turn begins, on the
`bot-started-speaking` message the backend was already sending.

It deliberately does **not** clear when the interviewer stops speaking, which is
the obvious reading of "live captions" and is wrong here. A candidate answers
*after* the question has finished. A caption that vanished at that moment would
disappear at exactly the point it is needed, and the reason this feature exists
is so somebody with rough audio can read the question while they answer it. So
the question stays on screen until there is a new one to replace it.

The clear is deferred until the first sentence of the new turn actually arrives,
rather than firing the moment speaking starts, so a turn that produces audio but
no text cannot leave the panel blank.

**Two things that would have silently produced no captions**

Worth recording, because both look like tidying up.

`bot_speaking_enabled` has to stay on even though the page does not use it. The
observer holds each finished sentence in a queue until the bot actually starts
speaking, and that flush is gated by the same switch. Turning it off to reduce
chatter would mean no captions at all rather than fewer messages.

And the page never sends RTVI's `client-ready` handshake, so the observer treats
it as an old client and stops suppressing word-level aggregations by itself.
Without asking for sentences explicitly, captions would arrive a word at a time
and stutter.

**Not done**

- **Nothing the browser does with these messages is covered by a test.** There
  is no JS harness in this repo and one was not invented for this. The rendering,
  the scroll behaviour, the toggle and the de-duplication ship verified only by a
  real session.
- **Interviewer-only is a decision, not a missing half.** If somebody later
  "completes" it by adding the candidate's side, the reasons above are the ones
  to argue with first. The switches are set explicitly in `run_bot.py` and a test
  fails if any of them is removed.
- Daily's own transcription stays off, for the reason it was always off: a second
  vendor transcribing the same audio, billed separately, showing the candidate
  words that differ from the ones stored against their name.
- RTVI's observer keeps an unbounded set of every frame id it has seen. Our own
  observers cap that at 2000; this one does not, and over a long interview it
  grows. Not touched because it is vendor code, but it is the third instance of
  the same shape in this pipeline.
- The stored transcript is unchanged and still written by the transcript
  observer. Captions are display only.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                     # 551 passed, 19 deselected
uv run pytest tests/test_captions.py -q
```

By hand, which is the only real check: join an interview and confirm the
interviewer's questions appear as whole sentences as they are spoken, that they
stay on screen while you answer, that nothing you say ever appears, and that the
toggle works.

---

## 2026-08-04 — Candidates can check their microphone before the interview starts

**What**

A screen between agreeing to take part and going in. It shows a live camera
preview, a bar that moves when the candidate speaks, and a button that records
four seconds and plays it straight back, so they hear what we hear. If more than
one microphone or camera is connected, they can pick.

It sits before the room is booked, not after, which matters more than it looks.

**Why**

Candidates joined blind. There was no preview, no level meter and no way to hear
yourself, so the first anyone learned their audio was bad was during the
interview, which is the worst possible moment to find out.

A poor recording costs the candidate twice over. It produces thin evidence, and
the report then correctly refuses to blame them for it: the recording quality
note exists exactly so a cheap headset does not read as somebody who could not
answer. Both halves of that are right, and together they mean the interview was
simply wasted. Nobody learns anything, and the candidate has to be asked back.

Two of the stored interviews had answers arrive in pieces. A check beforehand
does not fix a bad connection, but it turns a discovery made halfway through
into a decision made before starting.

**Where it sits, and why that is not arbitrary**

Joining spawns the interviewer as its own process and takes one of a small
number of concurrent slots. Checking devices after that would leave it sitting
in an empty room running its silence ladder while somebody hunts for a headset,
and would hold a slot another candidate could be using.

So the check comes first and the room is booked when they press Join. The cost
is that a wrong meeting ID is only discovered after the check rather than
before, so that error is shown on the check screen with the button re-enabled
rather than sending them back to the start.

**How**

- `frontend/candidate/index.html` — a new screen between consent and the call.
  Permission is explained before it is asked for, so the browser dialog is
  expected. Level meter from an `AnalyserNode`, loopback from `MediaRecorder`,
  device pickers from `enumerateDevices` (which only has labels once permission
  is granted, so it runs after). Every preview track is stopped before the room
  is joined: nothing leaves a camera running behind a page that is not using it.
  The first button now reads "Continue" rather than "Start interview", because
  it no longer starts anything.

**The pass condition**

- **A microphone is required.** No permission, no device, or a device another
  app is holding: they cannot go on, and the message says which of those it was
  and what to do about it. The interview is a spoken conversation and there is
  no version of it without one.
- **A camera is not.** It is previewed, and a failure is reported plainly and
  waved through. This interview does not use video at all, and refusing somebody
  an interview over a device nothing switches on would be a worse failure than
  the one this screen exists to prevent.
- **A meter that never moved warns and lets them through.** A candidate who
  simply did not speak during the check has a working microphone and a flat bar.
  Locking them out of their own interview over that cannot be undone from their
  side, and would be a heavier failure than a poor connection.

**Not done**

- **The camera is checked but still not used.** Video stays off: the room sets
  `start_video_off` and the call object still asks for `videoSource: false`. It
  is checked here so that recording video for human review, which is next, does
  not need this screen built twice.
- **The consent notice is unchanged**, and it still says the camera is not used,
  which is true today. Recording video will need that rewritten, and that is
  part of the same later change rather than this one.
- **What the check cannot tell anyone.** That permission was granted and a track
  is live is verifiable from a browser. Whether the audio is any *good* is not.
  A working microphone in a room with a fan, or one that clips, passes this
  check. The loopback is there because a person listening to themselves catches
  what no measurement here can, but whether they act on it is theirs to decide.
- **None of this logic is covered by a test.** `test_copy_style.py` inspects this
  page for user-facing copy and does not execute it, and there is no JS harness
  in the repo. Inventing one for this change was declined. The device handling
  therefore ships verified only by a real session, and the paths most likely to
  differ between browsers are the ones with the least behind them: Safari's
  handling of `MediaRecorder`, and what a mobile browser reports for cameras.

**Verify**

```bash
cd backend
uv run pytest tests/test_copy_style.py -q     # page copy, no dashes
node --check <(the page's script)             # syntax
```

By hand, which is the only real check: open a join link, agree, and confirm the
browser asks for permission only after the explanation. Speak and watch the bar.
Record and listen. Deny permission and confirm the message says how to undo it
and that trying again works without reloading. Join, and confirm the camera
light goes out.

---

## 2026-08-04 — Hanging up at the end was being recorded as a dropped call

**What**

A report can say the candidate dropped out of the call, which is a fair reason
for an interview to be thin. The count behind it went up whenever anybody left
the room. An interview ends with the candidate leaving, so it went up on every
interview that has ever run.

A departure is now only counted when the interview had not finished yet. Leaving
after the goodbye is somebody hanging up. Leaving in the middle is still counted,
whether or not they come back.

**Why**

Six stored interviews, six recorded disconnects, and not one of them was real.
Every single one was a candidate closing the tab after being thanked. The
evidence was identical in all six: one departure, an empty room afterwards, one
participant at peak.

One disconnect is on its own enough to mark a report degraded. So **every report
in the system said the recording was poor and that the candidate had dropped out
of the call**, on the strength of a normal ending. With the echo false positive
fixed the day before, this was the only thing marking anything degraded at all.

**This is the same fault as the echo bug, one signal along**, and it went
unnoticed for the same reason: it fired on everything, so it never looked wrong.
It is the fifth instance of this shape in the codebase, and the list is worth
keeping straight:

1. The judge failed silently, so an interview with no analysis read as a
   candidate who said nothing specific.
2. A report that could not be read displayed as "not scored yet", so nobody
   retried it.
3. The plan refiner said it had shortened an interview it had not touched.
4. The setup chat said "noted" to an interview length it was about to refuse.
5. The echo detector, and now this: an ordinary event reported as a failure.

Either an ordinary state reported as a failure, or a failure reported as
ordinary. Both are worse than an error, because nobody investigates either one.

**The case that needed thinking about**

A candidate whose connection dies mid-answer and never comes back looks exactly
like one who hangs up at the end: one departure, empty room, no rejoin. Presence
cannot tell them apart, and a rule built only on presence would have silenced
the real failure. That is the worse direction: a truncated interview is precisely
when a report most needs to explain itself.

The brain knows the difference, because it knows whether the interview reached
its close. So it is asked, through the same kind of callable the silence ladder
already takes from it. A departure before the interview finished is a drop. A
departure after it is a goodbye.

**How**

- `backend/bot/presence.py` — `RoomPresence` takes an optional `interview_over`
  callable. `left()` discounts a departure only when it returns true. Without it,
  every departure counts exactly as before, so a transport that cannot report
  this degrades to the old behaviour rather than to a broken half of it. A
  callable that raises also counts the departure: over-reporting a dropped call
  can be checked against the transcript, silencing a real one cannot.
- `backend/bot/run_bot.py` — passes `brain.is_finished or brain.withdrew`, the
  same expression the silence ladder is already given.
- `backend/tests/test_presence.py` — eight tests, including the drop that must
  still count and the two ends of the consequence: a clean interview stops
  claiming a poor recording, a truncated one still says so.

**What changed, measured**

Across all six stored interviews:

- Re-assessed with today's code they are **unchanged**: still one disconnect,
  still degraded. The count is written by the bot when the session ends and
  stored; scoring reads it back rather than re-deriving it. **This fix is
  forward-only.**
- Re-run today, the same six sessions would record zero disconnects, and with
  echo also fixed **none of them would read as degraded at all**.

That second line deserves saying plainly rather than being left in a diff. Once
these two fixes are in, the degraded flag has never once fired correctly on a
real interview. Six clean interviews reading as clean is the right answer, but it
also means this signal has no true positive behind it yet, and nobody should
discover that later.

Stored reports do re-validate on read, and it makes no difference here: what is
recomputed on read are the derived fields, and the disconnect count is not one of
them. An old report only changes if the interview is re-run.

**Not done**

- **No stored interview contains a real dropped call**, so like the echo change,
  the true-positive tests are constructed. The rule can be shown to stop the
  false positives; it cannot be shown against a real mid-interview drop.
- The count is not backfilled. Reconstructing it would need to know when the
  departure happened relative to the closing, and nothing stored records either
  time: presence keeps no timestamps and `brain_events` has no closing marker.
  Approximating it was the alternative and was rejected.
- `candidate_present`, `peak_others`, the bystander logic and the silence ladder
  are untouched. The ladder still holds rather than escalating when someone may
  be reconnecting, which is a different question from whether it was a fault.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                       # 542 passed, 19 deselected
uv run pytest tests/test_presence.py -q
```

Run a full interview to its natural close and hang up. `session_metrics` should
record `disconnects: 0` and `present_at_end: 0`, and the report should say
nothing about the recording. Close the tab mid-answer instead and it should
record `disconnects: 1` and say so.

---

## 2026-08-04 — The echo detector was firing on people saying hello

**What**

A report can note that the candidate was heard through their own speakers rather
than headphones, which is a real thing that happens and a fair reason for an
answer to come out garbled. The check that decided this compared which words the
candidate used against which words the interviewer had just used. Two people
having a conversation reuse each other's words constantly, so it fired on
ordinary exchanges.

It now looks for the interviewer's sentence coming back **in order**, which is
what an echo actually is, rather than for the same words in any arrangement.

**Why**

Across every interview stored, the check fired three times, and all three were
wrong. There were no correct hits at all.

Twice it was the candidate saying hello back:

```
bot:        "Good afternoon, Priya. I'm Mitra, an AI interviewer. I'll be
             speaking with you today. How are you doing?"
candidate:  "I'm doing great. How are you?"
```

Seven words, six of them in the bot's sentence, 86 percent by the old measure.
It is two people greeting each other, which uses the same words in both
directions by definition.

The third was different and worth recording, because it was not a greeting. A
candidate turn of four words, "I'm working on", scored 100 percent purely because
all four appear somewhere in a long bot turn. A fragment shares words with
whatever came before it and means nothing by it.

So every report said the candidate had been heard through their own speakers when
that had not happened once. That is a false statement about the recording
conditions, in a document about a person, and it also fed the sentence the
scoring model reads before it judges them.

**This is the same lesson as the one directly above it in this file.**
`_ACKNOWLEDGEMENTS` exists because counting fillers as broken audio flagged five
of eight healthy interviews. A signal that fires on ordinary conversation cannot
be the thing that decides anything. This is that lesson again, one signal along.

**How**

- `backend/feedback/health.py` — `_looks_like_echo` now measures the longest
  unbroken stretch of the candidate's turn that appears consecutively in the
  interviewer's, instead of counting shared words in any order. New helper
  `_longest_shared_run`. `ECHO_OVERLAP` is renamed `ECHO_RUN_FRACTION` because it
  no longer measures an overlap; **the value is unchanged at 0.7**. The docstring
  now records what the detector has to be told apart from, with both real failures
  in it, because that is the half that was got wrong.
- `backend/tests/test_health.py` — nine tests. Both real greetings and the real
  fragment, verbatim from the transcripts, are not echo. Three constructed
  echoes still are, including one of the greeting itself so the fix cannot be an
  exemption for greetings. The thinking-repeat case that the threshold exists to
  protect still passes.
- `REPO_MAP.md` — the constants table.

**Why sequence, and not the other candidate**

The alternative was to ignore ubiquitous words and require the overlap to fall on
distinctive ones. It fixes the greetings, and it fails on the fragment: "working"
is not a common word, so "I'm working on" still scores 100 percent. It also needs
a list of which words count as ubiquitous, invented with no measurement behind it.

Sequence needs no new vocabulary and describes what echo physically is: our own
audio returning, which survives a bad microphone dropping words but not
rearranging them. Measured on the real transcripts, the false cases score 8 to 50
percent and a genuine return scores 100. The threshold is not doing the work,
which is the difference between separating two cases and moving a line between
them.

**What changed in the reports**

Checked against all six stored interviews rather than assumed.

- Echo now fires **zero** times across all of them, down from three.
- The clause "was heard through their own speakers" is gone from all three
  reports that carried it.
- **No report changed whether it reads as degraded.** All six were already
  degraded on `disconnects`, and still are. The false clause was decorating a
  conclusion that other evidence had already reached, which is why nobody noticed
  it was false.

Stored reports re-validate when they are read, so an existing report picks this up
the next time it is opened, with no rescoring and no model call.

**Not done**

- **There is no real echo in any recording we have, so the detector's accuracy on
  real echo is still unmeasured.** The true positives in the tests are
  constructed. They are what echo looks like, not what it looked like. This
  change can be shown to have stopped the false positives; it cannot be shown to
  still catch the real thing, and the tests say so in their own docstrings.
- All six stored interviews report exactly one disconnect and are therefore all
  degraded. That looks like the candidate leaving at the end being counted as a
  dropped call, which would be the same shape of fault as this one. Not
  investigated here.
- Nothing else in the health assessment was touched: `FRAGMENT_WORDS`,
  `_ACKNOWLEDGEMENTS`, `PROMPTING_STAGES` and the wording of the sentence are
  unchanged.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                    # 534 passed, 19 deselected
uv run pytest tests/test_health.py -q
```

Open a stored report that previously said the candidate was heard through their
own speakers. The clause should be gone and the rest of the sentence unchanged.

---

## 2026-08-03 — Interview length can be chosen, and nothing claims to change it any more

**What**

An interview can run between 20 and 90 minutes. Nothing ever asked the employer
which they wanted, so every interview quietly took the 40-minute default. If an
employer said what they wanted anyway, one of two things happened: a length
inside the range was ignored, and a length outside it produced a validation
error on screen and then trapped the profile in a loop it could not leave.

The setup conversation now asks how long the interview should run, states the
range while asking, and refuses to agree to a number it cannot build. A refused
number is explained in a sentence and the conversation carries on.

Separately, the plan-refinement chat used to say it had shortened an interview
when it had not and could not. It now says plainly that length is not changed
there, and points at where it is.

**Why**

This was found the worst way round. An employer asked for a five-minute
interview. They were told it had been done. The bot then opened the call by
telling the candidate it would take about forty minutes, which was correct: it
was reading the real stored value, and the real stored value had never changed.

The failure inside the setup conversation had two halves. Asked for five
minutes, the assistant negotiated in good faith, said "noted", and asked a
sensible follow-up about which competencies to drop to fit. Then it produced a
plan that could not exist, and the employer was shown this:

```
Model produced an invalid EvaluationSpec: 1 validation error for EvaluationSpec
duration_minutes Input should be greater than or equal to 20
[type=greater_than_equal, input_value=5 ...]
For further information visit https://errors.pydantic.dev/...
```

A recruiter, sent to a Python library's documentation, by a message that never
states the actual rule. And then it was a dead end: the exchange where the
assistant agreed to five minutes stayed in the conversation, so every retry
rebuilt the same impossible plan and produced the same error. Four different
replies produced four identical failures, then four server errors. The only way
out was to abandon the profile.

The refinement bug is the more dangerous shape of the same thing. Asked to fit
five minutes, it replied that it had lowered every competency's emphasis and cut
each to a single seed question "so the interview can fit into five minutes". It
had genuinely cut the questions, and said so accurately. It had not changed the
length, and could not: emphasis divides a fixed total between competencies, so
lowering all of them by the same amount divides the same minutes the same way.
Sections stayed at 8.5, 5, 7, 5, 7 and 3.5, summing to the same forty. **A real
change, correctly described, with a false conclusion attached.** The employer
sees one thing move and reasonably assumes the rest moved with it.

**This is the fourth time this codebase has reported success on something that
did not happen.** The judge that failed silently and left an interview looking
like a quiet candidate. The report that could not be read and displayed as "not
scored yet". The refinement above. And a setup conversation that said "noted" to
a length it was about to refuse. The shape is always the same: a failure wearing
the appearance of an ordinary state, which is worse than an error, because
nobody investigates a success. It is worth recognising on sight.

**How**

- `backend/blueprint/clarify.py` — the prompt now requires the duration question
  and supplies the wording, states the range in the same sentence, and forbids
  agreeing to anything outside it. Written to leave no alternative rather than to
  discourage one, per DECISIONS.md. A spec that fails validation now raises
  `SpecRejected` carrying a sentence for an employer, and `next_turn` turns that
  into an ordinary assistant turn rather than an error.
- `backend/blueprint/refine.py` — the prompt states that length cannot be changed
  there and where it can, and that the reply describes what changed rather than
  what it hopes that achieves, with the observed false sentence as the
  counter-example. The fixed length is also put in the model's context as a fact.
- `backend/app/main.py`, `frontend/admin/admin.js` — the panel shows the allowed
  range beside the length. The numbers are substituted at serve time from the
  contract, the same way the product name already is, so the panel cannot state a
  rule the backend does not enforce.
- `backend/tests/` — 14 tests across `test_clarify.py`, `test_register.py` and
  `test_copy_style.py`.

**How the dead end was broken**

Worth stating on its own, because there was a choice. The alternative was to
quietly clamp an out-of-range number to the nearest legal one, which would have
been the same bug this entry is about: doing something other than what was asked
and not saying so.

Instead the refusal becomes a normal turn in the conversation. It is stored like
any other assistant message, so the next attempt has the correction in its own
history and cannot rebuild the same plan from the same context. The mechanism
that caused the loop is the mechanism that ends it, and no new path was added.

**Not done**

- Duration still cannot be edited directly. It is changed by reopening the spec
  and saying so, which is a conversation rather than a field. That is consistent
  with how the rest of the spec works, and with how a change propagates to
  candidates who have not been interviewed. A direct control would need its own
  answer for propagation.
- `EvaluationSpec.competencies` has no minimum length, so a spec with zero
  competencies validates. Found while testing this and left alone: it is the
  contract, and changing it was out of scope here.
- Competency weights are normalised before validation, so an out-of-range weight
  is repaired rather than refused. Also found while testing, also left alone.
- The refinement's truthfulness rests on the prompt. The code already makes a
  length change impossible; nothing verifies that the sentence it writes matches
  what it did.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                                     # 525 passed, 19 deselected
uv run pytest tests/test_clarify.py tests/test_register.py -q
```

Create a profile and ask for a five-minute interview. The assistant should say 20
is the shortest it can set up and offer it, the conversation should continue, and
a length you then choose should appear on the profile and in the bot's opening
line.

---

## 2026-08-03 — Ending the interview now asks first

**What**

The candidate's page has a button that ends the interview. It used to end it the
moment it was pressed. It now asks whether they are sure, and nothing happens
until they say yes a second time. The option that keeps the interview running is
the plain one, and it takes the keyboard focus.

**Why**

Pressing that button cannot be undone. The interviewer leaves the call, the
transcript is written, and the interview is finished. There is no way back in.

The button sat next to Mute, in red, on a page someone is looking at while being
interviewed for a job. A candidate reaching for the wrong control lost their
interview, and nothing stood between the two.

The wording is kept calm on purpose. Somebody pressing that button may be
stressed already, and a page that shouts at them for it would make a bad moment
worse. It states the consequence once, plainly, and lets them choose.

**How**

- `frontend/candidate/index.html` — the control row hides and a confirmation
  block takes its place, with "No, keep going" and "Yes, end it". Only the second
  leaves the call. "No" restores the controls and changes nothing. Focus moves to
  the safe option when the block appears, so a stray Return does not end the
  interview.

**Not done**

- The bot's own handling of a candidate who *says* they want to stop is
  untouched. That path is deliberate, measured, and different: it offers once,
  takes a repeat as a yes, and never asks a third time. This change is only the
  button.
- There is no keyboard shortcut and no Escape handler on the confirmation. Escape
  is worth adding as a way to back out, and was left rather than guessed at.

**Verify**

```bash
cd backend
uv run pytest tests/test_copy_style.py -q   # candidate page copy, no dashes
```

Open a session, press "End interview", and confirm the interview keeps running
until "Yes, end it" is pressed.

---

## 2026-08-03 — The transcript was being cut in half, and it was a timeout

**What**

When the candidate finished speaking, the system waited a fixed moment for the
final text to arrive before treating the turn as over. That wait was set to
0.35 seconds. Across two real interviews the text never once arrived that fast:
the quickest was 0.46 seconds and the slowest 0.52. So the wait ran out every
single time, and the sentence was treated as finished while the rest of it was
still coming.

The wait is now 0.65 seconds, which clears the slowest measurement with room to
spare. Nothing else about turn-taking changed.

**Why**

This did not look like a timing problem, which is why it survived two
interviews. It looked like the system mishearing an accent.

The chain runs like this. The wait expired before the words were complete, so one
spoken sentence was recorded as several separate turns. Words at the point where
it was cut came out wrong, and the wrong ones looked exactly like accent errors:
"session management" was recorded as "recession management", "concurrency
control" as "congruency control". Worse, the number of turns is what the system
uses to decide when it has heard enough about a topic, so one sentence counting
as three made topics end early.

A candidate scored 2.0 out of 5 on technical depth partly because the record did
not contain what they had said.

What ruled out the accent explanation was running the same speaker saying the
same phrases through the transcription service in one batch, with no live timing
involved. None of the errors appeared. The words were never the problem; the
clock was.

The old number came from Pipecat's default. A 63-second trial session had
measured 0.31 seconds, which appeared to leave a margin, and the note in the code
said plainly that it should be set from a real session once one existed. Two now
do, and they say something different. The measurements:

```
0.46  0.46  0.46  0.47  0.47  0.47  0.47
0.48  0.48  0.48  0.48  0.49  0.51  0.52

median 0.475   p90 0.51   max 0.52   min 0.46   n=14
```

**Raising it costs nothing.** The transcription service announces when it has
finished, and the turn ends on that announcement. The number here is only a
backstop for when the announcement never comes. If it arrives at 0.48 seconds the
turn ends at 0.48 seconds whether the backstop is 0.35 or 0.65. All that changes
is what happens when something goes wrong.

**How**

- `backend/bot/services/stt.py` — `DEEPGRAM_FINALIZATION_WAIT_SECONDS` 0.35 to
  0.65. The docstring now records the fourteen samples and their distribution,
  that this replaces the vendor default on measurement exactly as the file said
  it should, that the samples were taken in India and that this figure includes a
  network round trip so another deployment may measure differently, and what
  would change the answer.
- `backend/tests/test_stt.py` — a new test that the wait clears its measured
  maximum, matching the one that already guards the OpenAI figure.
- `CLAUDE.md`, `docs/DECISIONS.md` — both said 0.35 was a deliberate margin over
  a measured 0.31 while the code said it was an untuned default. The measurement
  settles it in the code's favour. Both now carry the new figure and the new
  evidence, and keep the 0.31 as what it was: one short session on a different
  network that turned out not to be representative.
- `REPO_MAP.md`, `HANDOFF.md` — this was listed in both as an open disagreement
  between the code and the docs. Struck, with a note on how it resolved.

**Not done**

- **This has not been confirmed on a live session.** The number is right against
  fourteen samples of the failure, but the fix itself is unobserved. The
  confirmation is a re-measurement: run a real interview and check that
  `stt_lag` in `session_metrics` now falls inside the wait rather than above it,
  and that a spoken sentence arrives as one turn instead of three.
- Nothing about endpointing changed. `SMART_TURN_STOP_SECS`, the VAD settings and
  the OpenAI wait are a different layer solving a different problem, and were
  left alone.
- Keyterm prompting was investigated as a possible fix for the same symptom and
  is not part of this change. It may still be worth doing for genuine domain
  vocabulary, but it was not the cause here.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                 # 507 passed, 19 deselected
uv run pytest tests/test_stt.py -q
```

After the next real interview, read `session_metrics.latency_summary.stt_lag_max_s`
for that session. It should sit below 0.65. If it does not, raise the constant to
clear it rather than assuming the samples were unlucky.

---

## 2026-08-03 — The warning banners are written once and sent to the panel

**What**

A report can carry two warnings at the top: one saying the recording was poor,
one saying our own analysis of the interview did not finish. The first of those
was written out twice, once in the backend and once again in the browser, and
the two copies had quietly grown apart. They are now written in one place and
the panel displays whatever it is handed. The second warning is now displayed
too, which it never was. If both apply, both appear.

The wording did not change. The backend's version is what the scoring step
already used, so it was kept exactly as it was and the browser's copy deleted.

One other thing was fixed while it was open. A report that is saved but cannot
be read back was being shown as "scoring the transcript", which reads as work in
progress. It now says it could not be opened and offers to rebuild it.

**Why**

Two copies of a sentence do not stay the same sentence. These had already
drifted in three places:

| | backend | browser |
|---|---|---|
| echo | "was heard through their own speakers" | "was heard through their own speakers rather than headphones" |
| silences | "had 3 long silences" | "went quiet 3 times long enough to be prompted" |
| closing | "Where the **evidence** below is thin ... not a conclusion about **the candidate**." | "Where the **scores** below are thin ... not a conclusion about **them**." |

So the model doing the scoring was told one thing about a recording and the
employer read another, both presented as a description of the same call.

There was also a disagreement that was not about wording. When none of the
individual observations apply, the backend says nothing at all, and the browser
fell back to asserting the candidate "could not be heard clearly" — a claim
about a person that no measurement supported. That state looks unreachable given
how the warning is switched on, but the two files disagreeing about it is
exactly the problem: nobody had decided, so each guessed differently.

The browser's copy carried a comment explaining itself: built there "so the panel
can word it its own way". It never did word it its own way. It worded it almost
the same way, slightly wrong, and drifted. That intent is overturned here
deliberately. The cost had also just doubled, because the second warning would
have needed the same duplicate treatment.

The dropped-report display is the same shape of fault as everything else in this
sequence: a system failure wearing the appearance of a normal state. "Scoring
the transcript" says work is under way, so nobody retries it and nobody looks.

**How**

- `backend/shared/contracts.py` — `ConversationHealth.as_sentence` and
  `JudgmentHealth.as_sentence` become computed fields, so they serialise into
  the report and reach the panel alongside `degraded`, which was already one.
  Both sentences are byte for byte what they were.
- `backend/feedback/score.py`, `backend/scripts/unheard_candidate_run.py` — the
  call sites become attribute access. No scoring logic changed.
- `frontend/admin/admin.js` — `healthSentence()` deleted. A new `banner(health)`
  renders any health record that is degraded and has a sentence, and the report
  card calls it twice, for the recording and for the analysis. `esc()` is still
  applied: it is server data now, but it still goes into `innerHTML`. The
  unreadable-report branch is new.
- `backend/tests/test_feedback.py` — four tests: both sentences serialise; a
  healthy report carries none; a stored report keeps its sentence through a
  round trip; and the panel no longer contains a sentence-building function or
  any of the wordings that drifted.
- `backend/tests/test_interviews.py` — two tests: the API distinguishes a
  dropped report from an unscored one, and the panel acts on that distinction.

**Not done**

- **No new API field was needed to tell a dropped report from an unscored one.**
  `feedback_status` is only set to `ready` immediately after a report is stored,
  so `ready` with no report means it was dropped on read. The panel keys on that
  pair. `feedback_error` was left alone: it means scoring failed, which is a
  third thing.
- The computed field is named `as_sentence`, which reads oddly as a JSON key. It
  is what the contract already called it and renaming would have touched every
  call site for cosmetics. Worth changing if the contract is ever versioned.
- The panel is still plain JavaScript assembling HTML strings. Nothing here made
  that better or worse, and no templating layer was introduced. Two sentences is
  not a system.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                     # 506 passed, 19 deselected
uv run pytest tests/test_feedback.py tests/test_interviews.py -q
node --check ../frontend/admin/admin.js
```

Open a report degraded on both counts and two banners appear, recording first,
analysis second. Neither is composed in the browser: both strings come from
`conversation_health.as_sentence` and `judgment_health.as_sentence` on the
report.

---

## 2026-08-03 — Stored reports are checked against the current shape when read

**What**

A finished report is saved once, as a block of data, and never rewritten. Some
of the things a report contains are not stored but worked out from the numbers
next to them, and that working out happens at the moment it is saved. So if we
later add something new that a report should say, every report already saved
stays silent about it forever, even though the numbers it would be worked out
from are sitting right there.

Reading a report now runs it back through the current definition first, so those
worked-out parts are filled in from what was stored. Nothing is rewritten and no
saved report is altered. If a saved report turns out not to fit the current
definition at all, the rest of the interview still opens normally and the report
alone is left out, with a note in the log saying which interview and why.

**Why**

This unblocks the next change, which moves the wording of the report's warning
banners out of the browser and into one place.

Without it, that change would break exactly the reports it was meant to help.
Whether a recording was poor is already a worked-out value and is therefore
already saved. The sentence explaining it was not. Move the sentence to the
report, delete the browser's copy, and every report saved before that day would
have arrived at the panel with the warning switched on and nothing to say: a
banner reading **"Read this first."** followed by blank space. That is a worse
failure than the drifted wording it was replacing, and it would have appeared
only on real saved reports, which are the ones nobody can test against locally.

The second reason is the one that shaped how it was built. There is no way from
here to see what the live database actually holds. A report saved months ago
under an older definition is a real possibility, and if reading it threw an
error, the entire interview record would have become unopenable: no transcript,
no timings, no status. The transcript is the thing that has to survive, so a
report that cannot be read costs the reader the report and nothing else.

**How**

- `backend/app/interviews.py` — new `_validated_report(interview_id, stored)`,
  called where the read path used to hand the raw column straight out. It
  validates through `FeedbackReport` and dumps it again, which recomputes every
  computed field from the stored counts. On any failure it returns `None` and
  logs a warning naming the interview and the error. `InterviewView` is
  unchanged and still carries a plain `dict`.
- `backend/tests/test_interviews.py` — five tests: a report saved without a
  computed field gains it on read while its stored counts stay untouched; a
  current report round-trips byte for byte; an interview with no report is
  unaffected; an unreadable report is dropped while the transcript, metrics,
  status and credentials all still come back; and the failure is logged with the
  interview id.

**Not done**

- **This was tested against constructed fixtures only.** The local database
  (`backend/interviewer.db`) is schema with zero rows, and the deployed instance
  is unreachable from here, so no report that was genuinely written under an
  older contract has ever been through this path. The guard is written on the
  assumption that such reports exist and cannot be inspected. Worth knowing if
  one ever does fail: the log line names the interview id, and the row is
  untouched, so it can be read directly.
- **The panel shows a dropped report as "not scored yet".** It has a report and
  cannot read it, which is a different thing, and the reader is not told the
  difference. Surfacing it properly would mean a new field on the response, and
  the existing `feedback_error` is about generation having failed and must not
  be repurposed to mean this. **Follow-up work.**
- Nothing was rewritten or backfilled. This recomputes on read and never writes.
  A saved report can still be brought up to date permanently by regenerating it,
  which re-scores and costs a model call.
- The panel was not touched. That is the next change.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                        # 500 passed, 19 deselected
uv run pytest tests/test_interviews.py -q
```

---

## 2026-08-03 — A report now says when its own analysis did not run

**What**

While an interview is happening, a background step reads each topic and pulls
out the specific things the candidate claimed. Those extracted claims are what
the final report points at. If that step fails, nothing is extracted, and the
report comes out looking exactly like a report on someone who spoke only in
generalities. Until now it said nothing about the difference.

A report now carries a record of how much of that analysis actually ran. When
enough of it failed, the report says so in plain language at the top, names the
topics it has no analysis for, and refuses to describe the evidence as strong.
Nothing about the candidate's answers changes. Every quote that survived is
still there, still checked against the transcript, and still means what it said.

**Why**

The previous change made this failure findable by someone who already suspected
it. The trace lived in the session's technical record, which nobody opens unless
something already looks wrong. A recruiter reading the report itself had no way
to know that half the system had not run.

That gap matters because of what fills it. A report with no extracted claims
reads as a thin candidate. The reader has no reason to doubt it, so a technical
fault quietly becomes a judgement about a person, which is the one outcome this
product is built to prevent.

This is the same problem the recording-quality check already solves for audio. A
bad microphone used to read as an incoherent answer until the report started
stating it outright and holding back its confidence. A failed analysis step is
the same class of fault one layer up, so it is handled the same way rather than
with a second invented mechanism.

**How**

- `backend/shared/contracts.py` — new `JudgmentHealth`, modelled on
  `ConversationHealth` and placed beside it: the three counts from the previous
  change, the competencies whose analysis was lost, their share of the spec by
  employer weight, and `degraded` plus `as_sentence()`. `degraded` and
  `cancelled` are computed fields rather than properties, for the same reason
  `ConversationHealth.degraded` is one: the panel reads them over JSON.
  `FeedbackReport` gains an optional `judgment_health`, independent of
  `conversation_health`. New constant `JUDGMENT_DEGRADED_ABOVE_WEIGHT = 0.25`.
- `backend/feedback/health.py` — new `assess_judgment(session_metrics, spec)`,
  next to the existing `assess` and derived the same way, from what the bot
  already wrote. It reads the counters for the totals and the `judgment` and
  `judgment_failed` events for which competency each one belonged to, which is
  what makes the weighted reading possible.
- `backend/feedback/run.py` — one line to derive it, beside the existing
  conversation-health line, off the session metrics that were already loaded. No
  new path.
- `backend/feedback/score.py` — `score`, `build_report`, `_empty_report` and
  `_recommendation` take it as an optional argument. `_recommendation` caps a
  confident signal at `limited_evidence`, in its own check next to the existing
  recording-quality cap rather than merged with it, so neither degradation can
  hide the other. No new recommendation value. Quote verification, scoring and
  every other rule are untouched.
- `backend/tests/test_feedback.py` — nine tests: a clean session is byte for byte
  what it is today; every judgement failing degrades the report and caps the
  signal; a partial failure keeps the surviving score and quote intact while
  still naming what is missing; a competency that failed once and succeeded once
  is not counted as lost; losing a light competency does not degrade the report
  but losing a heavy one does; cancelled work is not a failure; an interview
  from before any of this is not flagged; and audio and analysis degradation
  coexist without either overriding the other.

**Not done**

- **The panel does not show the new sentence yet.** It needs to appear beside the
  existing recording-quality banner in `frontend/admin/admin.js`, which renders
  at the top of the report card. It was left out deliberately: that banner's
  wording already exists twice, once in Python and once in JavaScript, and the
  two have drifted. Adding a second duplicated pair would make a known problem
  worse. The right fix is to send the sentence from the report and have the panel
  render what it is given, for both banners at once. **Follow-up work.** Until
  then the sentence is on the report data and reachable through the API.
- **The scoring model is not told.** Recording quality is passed into the prompt
  because it changes how a thin answer should be read. A failed analysis step
  does not: the transcript is complete and the model reads it directly. Telling
  it would risk making it more cautious about the candidate, which is backwards.
- Nothing under `backend/bot/` changed. The fallback from the previous entry
  behaves exactly as before.

**Verify**

```bash
cd backend
uv run pytest tests/ -q                     # 495 passed, 19 deselected
uv run pytest tests/test_feedback.py -q
```

To see it end to end, run an interview with the background model pointed at
something that does not exist, using the command in the entry below, then open
the report. `judgment_health.degraded` will be true, `unjudged_competencies` will
list every topic, the recommendation will be no stronger than `limited_evidence`,
and `judgment_health.as_sentence()` returns the line a reader should see.

---

## 2026-08-03 — Judge failures are no longer silent

**What**

During an interview, a second background model reads what the candidate has just
said and pulls out the specific things they claimed, so the interviewer can refer
back to them later and so the final report has something concrete to quote. That
background model sometimes fails, usually because it has been pointed at a model
name that does not exist. When it failed, it failed completely silently. The
interview carried on and finished normally, and nobody watching had any way to
know that half the system had stopped working. This change does not alter what
happens when it fails, which is correct behaviour. It only makes the failure
leave a trace: a line in the log, an entry in the session record, and a running
count of how many attempts succeeded and how many did not.

**Why**

A failure nobody can see is worse than a failure that stops things, because you
find out about it much later and from the wrong evidence. When that background
model dies, the interview produces no extracted claims, nothing is carried from
one topic to the next, and no inconsistencies are noticed. The finished report
then reads exactly like a report on a candidate who never said anything
specific. That is a written judgement about a real person, and the cause is a
configuration mistake nobody was told about.

It was invisible in three separate places at once, which is why it survived. The
background call caught its own errors and quietly returned nothing. The code
above it had a warning for exactly this, but the warning could never run, because
nothing was ever passed up to it. And the session's event list, which exists so
you can tell a busy interview from a broken one, only recorded successes.

Counting attempts and failures alone was not enough either. When an interview
ends, any background work still running is stopped, which is correct. But a
stopped attempt was recorded as neither a success nor a failure, so it was
indistinguishable from one that worked — and the work most likely to be stopped
is whatever was running as the interview closed. Counting successes separately
closes that hole: whatever is left over is work that was cut short.

The setting that causes it makes this worse. It is named for offline work, so a
wrong value there does not look like something that could reach a live
interview. It can, and does.

**How**

- `backend/bot/brain/drivers.py` — the background call now logs a warning before
  returning nothing, naming the section it was assessing and the model it was
  using. It still returns nothing, so the fallback is untouched. Adds a `loguru`
  import, already used elsewhere in this package.
- `backend/bot/brain_director.py` — when the background call comes back empty,
  this now records a `judgment_failed` event with the section and the kind of
  work requested, instead of returning without a word. The pre-existing handler
  for a call that raises does the same and also records the exception type. Both
  handlers were kept separate. Three counters were added, attempts, successes and
  failures, exposed through a new `judgment_summary()` shaped like the existing
  `RoomPresence.summary()`. Successes are counted on the success path, next to
  the existing event. The three do not have to add up, and the docstring says so:
  `attempted - succeeded - failed` is work cancelled by `cleanup()` at pipeline
  teardown. `asyncio.CancelledError` inherits from `BaseException`, so neither
  handler catches it and neither should — the cancellation is correct, only the
  count was dishonest. The gap must never be read as success.
- `backend/bot/run_bot.py` — spreads `judgment_summary()` into the session
  metrics written to the database, next to the latency numbers, in the same way
  room presence already is. Also records `judge_model`, alongside the existing
  `llm_model` and `stt_provider`, so a session record says which model the
  background work was pointed at.
- `backend/tests/test_brain_director.py` — eight tests: a failing call is logged
  and recorded, both when it raises and when it returns empty; the interview
  still moves through its sections when every one of them fails; the counter
  increments per attempt; a working call records no failures and increments
  successes; a mix of working and failing calls moves all three counters
  independently; and attempts equal successes plus failures whenever nothing was
  cancelled, which is what would catch a future outcome path that counts an
  attempt and then records nothing.
- `backend/tests/test_drivers.py` — new file. Four tests covering the background
  call's own error handling: it still returns nothing, it says so, the message
  names the section and the model, and failed attempts are still counted. This
  file's subject previously had no tests that ran without a real model.

**Not done**

- **Behaviour is unchanged, deliberately.** The interview still continues and
  still falls back to simpler rules when the background work fails. That was
  never the bug.
- **The report does not yet say its confidence is reduced when this happens.**
  It should: a report built with no extracted claims is thinner than it looks,
  and the reader cannot currently tell. That is the right next step and is
  deliberately not in this change, because it touches how a report is scored and
  presented, which deserves its own review. **Follow-up work.**
- The setting was not renamed and the model tiering was not changed.
- The two exception handlers were not merged. They catch genuinely different
  situations: one for a background call that handles its own errors, one for a
  substitute that does not.
- Nothing else under `backend/bot/brain/` was touched.

**Verify**

```bash
cd backend
uv sync
uv run pytest tests/ -q                                  # 486 passed, 19 deselected
uv run pytest tests/test_drivers.py tests/test_brain_director.py -q
```

To see it end to end against a live interview, point the background model at
something that does not exist and run a session:

```bash
cd backend
OPENAI_BLUEPRINT_MODEL=gpt-4.1-does-not-exist \
  PYTHONPATH=. uv run python scripts/dev_interview.py
```

Join the printed URL, talk for a couple of minutes, then Ctrl-C. You should now
see `judgement failed for section ... on model 'gpt-4.1-does-not-exist'` in the
terminal. In the database, the interview's `session_metrics` will carry
`judgments_attempted` and `judgments_failed` as equal non-zero numbers with
`judgments_succeeded` at zero, and `brain_events` will contain `judgment_failed`
entries. Before this change all of it was absent and the session looked healthy.

On a healthy session the three numbers tell you how much landed: successes plus
failures short of attempts means the rest were cut off when the interview ended.
