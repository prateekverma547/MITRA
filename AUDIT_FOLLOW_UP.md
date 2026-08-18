# Follow-up audit: the follow-up question machinery

**Scope.** Everything that decides what the interviewer asks next: the judge, term
state in the brain, every prompt block that can supply a subject, how those
blocks interact, the section advance that resets the allowance, and the harness
and signals used to measure any of it.

**Read at** `stg`, HEAD `49efed4`, with the five small fixes and the split-screen
layout uncommitted. Line numbers are as of that tree.

**Method.** Static reading, plus stored artefacts: the ten-run `s2s11` arm (the
current tree), the `db2`, `fixed` and `before` arms, the run logs which record
every sanctioned term probe, and the real interviews in `backend/interviewer.db`.
Transcripts were read directly rather than through the signals, because three
signals were wrong last week. Observed and inferred are marked.

---

# Part 1 — Findings, worst first

## F1. Two mechanisms nominate terms and only one of them is counted

**NOT WORKING.** This is the central finding and most of the rest follows from it.

The term block (`_decided_question_late`, `prompting.py:520`) is budgeted: two per
section (`MAX_TERM_PROBES_PER_SECTION`, `brain.py:123`), eight per interview
(`MAX_TERM_PROBES_PER_INTERVIEW`, `brain.py:97`), two asks per term
(`MAX_TERM_ATTEMPTS`, `brain.py:70`), then a spoken let-go.

The depth ramp nominates terms **independently and is counted by nothing**. At
`turns_spent == 1` (`prompting.py`, `=== MANDATORY: STAY ON THE ANSWER THEY JUST
GAVE ===`):

> "If they named a technique or system without saying what it does, that term is
> your question. Say something close to this: *You said you used X. Walk me
> through how that actually works.*"

and again at `turns_spent == 2` ("FIRST: did their last answer name a technique
or system without saying what it does? If so, that term IS your question").

That is the same sentence the term block supplies, produced by a different block,
with no raise count, no per-section allowance, no interview budget and no let-go.

**Observed.** Across the current 10-run arm, **89 probe-shaped questions** in the
transcripts against **67 the brain recorded** as spent. Ten of sixty sections
contain more probe-shaped questions than the per-section cap of two allows, the
worst holding six.

The clearest case is `s2s11/competent__gpt-4.1-mini.json`, `applied_llm_depth`:

```
turns_spent=2  PROBE  You said fine-tuned. Walk me through how that actually works.
turns_spent=3  PROBE  You said bf16. Walk me through how that actually works.
turns_spent=4  PROBE  You said Mistral-format jinja template. Walk me through how that actually works.
turns_spent=5  PROBE  You said Mistral-format jinja template. Walk me through how that actually works.
turns_spent=6  PROBE  You said Mistral-format jinja template. Walk me through how that actually works.
turns_spent=7  PROBE  You said Mistral-format jinja template. Walk me through how that actually works.
```

Six probes, four of them the identical sentence, and **the run log records no
term probe for that section at all** — the first logged probe of that run is in
`evaluation_reasoning`. The counter never moved, the cap never bound, the let-go
never fired, and the candidate was asked the same question four times running.

**The accounting is keyed to the wrong thing.** `_count_if_it_asked`
(`brain.py:1113`) increments only when the spoken turn mentions `_offered_term`,
the single term the prompt named. If the model unpacks a *different* term, the
question happened and nothing was counted. So the caps bound one term's
appearances in one section, not term-probing.

## F2. The depth ramp forbids at stage three what it requires at stages one and two, and is ignored a fifth of the time

**NOT WORKING, and it is the block contradicting itself rather than two blocks
contradicting each other.**

From `turns_spent >= 3` the ramp says:

> "You have already unpacked a term on this topic. Do not unpack another, and do
> not ask again about anything you have already asked about here. This turn, ask
> for {wanted}"

**Observed**, current arm, over 205 turns at `turns_spent >= 3`:

| | |
| --- | --- |
| unpacked a term anyway | **52 (25%)** |
| asked for the demanded thing | 103 (50%) |

`db2` reads 19% and 55% on 196 turns. So the one instruction in the prompt that
explicitly forbids repetition is violated on a fifth of the turns it governs, and
obeyed on half.

The sequence above is what that looks like: the ramp demanded a number, then a
trade-off, then what went wrong, then a decision, across turns 3 to 7, and got
four identical term probes instead.

**Inference, not observation:** the most likely source of those four is the
verbatim window. Eight turns of this section are in front of the model, four of
them the same question, and nothing in the prompt is stronger than a pattern the
model can see itself having established. I cannot separate this from the ramp's
stage-1 nomination by reading alone. **What I would run:** the same ten runs with
`_render_depth_ramp` returning `""` at `turns_spent in (1, 2)`, leaving everything
else alone, and count probe-shaped questions. If they fall to roughly the 67 the
brain sanctions, the ramp is the source; if they stay near 89, it is imitation.

## F3. A term gets a fresh allowance in every section it survives into

**WRONG PLACE.** Per-section scoping was chosen to stop cross-section drift
(`state.py`, `term_raises`). For a term the judge keeps nominating it produces
exactly that.

**Observed**, per interview (I checked this per-run after first mis-reading the
aggregate, and the per-run figures are the honest ones): no section exceeds two
sanctioned probes and no interview exceeds eight, so **the accounting is correct
as written**. What it permits is the finding: one term spanning **three sections**
in both arms, taking a fresh allowance of two in each, so a single term can
consume six of the interview's eight probes.

`s2s11.log`, one run: `'Mistral-format jinja template'` in `applied_llm_depth`,
then `'Mistral jinja template'` in `ml_service_deployment`, then again in
`client_communication`. Two strings for one idea, each with its own allowance,
which is the failure `MAX_TERM_PROBES_PER_SECTION` was written to bound and which
it bounds only within a section.

`_open_terms` is still stale at a boundary (`brain.py:890`, documented in its own
docstring), so the term offered on a new section's first turn can be the previous
section's, with `times_raised` refreshed to zero by the false-escalation fix. The
refresh made the *sentence* honest; it did not stop the carry.

## F4. `probe_mechanism` cannot see an escalated term probe

**NOT WORKING.** The guardrail number quoted in every measurement this week.

`scripts/compare_models.py` defines `_MECHANISM_CUES` **twice**, at line 266 and
line 391. The second shadows the first. The first contains
`"what does it actually do"`; the second does not.

That string is the term block's escalation sentence, the second ask, the thing
the term machinery exists to produce.

**Observed:** 19 escalated probes in the current arm, **0 counted**, 19 missed.

So "mechanism questions per opportunity", the number that has gated three changes
this week at 24%, 16%, 20% and 13%, systematically omits every second ask. Its
direction may still be informative; its level is not.

`_MENU_CUES` is broad in the other direction: `"how did you measure"` and
`"what metric"` are also the seed questions of `evaluation_reasoning`, so a
section asking its own material scores as menu.

## F5. Repetition that remains is almost entirely term probing

**Observed, and it narrows the problem usefully.** Current arm, 384 competency
questions, exact repeats within a section:

| gap between the two askings | count |
| --- | --- |
| 1 turn | 6 |
| 2 turns | 7 |
| 3 turns | 4 |
| 4 turns | 1 |
| 5 turns | 1 |
| **total** | **19 of 384 (5%)** |
| **of which term-probe-shaped** | **17** |

Two consequences. The seed rotation's wrap, which cost three gap-4 repeats when
it was introduced, now accounts for **one**. And nine attempts at "the repetition
problem" have been aimed at seeds, ramps, carried claims and section state, while
17 of 19 exact repeats come from one mechanism.

## F6. Four blocks can claim the turn and the arbitration is partial

**WRONG PLACE.** Assembly order, `prompting.py:1258`:

```
header · refusal · CONTRADICTION · candidate_context · body · TERM/LET-GO ·
carried claims · time pressure · guidance · red flags · scope
```

and inside `body`: footing, section name, target depth, **dealbreaker**,
**seed question**, **depth ramp**.

Four blocks are mandatory-shaped, meaning they open by removing the model's
discretion:

| block | says | gated by |
| --- | --- | --- |
| contradiction | `=== MANDATORY: THIS TURN'S QUESTION IS ALREADY DECIDED ===` | probed once each |
| term | same header | 2/section, 8/interview |
| let-go | `=== MANDATORY: LET THIS ONE GO ===` | replaces the term block |
| depth ramp, turn 1 | `=== MANDATORY: STAY ON THE ANSWER THEY JUST GAVE ===` | **nothing** |

Arbitration exists between the first three and is deliberate and documented:
`_decided_question_late` returns `""` when a contradiction is present, and the
let-go precedes a new term. **The ramp is outside that arbitration entirely.** At
`turns_spent == 1` both the ramp and the term block can be mandatory in the same
prompt, pointing at possibly different terms, with nothing choosing between them.

Two stale cross-references, both introduced by this week's own work:

- The ramp at turn 1 says *"do not move to any question listed below"*. The seed
  question is rendered **above** the ramp inside `body`. Whatever it is pointing
  at, it is not the seeds.
- `_render_red_flags` (`THINGS WORTH NOTICING`) lists every dealbreaker and says
  *"probe it gently and factually. Ask for the specific example that would settle
  it"*, while `_render_dealbreaker` says of the same dealbreaker *"It is a
  standard, not a question: never read it out or say anything is being checked."*
  Recorded in the first audit, still present, and the word "also" in the second
  block is false.

## F7. The judge's output is used in four ways with four different persistence rules

**RIGHT PLACE, worth writing down because nobody has.** `drivers.py:255` returns
one object; `apply_judgment` (`brain.py:771`) treats each field differently:

| field | rule |
| --- | --- |
| `coverage` | overwrites, unless the section is closed and already has a verdict |
| `rationale` | same rule, and **nothing ever reads it** |
| `claims` | appended, deduped by exact text |
| `contradictions` | appended, deduped by `later_statement` |
| `unexplained_terms` | **replaced wholesale**, never merged |

The replacement rule is the one the interviewer depends on: a term dropping off
the judge's list is the only way the brain learns to stop asking. It is stated in
the prompt (`drivers.py`, "Your list is REPLACED each time you are asked") and in
the code, and the two agree.

**Cadence:** requested after every candidate answer in a competency section
(`_maybe_request_judgment`, `brain.py:813`), single-flight, never awaited. Plus
one `section_end` request per section from `_advance`. So a six-turn section costs
seven judge calls.

## F8. The judge is asked the wrong question at section end

**WRONG PLACE.** Recorded in the first audit, unfixed, and it interacts with F3.

`_advance` runs `_carry_claims_from(section)` **before** building the
`section_end` request with `carried_claims=list(self._carried_claims)`. The
section's own claims therefore reach the judge labelled "CLAIMS FROM EARLIER
SECTIONS" (`drivers.py:262`). The judge re-reads the same transcript with its own
claims presented as history.

Since that judgement also returns `unexplained_terms`, and those land in
`_open_terms` for whatever section comes next, this is one of the routes by which
a term crosses a boundary.

## F9. What resets, and what does not

**Enumerated because two bugs of this shape have already been found.**

| state | scope | resets when |
| --- | --- | --- |
| `Section.term_raises` | per section | new `Section` object, so at every advance |
| `Section.closed_terms` | per section | same |
| `Section.term_probes_spent` | per section | same |
| `_term_probes_spent` | interview | **never**, correct |
| `_explained_terms` | interview | never, correct |
| `_let_go_said` | interview | never |
| `_open_terms` | current section | **only in `apply_judgment`, and only for the section being judged** |
| `_asked_since_judged` | between verdicts | cleared in `_note_what_they_explained` |
| `_offered_term` | per render | every `_render_system_instruction` |

The one that is still wrong is `_open_terms`: it is the previous section's list at
a new section's first turn (F3). Everything else resets where it claims to.

Two of this shape are already fixed and both were the same bug: `times_raised`
read before its refresh, and `coverage` written after its section closed.

## F10. Signals: which can be trusted

| signal | verdict |
| --- | --- |
| `probe_mechanism` | **cannot be trusted at level.** Misses every escalated probe (F4) |
| `probe_menu` | broad. `"how did you measure"` is also `evaluation_reasoning`'s own seed |
| `probe_new_topic` | untested here; it keys on seed vocabulary, which the candidate also uses |
| `seed_overlap_rough` | **documented as unreliable by its own docstring**: 45 found where a person found 55, three false positives scoring the acknowledgement rather than the question |
| `probe_terms_specific` | proxy is "has a digit or an internal capital". Rewritten once after being inverted. Unvalidated since |
| `REPEAT_OVERLAP = 0.6` (`health.py:238`) | reads 5% on the current arm where duplicate *answers* read 58%. Measures near-verbatim repetition, not subject repetition, and only the first was ever claimed |
| exact-repeat count | **trustworthy**, string equality, no threshold |
| duplicate authored answers | **trustworthy**, fixture ground truth, no similarity rule |
| sanctioned probes from the run log | **trustworthy**, emitted by the brain at the moment it counts |

Three of the nine can carry a conclusion. The audit above uses only those three.

## F11. What the fixture can and cannot show

**Observed.**

- The competent candidate holds **31 authored answers** and its selection is a
  pure function of the question, by design (`harness.py:_selected`). Same question,
  same answer, always.
- A run reaches **11.5 of 31** on average, range 8 to 16.
- **72% of its answers repeat verbatim** within a run, because the interviewer
  returns to the same subjects.

What this means for any measurement taken today:

- **Can see:** whether the interviewer returns to a subject, because a repeated
  answer is hand-authored ground truth that it did.
- **Cannot see:** whether a candidate would have explained a term on the second
  ask. The fixture's answer to a given question never changes, so "asked twice and
  still unexplained" is decided by the fixture, not by the exchange. Every number
  about escalation and let-go inherits that.
- **Cannot see:** anything about pacing. The harness charges a flat 22s a turn;
  real interviews run 20.0s median with a 7 to 37 range, and sections end on the
  ceiling here against the budget in reality.
- **Cannot see:** a candidate who rephrases. A real candidate who says the same
  thing differently would change the judge's verdict; this one cannot.

The three past fixture failures were all of this kind, and the current fixture
still shapes the answer in the second bullet, which is precisely the mechanism
under audit.

---

# Part 2 — What a fresh reader would conclude the design is

Written as asked, in one paragraph, from the code alone.

> The interviewer asks the section's own questions, one per turn, in the order the
> blueprint wrote them, escalating in depth as the section proceeds. Three things
> may interrupt that: a contradiction, which is raised once and takes precedence
> over everything; a term the candidate used without explaining, which is asked
> about at most twice and then explicitly let go of; and the section running out
> of turns or time. The judge, off the critical path, decides after every answer
> which terms remain unexplained and how much of the section has been covered, and
> the interviewer is bounded so that no single word can consume a topic.

That paragraph is recoverable, it is coherent, and **the code does not implement
it.** The gap is not subtle: the depth ramp is a fourth interrupter that the
paragraph does not mention and that the bounding does not cover, the caps bound
one term's appearances rather than term-probing, and a term that survives a
section boundary starts again. A reader who believed the paragraph would predict
at most two probes per section and would be wrong in ten of sixty.

So the honest answer to "is the intended design recoverable" is: **yes, and that
is the problem.** Every previous fix has been consistent with the paragraph and
has moved a number, because each addressed a real mechanism inside it. The
transcripts did not change because the behaviour is produced by a mechanism the
paragraph does not describe.

---

# Part 3 — Things worth thirty seconds, written down and left

- `_MECHANISM_CUES` defined twice in `scripts/compare_models.py` (266, 391).
- `"do not move to any question listed below"` in the ramp at turn 1 points at
  material now rendered above it.
- `THINGS WORTH NOTICING` and `_render_dealbreaker` give opposite instructions
  about the same dealbreaker, and "also" is false.
- `depth_rationale` is written by `apply_judgment` and read by nothing.
- `_open_terms` staleness is documented in its own docstring and still there.

# What I could not assess

- Whether the ramp's stage-1 nomination or window imitation produces the
  unsanctioned probes (F2). Needs the ablation described there.
- Anything about a real candidate's second ask (F11). Needs a live interview, and
  the only real one on the box predates most of this machinery.
- Whether `probe_new_topic` and `probe_terms_specific` measure what they claim.
  Needs the same hand-read against transcripts that `seed_overlap_rough` already
  had and failed.
