# Changelog

A running record of every change made to this codebase, newest first.

The point is that anyone can see what was touched and why without reading
diffs. Each entry says what changed in plain language, what problem it solved,
which files moved, what was deliberately left undone, and the exact commands to
confirm it works.

Write for someone who has never seen this repository. Keep jargon out of
**What** and **Why**; file names belong in **How**.

---

## 2026-08-21 — The interview page records when it is not in view, and says so

**What**

A candidate can open an LLM in another tab, read an answer and speak it. That was
done to the product deliberately and nothing noticed.

The browser now records when the interview page stops being the thing in front of
the candidate, and for how long. The consent notice says so before they join, a
short line says so again when they come back, and the panel shows a reviewer a
count and a duration beside the report.

**What this is not, and the wording matters**

It is not a proctoring system and it must not read as one anywhere in the code or
on any screen. It catches the same-device case and nothing else. A phone, a
second monitor with this window still focused, or another person in the room
produce nothing at all, and reading a second window does not even require focus.
A clean record is therefore not evidence of anything, and every place this
surfaces says so.

**There is no score, no threshold and no percentage.** A number would imply a
model nobody has validated, and the eighteen stored interviews contain no real
cheating to validate one against. What is stored is a count and a duration for a
person to read.

**The timing half was investigated and dropped.** The question-to-answer gap is
derivable from stored transcripts, and it does not separate anything: across 11
interviews and 146 exchanges the spread within one person is 4.2 times the spread
between people (mean per-interview standard deviation 1.33s against 0.32s for the
spread of per-interview medians). It is also measuring the wrong quantity, at
+0.60 correlation with the word count of the candidate's first fragment: short
first fragments give a 1.9s median gap and long ones 5.5s, so it tracks how much
they said rather than how long they thought. The longest pause in the whole
dataset, 14.8 seconds in `int_7ffd1be01f56`, precedes the candidate saying "I
don't know." No schema change fixes that.

**What is recorded, and what is deliberately not**

`visibilitychange` and `blur` both fire on an ordinary tab switch, so they are
folded into one away state rather than counted twice. One absence per departure,
with its start offset, its duration in seconds, and which event opened it.

**Watching starts when the call starts, not when the page loads.** The device
check asks for camera and microphone permission and that dialog blurs the window
on every browser, so watching earlier would put an absence on every interview
that ever ran and mean nothing by it.

**An absence still open at the end is dropped rather than closed.** Closing the
tab is how an interview ends, so recording it would put one final absence on
every single interview, which is the same as recording nothing while looking like
a finding. Both `finish()` and `pagehide` discard it.

No duration filter is applied in the page. A notification click is under two
seconds and reading an answer is much longer, and that is exactly the judgement
the reviewer is being shown the duration to make. The server drops only what is
impossible: zero or negative, or longer than any interview would run.

The failure direction is worth knowing. An absence only opens on a real event, so
a browser that reports focus oddly can leave one open and never close it, and an
absence that never closes is discarded. It under-records rather than inventing
absences, which is the right way round.

**Storage**

Its own nullable JSON column on the interview row, `focus_events`, picked up by
the existing additive migration. Not a key inside `session_metrics`, because two
different writers own them: the bot writes that column once at the end from its
own process, and would clobber anything the browser had posted during the call.

`POST /interviews/focus` is public and authenticated exactly as
`/interviews/join` is, with the candidate's own meeting credentials, and refused
with the same single message so it cannot be used to probe for valid meeting ids.
Absences are appended as each one ends rather than saved up, so a dropped
connection at the end cannot take the earlier ones with it. The stored list is
capped at 200 as a defence against a page in a redraw loop; nothing is inferred
from reaching it.

**Nothing reaches the interviewer.** No frame, no prompt, no transcript entry, no
brain state, no judge input, no scorer input. `bot/`, `feedback/` and
`shared/contracts.py` are untouched by this change, and a test asserts the stored
absences appear in neither the report nor the transcript.

**What the candidate is told**

In the notice they already read and accept, in the half that renders either way
because it is true either way:

> If you move to another tab or window, we record that the interview page was not
> in view, and for how long. We cannot see what you were looking at. This is not
> scored, and it is shown to the person reviewing your interview.

And once, when they come back, in a small line that fades after six seconds:

> The interview page was out of view for 12 seconds. The person reviewing your
> interview will see that.

It is absolutely positioned so nothing reflows when it appears, sits in the
panels' own top padding clear of the orb and a long way from the captions, and
takes no pointer events. It does not accuse, does not block, and does not
interrupt: the interviewer keeps talking and the candidate can ignore it. It
exists so the record is not a silent trap, and because somebody who knows it is
noticed is less likely to do it again, which is worth more than catching them.

**What the reviewer sees**

One plain line under the three health banners:

> The interview page was out of view 3 times, 47 seconds in total. The candidate
> was told this is recorded. It shows only what happened on this device, so it
> says nothing about a second screen, a phone, or anyone else in the room.

Muted, the same weight as the generated-at line, with no colour that implies a
verdict and no ranking. **Silent when the page was never left**, rather than
reading "0 times": an absence is not a result, and a row saying zero would turn
one into one.

**Files**

- `backend/app/db.py` — the `focus_events` column.
- `backend/app/interviews.py` — `FocusAbsence`, `FocusReport`, `POST
  /interviews/focus`, and the field on `InterviewView`.
- `frontend/candidate/index.html` — the consent line, the away state machine, the
  returning line and its styling.
- `frontend/admin/admin.js` — `focusLine`.
- `backend/tests/test_interviews.py` — seven tests, and the new public route.

**Confirm**

```
cd backend && uv run pytest -q          # 761 passed
```

---

## 2026-08-19 — Appinventiv is credited on every screen, and a job description starts from something

**What**

Three things: the company name at both ends of every page, the company logo as a
watermark on the call, and six job descriptions an employer can start from
instead of an empty box.

**The credit, and why it differs by surface**

`COMPANY_NAME` and `COMPANY_CREDIT` live in `shared/branding.py` beside
`BOT_NAME`, and reach the pages through the same token substitution the product
name already uses. They are deliberately separate constants. Mitra is what the
candidate talks to and is introduced by name in the greeting; Appinventiv is who
built it, is never spoken, and never reaches a prompt. There is a test asserting
the company name appears nowhere in the interviewer's system instruction.

The wording is the same everywhere: "by Appinventiv" at the top, "Appinventiv" at
the bottom. The treatment is not, because the surfaces are not.

- **Admin panel**, light. The header already held the product name and the words
  "Admin panel"; the credit is pushed to the far end of that same bar, in the
  same 13px muted grey as the label beside it, so it reads as chrome rather than
  as a second brand. At the foot, a centred 12px line above a hairline rule, with
  no background: a footer with a fill reads as a section of the product.
- **Candidate join page, device check and thank you**, light. A 12px muted line
  above and below the card, capped to the card's own width so it sits in the same
  column. These three wrappers changed from a centred row to a centred column to
  take them, which is the only layout change on the light screens.
- **Call screen**, dark. The top bar was two items at opposite ends, so a third
  between them centres itself without any of the three needing a width, which
  matters because the timer changes width every second. Muted to a grey slightly
  darker than the timer so the reading order is name, then time, then this. The
  foot of this screen is the watermark rather than a second line of text, so the
  same information is not given twice.

**The watermark, and the position is the whole decision**

The logo is reachable at `/assets/appinventiv-logo.png`, confirmed by request
rather than assumed: the existing asset route serves it, 200, `image/png`.

Three places were ruled out before choosing. The camera panel is the candidate's
own face. The orb is how the interviewer signals it is speaking, and a second
mark in that half would compete with the one thing on screen that has to stay
unambiguous. The captions are what a candidate reads while answering and are an
accessibility feature first.

What is left is the chrome. The controls strip is centred buttons with genuine
empty space at both ends, so the mark sits where the page was already blank, over
no content at all, and at the opposite end of the screen from the orb. Below
900px the controls fill the width they are given, so it drops onto its own line
underneath rather than overlapping a button.

**The artwork is transparent, and that was not the problem.** 91 percent of the
file is fully transparent, the corners carry no matte, and it needs no white box
removing. The problem is that it is near black ink, which on a `#0f1117` screen
is invisible, so the page paints it white with `brightness(0) invert(1)`. That
costs the blue in the mark: it is a two colour logo rendered in one. A supplied
light-on-dark cut is the only thing that would bring the blue back, and is worth
asking for.

Sizing is against the ink rather than the box. The file is 1200x630 with the
wordmark occupying only the middle 262px, so roughly seven tenths of the element
height is padding baked into the artwork. At 40px tall the wordmark reads at
about 17px, which is the size of the text beside it.

**Job description templates**

Six roles, one Markdown file each in `backend/jd_templates/`, served by a guarded
`GET /jd-templates` that returns all six with their text in one response. About
11KB, which is cheaper than a spinner over a box somebody is about to type into.
Business Analyst, Project Manager, QA Engineer, Mobile Engineer (React Native),
Marketing Manager, Social Media Content Specialist. The three that were asked for,
plus the three roles a mobile services company refills constantly: someone to run
the engagement, someone to test what it ships, and someone to build it.

Each is around 320 words in the shape of the JDs already in the fixtures: about
the role, what you will do, what we are looking for, nice to have. That is enough
for the clarification chat to have something to ask about, which is the only bar
that matters here.

**Text only, and the boundary is the design.** A template fills the box. It does
not decide what the interview tests: the clarification chat runs exactly as it
did, the employer answers the same questions, and the `EvaluationSpec` with its
competencies, weights and dealbreakers is produced the same way. No spec is
pre-built and no step is skipped. There is a test asserting the templates contain
none of the words "competency", "weight" or "dealbreaker", so a future edit cannot
quietly turn one into a spec.

**Chosen into an editable textarea, never attached as a file.** Picking a role
loads the text and focuses it, and it is ordinary editable text from that moment.
A template that could not be changed before the chat would be worse than none,
because it would feed the model words the employer never meant and every
competency after that would be derived from them. Writing from scratch still
works, uploading a real file still works, and a file wins if somebody does both.
Choosing a template over text already typed asks first.

The title field is filled from the template only when it is empty, so a template
never overwrites what the employer called the opening.

**What is not verified**

There is no JavaScript harness in this repo, so everything in `admin.js` ships
unverified: the template buttons, the replace confirmation, the title autofill,
and the `File` the typed text is sent as. The route behind it is tested, the
templates are tested, and the copy is covered by `test_copy_style.py`. All of the
CSS is unverified by definition, which is why the visual side needs a look.

**Files**

- `backend/shared/branding.py` — `COMPANY_NAME`, `COMPANY_CREDIT`,
  `COMPANY_LOGO_URL`, and the note on what the artwork is.
- `backend/app/main.py` — three tokens, and the guarded `/jd-templates` route.
- `backend/app/jd_templates.py` — the loader, with the title read from each
  file's own heading so the label and the text cannot disagree.
- `backend/jd_templates/*.md` — six roles.
- `frontend/admin/index.html`, `frontend/admin/admin.js` — header credit, footer,
  template picker and its styling.
- `frontend/candidate/index.html` — brand lines on three light screens, credit in
  the call bar, watermark in the controls strip.
- Tests in `test_api.py` and `test_interviews.py`.

**Left undone**

**A light-on-dark cut of the logo**, which would restore the blue on the call
screen. The CSS filter is the honest substitute and is documented as such.

**An employer cannot save their own template.** A file per role means adding one
is a commit and a review, which is right for copy shown to a paying customer, and
it means no migration and no drift between environments. Saving custom templates
is a database feature nobody has asked for.

**Confirm**

```
cd backend && uv run pytest -q                  # 754 passed
```

---

## 2026-08-18 — The term counter could not see the term block's own probe

**What**

From the live interview `int_7e31aaff4ab2`:

```
[511s] You said session management. Walk me through how that actually works.
[542s] You said session management. Walk me through how that actually works.
```

Word for word, 31 seconds apart, with a real answer in between. The candidate
began their second reply "So as I said".

A second probe about a term is supposed to escalate. The wording for that exists
and already differs — "You have said X twice now. What does it actually do?" —
and it fired correctly on `KL divergence` in this same interview. It did not fire
here, and the reason turned out to be one line.

**Why the escalation did not fire**

`asked_about` rejected anything without a question mark. That test was added to
stop the spoken acknowledgement ("Okay. So you used session management.") from
burning an attempt, and its comment reasoned that `VOICE_RULES` guarantees a
question mark in every real reply.

**The term block itself breaks that guarantee.** Its first rung supplies "You
said X. Walk me through how that actually works." — an imperative, ending in a
full stop, because that is how somebody says it out loud. So the brain's own
supplied probe failed the brain's own counter. Measured across ten runs before
the fix: 62 first-rung probes, 60 of them with no question mark anywhere.

With the count stuck at zero, `times_raised` stayed at zero, and
`_render_unexplained_term` rendered the first rung again. The second probe was
the first probe.

**`KL divergence` reached the escalation by accident**, which is what made the
two look like different mechanisms in the same interview:

```
[292s] You said KL divergence. Walk me through how that actually works.   not counted
[326s] How do you actually calculate that distance mathematically in KL     counted
       divergence?
[358s] You have said KL divergence twice now. What does it actually do?     counted
```

The rung-one probe was invisible there too. What counted was an intervening
question the model composed itself, which happened to name the term and happened
to carry a question mark. Same mechanism, different luck.

**The whole test suite wrote around it.** Every existing test phrases the probe
"Walk me through how that works**?**" with a question mark production never
emits. That is why nine attempts at this subsystem never found it: the fixture
disagreed with the prompt, and the fixture is what was being read.

**What changed**

`_asks_for_something` in `bot/brain/terms.py`. A question mark still counts; so
does a sentence opening with an imperative that requests an explanation — walk me
through, talk me through, tell me, explain, describe, help me understand.
Sentence-initial only, so "he asked me to explain the retry logic" is still a
report rather than a request, and the acknowledgement is still not an ask.

**The counter was fixed rather than the rung, and that ordering matters.** The
alternative was to render the escalated wording on the second *offer* instead of
the second ask. That reintroduces the exact falsehood this module exists to
prevent — telling somebody "you have said X twice now" when they were asked once,
which happened 29 times across six runs and is written up two entries above. The
count has to stay the authority for the sentence to be true. Fixing the count
fixes the rung; loosening the rung would not have fixed the count.

**Measured**

Ten competent runs a side, `gpt-4.1-mini`, 22 seconds a turn.

| | before | after | instrument |
| --- | --- | --- | --- |
| verbatim repeated questions | **8**  [1 0 0 1 0 1 0 0 2 3] | **0**  [0 0 0 0 0 0 0 0 0 0] | `feedback.health._verbatim`, production |
| probes actually asked | 86  [10 7 9 5 9 11 8 8 7 12] | 69  [8 7 7 6 6 6 8 8 6 7] | the block's four supplied sentences, scratchpad |
| — first rung / escalated | 67 / 19 | 43 / 26 | as above |
| probes counted | 53  [4 5 4 5 8 6 7 3 5 6] | 76  [8 8 6 8 8 8 8 6 8 8] | `Section.term_probes_spent`, production state |
| sections over the allowance | 0 | 0 | as above |
| mechanism questions | 70/382 = 18% | 47/378 = **12%** | `compare_models._probe_shape`, 19 cues |
| let-gos spoken | 21  [2 3 2 2 2 3 3 2 1 1] | 30  [3 2 3 2 3 3 3 4 3 4] | the let-go block's phrasing, scratchpad |
| `questioning_health` degraded | 5 of 10 | 0 of 10 | `assess_questioning`, production |
| unsupported attributions | 7  [2 2 0 0 0 0 0 0 0 3] | 4  [0 0 0 1 1 1 0 1 0 0] | "you said X" against prior candidate words, scratchpad |
| distinct subjects per section | 5.05 | 5.11 | 0.4 word-overlap grouping, scratchpad proxy |

**Verbatim repeats went to zero and stayed there on every run.** Six of the
eight before were first-rung term probes, which is what this fixes. The other two
were an echo turn and a seed question repeated in one section; neither is a term
probe and neither recurred, but nothing here would have stopped them.

**The probe count moved, and it was told not to. Read what moved.** Probes
*counted* rose 53 to 76 because the counter can now see them. Probes *asked* fell
86 to 69, because `MAX_TERM_PROBES_PER_INTERVIEW` is 8 and it now actually binds:
after the fix nine of ten runs spend the full 8, where before they recorded 3 to
8 while asking 5 to 12. The budget was being under-charged by roughly a third,
so probing ran past its allowance without any counter showing it.

**Mechanism questions fell from 18% to 12%, and that is on the must-not-regress
list.** It is downstream of the same thing: 17 fewer probes are asked, so fewer
mechanism questions are asked. It is still well clear of the 5% this feature was
built against, but it moved in the wrong direction and it should not be read as
noise — the per-run counts went from 4-11 to 2-8.

**Deflection still holds**, checked separately because the competent candidate
does not exercise it. Three deflecting runs on the fixed code: 4 terms reach the
report in each, 4 let-gos spoken in each, no verbatim repeats, and 12 of 24 term
renders were the escalated rung — against 21 of 155 on the competent arm before
the fix. A candidate who dodges now reliably gets the second question rather than
the first one twice, which is what the escalation was written for.

**The remedy is a cap, and the cap was not in scope.** `MAX_TERM_PROBES_PER_INTERVIEW`
was set to 8 against a counter that was undercounting by about a third, so 8 buys
materially fewer real probes now than it did when the number was chosen. Restoring
the previous volume means raising it. That is a change to a cap's value and was
explicitly out of scope for this change, so it is flagged and not done.

**Files**

- `backend/bot/brain/terms.py` — `_ASK_IMPERATIVES`, `_asks_for_something`, and the
  guard in `asked_about` that used to test for a question mark alone.
- `backend/tests/test_brain.py` — four tests, written with the wording production
  actually emits.

**Left undone**

**The first rung still contradicts `VOICE_RULES`**, which tells the model its
whole reply contains exactly one question mark while the block hands it a sentence
with none. The model follows the block, which is why 60 of 62 probes had no
question mark. Harmless now that the counter reads both forms, and not changed
here because altering the supplied wording changes what the interviewer says and
would confound the next measurement of this subsystem.

**Nothing was done about the second probe's content beyond the rung that already
exists.** Naming what the first answer left out would need the judge to say which
of "restated it" or "explained part of it" happened, and the judge was out of
scope.

**Confirm**

```
cd backend && uv run pytest -q
```

---

## 2026-08-18 — Asking to skip a question was read as asking to leave, and a word-for-word repeat read as healthy

**What**

Two bugs from the live interview `int_7e31aaff4ab2`, both seen with a person
talking.

A candidate said "right now, I don't know the answer of this question. Can we
skip it?" and was asked "Okay. Would you like to end the interview here or
continue?". They said they would like to continue and the interview carried on,
so nothing was lost, but somebody who wanted a different question was asked
whether they wanted to leave.

Separately, the interviewer asked "You said session management. Walk me through
how that actually works." at 511 seconds, got a real answer, and asked the same
sentence again at 542 seconds. The candidate began their reply "So as I said".
The report on that interview said the questioning was healthy.

**Why the withdrawal check fired, and it was not the withdrawal check**

Nothing in this repo matched that sentence. `classify` returns NONE: there is no
end verb in it, "skip" is not one, and the soft phrases have to be the whole
utterance. `looks_like_refusal` holds "skip it" but is whole-utterance too.
`classify_repair` returns NONE. All four were checked against the exact stored
text before anything was changed.

What fired was the interviewer model's own `end_interview` tool. The corroboration
gate then did its job — the patterns disagreed, so `act_now` was False and the
interview did not end — and what the candidate heard was the one offer that the
two-tier design permits, `OFFER_TO_STOP`, word for word.

So the bug is that the design has two outcomes where it needs three. End, and ask
whether to end. There was nowhere for "they want a different question" to go.

**A skip now goes where declining a question already goes.** `asked_to_skip_question`
in `bot/brain/withdrawal.py` recognises a request to leave this question, and it
is used in two places: `bot/tools.py` declines to offer to stop, and `observe`
counts a declined turn. The refusal block already says acknowledge briefly, ask a
different and easier question on the same topic, and never re-ask the declined
one, which is exactly what a skip should get. No new machinery.

**Everything in it fails towards still hearing a withdrawal**, which is the thing
it must not trade away. Four guards, any one of which is enough to refuse to call
something a skip: naming the interview, call, session, conversation or meeting;
any end verb anywhere in the utterance; any soft stop phrase anywhere in it,
searched rather than matched whole, which is stricter here than `classify` is;
and any intent `classify` itself can see. So "can we skip the rest of this
interview", "skip this and let's stop" and "I'm done, next question" all return
False and take the ordinary stop path. And the veto is only ever consulted when
the patterns did **not** corroborate, so the explicit path is untouched: a
candidate who plainly asks to stop still stops at once. Being wrong here costs
one unwanted question; being wrong the other way keeps somebody in a room they
asked to leave.

Every pattern also requires a frame that addresses the interviewer — a request, a
preference, a suggestion, or a bare imperative at the start of the sentence — so
"we had to skip that release because of budget" is an answer. That is the same
false positive the tool description already warns the model about with "we had to
end that project early", in the skip vocabulary.

**Why `degraded` did not fire, and it is the design rather than the threshold**

`repeated_questions` counted 2 against a floor of `max(3, 10% of 34)` = 3. One
short. Lowering the floor is the wrong fix and would break the signal: the other
repeat in that interview is a 0.70-overlap re-ask after a let-go, a legitimately
tightened version of an earlier question, and a signal that fires on those fires
on every competent interview.

The rate test measures a *similarity*, and a threshold is exactly right for that.
A word-for-word repeat is a different claim. It is not a rephrase, not a
narrowing and not a follow-up, because there is nothing left for it to be — the
candidate has already answered these exact words. So `verbatim_repeats` is
counted separately and **one is enough on its own**, with no rate applied. It
stays rare by construction rather than by threshold.

**Nothing at interview time saw the second ask, and the counter cannot.**
`assess_questioning` runs after the fact, from the stored transcript, and reports.
The counter saw both turns and correctly scored one repeat event; the first ask is
the original. What allowed the second is `MAX_TERM_ATTEMPTS = 2`, which lets a
term be probed twice on purpose — the comment on it describes the second as an
escalation before the let-go. Nothing enforces that the escalation is *worded*
differently, and the model was handed the same term with the same framing and
produced the same sentence. That cap is deliberately not touched here.

**Measured**

`questioning_health` recomputed across all 18 stored runs, before and after:

| | degraded |
| --- | --- |
| before | 0 of 18 |
| after | 1 of 18 |

**The honest sample is 1, not 18.** 17 of the 18 stored runs have no `section_id`
on any interviewer turn, so `assess_questioning` skips every turn in them and
`interviewer_turns` is 0. The observer seam that carries the section onto a turn
is recent. A "before" of zero is not evidence the signal is quiet; on 17 of these
runs it is blind, and that is worth knowing separately from this change.

So the same exact-match test was also run interview-wide, ignoring section scope,
which is strictly wider than what shipped:

| | runs with an identical interviewer question |
| --- | --- |
| interview-wide, all 18 runs | 2 |

`int_c9e7d27bd702` said "Okay, so you used session management, concurrency." three
times, at 84s, 120s and 174s. It is the same defect and the same term. Both
firings are true positives, and the section-scoped test that shipped can only fire
on a subset of these two. It does not fire on every interview.

**The tool description moved a little, and did not reproduce the live failure.**
`scripts/tool_call_check.py` gained the five skip phrasings, and the description
was run with and without the new block, `gpt-4.1-mini`, temperature 0.2:

| | should end | skip requests wrongly ended | other false positives |
| --- | --- | --- | --- |
| without the skip block | 10/10 | 1 of 5 | 2 of 6 |
| with it | 10/10 | 0 of 5 | 2 of 6 |

The one it fixed was "Can we move on?". **It did not fix the live sentence,
because the harness never broke on it**: one turn of context is not eleven minutes
of context, and the model left "right now, I don't know the answer of this
question. Can we skip it?" alone in both arms. The prose is therefore not what
this fix rests on. The veto in `handle_end_interview` is, and it fires on what the
candidate said regardless of why the model reached for the tool. Same lesson as
everywhere else in this file: structural beats prose.

**Nothing regressed.** Recall on the ten phrasings a pattern list will never
contain stayed at 10/10 in both arms. The two pre-existing false positives are
unchanged and untouched.

**Files**

- `backend/bot/brain/withdrawal.py` — `asked_to_skip_question`, its patterns and
  its four guards.
- `backend/bot/tools.py` — the third outcome in `handle_end_interview`; the skip
  case added to the tool description.
- `backend/bot/brain/brain.py` — a skip counts as a declined turn in `observe`.
- `backend/feedback/health.py` — `_verbatim`, and `verbatim_repeats` counted per
  section.
- `backend/shared/contracts.py` — `QuestioningHealth.verbatim_repeats`, the second
  test in `degraded`, and the sentence saying so.
- `backend/scripts/tool_call_check.py` — five skip phrasings in `SHOULD_NOT_END`.
- Tests in `test_withdrawal.py`, `test_tools.py`, `test_brain.py`, `test_health.py`.

**Left undone**

**The identical second probe is not prevented, only reported.** Preventing it
means either lowering `MAX_TERM_ATTEMPTS` or making the second probe read as an
escalation rather than a repeat, and both are changes to the term caps and the
ramp. Worth doing, and not done here.

**`narrow_sections` still needs three asks of the same thing in one topic** to
list a section, because `here > 1` counts repeat events rather than questions.
That is consistent with its own docstring, it is why the field was empty on this
interview, and it is left alone: widening it would make the signal fire more often
for no new information, and `degraded` no longer depends on it.

**No scripted candidate was added.** The stored transcript covers the trigger and
is quoted directly in the tests; the model-facing question is whether the tool is
called, which `scripts/tool_call_check.py` already exists to answer and now does.

**Confirm**

```
cd backend && uv run pytest -q                       # 741 passed
PYTHONPATH=. uv run python scripts/tool_call_check.py # 10/10, skips all ok
```

---

## 2026-08-14 — The interviewer is given the dealbreakers it can ask about, and ignores them

**What**

`EvaluationSpec.red_flags` was never passed to the brain. The interviewer had
never seen a dealbreaker, and the scorer checked against a list the interview was
never given. The seeds could not cover the gap because they are generated from
`target_depth`, which states a different bar: one competency's target depth reads
"including handling of ambiguous or negative results" while its dealbreaker reads
"avoids or deflects when asked about negative results".

An askable dealbreaker now renders beside `target_depth` in the section that owns
it. **It did not work, and the entry is kept because the reason is worth more
than the change.**

**Askable against observable, and the spec does not carry the distinction.**
`red_flags` is a list of strings and the clarification chat never asks the
employer which kind they are writing, so it is inferred: a dealbreaker that OPENS
with an inability ("cannot", "can't", "unable to") names something a question can
establish; anything else describes a manner spread across answers. A prefix test
on a closed employer-authored list, no threshold, readable by eye against all 14
dealbreakers in the three fixtures. It calls 3 askable and 11 observable and all
3 are genuinely questions.

It keys on phrasing rather than meaning, so "struggles to explain why metrics
were chosen" gets nothing. It misses compounds, where an askable half sits behind
an observable opening clause. And it misses the hybrid: "avoids or deflects when
asked about negative results" is a manner, but the manner cannot be seen without
first asking. Every one of those errors renders nothing, which is the behaviour
that already existed. Over-rendering is the expensive mistake, because it
announces the trap to the candidate.

**Ownership** is matched on five-character stems against the competency's name,
description and target depth. Whole words put "cannot describe a time they
deployed or debugged" on no competency at all, against one named "ML Service
Deployment and Debugging". A tie or a best score under two means unowned, and an
unowned dealbreaker renders nowhere rather than in all six topics. Two of the
fourteen render, both in `ml_senior`.

**Measured**

Ten competent runs and three deflecting, `gpt-4.1-mini`, at 22 seconds, all
thirteen scored. The comparison arm carries the uncommitted `_advance_reason`
reorder, so section-level numbers are against that and not a clean baseline.

**The bar was not met. `not_reached` rose.**

| dealbreaker | cleared | observed | not reached |
| --- | --- | --- | --- |
| cannot explain why a metric was chosen (**rendered**) | 83% -> 90% | 0% -> 0% | 16% -> 10% |
| vague or generic terms | 16% -> 10% | 33% -> 10% | 50% -> 80% |
| cannot describe a time they deployed or debugged (**rendered**) | 16% -> 20% | 0% -> 0% | 83% -> 80% |
| avoids or deflects on negative results | 83% -> 90% | 0% -> 0% | 16% -> 10% |
| **all four** | | | **42% -> 45%** |

The two rendered dealbreakers moved by three points and six points, on ten runs
against six. The one that got worse is an observable one this change does not
render. The only dealbreaker with room to move was "a time they deployed or
debugged", at 83 percent not reached, and it went to 80.

**The interviewer's behaviour in the owning section did not change**, which is
the measurement that settles it. In `ml_service_deployment`, over 60 questions in
each arm:

| | asking for a time or a specific occasion | mentioning debug, went wrong, root cause |
| --- | --- | --- |
| before the seed rotation | 0.1 a run | 0.1 a run |
| reorder only | 0.0 a run | 0.9 a run |
| with this block | 0.2 a run | 0.5 a run |

**Why it is ignored, and it was foreseeable from this file.** The block was
written to be neither mandatory nor advisory: a mandatory block naming a specific
item wins the turn, measured at 80 to 100 percent, and it would have won it from
the seed question; an advisory one is a preference between available options,
measured at 0. So it was fused into `target_depth` as a standard that never says
what to ask this turn.

That third category does not exist. A block that does not compete for the turn
does not change the turn. Worse, it is the exact shape recorded forty lines above
it as measured at no effect: the unexplained-term block this repo already
replaced said "coverage is insufficient, do not move on", which describes a state
and leaves the model to work out what to do about it. This block describes a
standard and leaves the model to work out what to do about it.

**Escalating it is the wrong response** and is not being done. Making it
mandatory would take the turn from the seed question, which is the one thing
currently putting the section's own material to the candidate, and would trade a
known gap for a new one. The right place for this is the blueprint generator,
where a dealbreaker that needs asking becomes a seed question and inherits the
rotation that already puts seeds in front of the model one per turn. That is a
different change, out of scope here, and it is the one worth making.

**Nothing regressed.**

| | reorder only | + this block |
| --- | --- | --- |
| **unsupported attributions** | 0.30  [0..1] | **0.10**  [0..1] |
| distinct authored answers reached, of 31 | 10.7  [7..13] | 11.1  [9..14] |
| answers already given in that section | 53%  [44-65] | 56%  [45-61] |
| distinct subjects per section | 2.9  [1..6] | 2.7  [1..5] |
| mechanism questions per opportunity | 18% | 16% |
| sections on one subject | 4/60 | 5/60 |
| deflecting: fires what is true of it | 3/3 | 3/3 |

Attributions fell rather than rose, against a bar of 0.40. The seed rotation
doubled them because the seeds are written from the CV; **dealbreakers are
written in the clarification chat from the JD and the CV never touches them**, so
the coupling that damaged the seeds is absent here. Mechanism fell two points
inside a per-run spread of 5 to 22.

**A bound that was already broken.** `ml_senior`'s worst-case system instruction
was **2445 tokens against a bound of 2400 before this change**, and 2490 after.
`test_section_prompt_stays_small_and_bounded` does not catch it because it runs
the default fixture, which has shorter target depths and renders no dealbreaker.
The bound is not raised here. If it has to come down, the candidate is
`_render_red_flags`, roughly 90 tokens, which lists every dealbreaker including
the observable ones passively and now overlaps this block on the askable ones.

**Files**

- `backend/bot/brain/prompting.py` — `_askable`, `_stems`, `_dealbreaker_for`,
  `_render_dealbreaker`, one call site.

**Left undone**

Everything the diagnosis points at: dealbreakers as generated seed questions, the
token bound, and the guard test running the wrong fixture.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 695 passed, 22 skipped
```

## 2026-08-14 — A section's open terms belong to that section, and the budget was never the scarce thing

`_open_terms` is assigned only in `apply_judgment`, and only for the section
being judged, so at a new section's first turn it still held the previous
section's list. That is the carry that let one term take a fresh allowance in
section after section, and it is why an earlier attempt to change which term
gets picked was reverted as inert: the selection rule could not fire at the
boundary because the list it sorted was a section out of date.

It is cleared when the section advances. No new state; a list is emptied.

**Measured before choosing, and the evidence was one-sided.** Of the 13 probes
that fell on a section's first turn in the previous arm, **13 named a term the
candidate had not said in that section**. `ml_service_deployment` opened with
"You said prompt-level rules. Walk me through how that actually works", carried
from `applied_llm_depth`. Not one was a legitimate "you said this here".

**What has the turn now** is the section's own broadest seed question, under the
ramp's "START THIS TOPIC BROADLY, ask the widest version of it". The term block
firing at turn zero was fighting that instruction directly.

**Cross-section probing is delayed, not lost.** The judge re-reads each section
and re-nominates from its transcript, so a term the candidate uses again here
comes back with a judgement behind it. What goes is asking about a word they have
not said in this topic, which is what made "you said X" false. The authored
cross-section material was never in this list anyway: it is in the seeds, which
say things like "how did you communicate the limitations of the language ID
prototype to stakeholders".

**Measured**

Ten runs, competent, `gpt-4.1-mini`, at 22 seconds, against the F2 arm.

Instruments, stated so the two arms are known to match: probe-shaped is a
two-sentence detector; counted is the brain's own log line; mechanism is
`probe_mechanism`, nineteen cues plus an echo; subjects, answers and attributions
are `measure.py`; repeats are exact string equality, within a section and across
two different sections. Every figure below is the same instrument on both arms.
**Probe-shaped and mechanism are not comparable to each other** and are not read
against each other here.

| | F2 | this |
| --- | --- | --- |
| **probes on a section's first turn** | 13 | **0** |
| **cross-boundary repeats** | 8 | **2** |
| probe-shaped questions | 73  [4..10] | 70  [5..9] |
| counted by the brain | 76  [0..8] | **65**  [0..8] |
| probes per interview | [0,6,7,7,8,8,8,8,8,8] | [0,2,4,5,7,7,8,8,8,8] |
| runs reaching the budget of 8 | 7 | **5** |
| sections exceeding the allowance | 6/60 | 4/60 |
| let-gos spoken | 15 | **19** |
| mechanism questions per opportunity | 18%  [10..27] | **22%**  [15..35] |
| distinct subjects per section | 2.9  [1..5] | 2.9  [1..6] |
| sections on one subject | 4/60 | 3/60 |
| distinct authored answers reached, of 31 | 10.9  [7..14] | **11.8**  [9..14] |
| answers already given in that section | 51% | 53% |
| within-section repeats | 5 | 5 |
| unsupported attributions | 0.30 | 0.20 |

**Predictions against actuals, and three of six were wrong**

| predicted | actual | |
| --- | --- | --- |
| first-turn probes 13 to near zero | **0** | right |
| cross-boundary repeats fall | **8 to 2** | right |
| exceedance unchanged at 6/60 | 4/60 | roughly right |
| counted should not move | **76 to 65** | **wrong** |
| subjects per section rise | 2.9 to 2.9 | **wrong** |
| mechanism roughly flat | **18% to 22%** | **wrong, favourably** |

**Where the model was wrong, and it is the useful part.**

The prediction that counted would hold rested on a claim made explicitly: the
budget of 8 binds in most runs, so allowance freed at turn zero gets spent later
in the same section. It was not spent. Counted fell by 11 against 13 probes
removed, and runs reaching the budget went from 7 to 5.

**So the binding constraint was never the budget. It is the supply of nominated
terms, and the carried list was inflating that supply.** Every previous
measurement that showed runs hitting 8 was reading a number propped up by terms
borrowed from earlier sections. With the borrowing gone, five runs of ten never
reach the budget at all and the spread runs from 2 to 8 rather than 6 to 8. Three
changes have now been reasoned about as though the budget were the scarce
resource; it was not, and the audit's own account of the caps inherits that error.

Mechanism rising was not predicted by anyone and has a clean explanation. A
first-turn probe cannot count as a mechanism question, because `_probe_shape`
skips a turn with no preceding answer in the section, and at turn zero there is
none. So thirteen probes moved out of a position where they could never register
and the section instead opens on its seed, gets a substantive answer, and the
follow-ups that echo it are exactly what the signal counts. Fewer sanctioned
probes, more mechanism questions, better placed.

Subjects per section not moving says the carried term was not what suppressed
subject diversity. That remains unexplained and is not this change.

**Held.** Mechanism rose rather than fell, well clear of the 3 to 5 percent
probing-off floor. The let-go is still spoken and more often, 19 against 15.
Sections over the allowance improved. Deflection on the F2 arm, completed over
all three runs this time rather than two: 24 probes, 6 let-gos, and 4, 1 and 4
terms reaching the report against 4, 4 and 2 in an older arm.

**Files**

- `backend/bot/brain/brain.py` — `_open_terms` and `_offered_term` cleared on
  advance.
- `backend/tests/test_brain.py` — the boundary test had pinned the carry as
  desired behaviour and now pins its absence. Its original reason, a section's
  first ask claiming it had asked before, is unchanged and cannot occur here at
  all now.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 701 passed, 22 skipped
```

## 2026-08-14 — The depth ramp stops nominating terms, and the accounting finally closes

The ramp's first two stages said "that term IS your question" and supplied the
term block's own sentence. That made it a fourth mandatory block, outside the
arbitration the other three share, doing the term block's job with none of its
accounting: no raise count, no allowance, no budget, no let-go.

**The shape was chosen by reading the run set, not the code.** Of 85 probe-shaped
questions in the previous arm, 33 fell at turns one and two where the ramp
nominated, and **22 of those named a term the judge already had open**, so the
term block would have asked them anyway. The ramp's unique contribution was the
remaining **11**: probes about terms the judge never flagged.

That ruled out the alternative, which was to let the ramp keep nominating and
spend the same allowance. It would have sanctioned exactly those 11. The judge is
the authority on what went unexplained; a ramp nomination is a second opinion
with no evidence behind it, and giving it budget spends a candidate's patience on
terms nobody thought needed explaining.

**The stage-three prohibition was half wrong and that half is gone.** It said "Do
not unpack another, and do not ask again about anything you have already asked
about here", and the audit measured it violated on a quarter of the turns it
governs. Most of that was not disobedience: **27 of the 40 probes in that zone
name a term the judge had open**, which is the term block firing, and the term
block is mandatory and rendered after the ramp. The prohibition was contradicting
a block that outranks it. The anti-repetition half is true and stays.

**Measured**

Ten runs, competent, `gpt-4.1-mini`, at 22 seconds, corrected signals. The
baseline is the F1 arm, which ran the **ungated** counter; the gate landed after
it and was never measured on its own, so a few of F1's let-gos are ones the gate
would have prevented.

| | F1 | this |
| --- | --- | --- |
| probe-shaped questions | 85  [6..12] | **73**  [4..10] |
| counted by the brain | 74  [0..12] | 76  [0..8] |
| the gap | +11 | **-3** |
| ...at turn 0 | 12 | 13 |
| ...at turns 1-2, where the ramp nominated | 33 | **24** |
| ...at turns 3+, where the term block fires | 40 | 36 |
| **sections exceeding the allowance in behaviour** | 15/60 | **6/60** |
| let-gos spoken | 16 | 15 |
| mechanism questions per opportunity | 24%  [9..43] | **18%**  [10..27] |
| probes in the stage-3 zone | 20% | 19% |
| distinct subjects per section | 2.8  [1..6] | 2.9  [1..5] |
| sections spending a topic on one subject | 7/60 | **4/60** |
| answers already given in that section | 55% | **51%** |
| distinct authored answers reached, of 31 | 11.3  [9..15] | 10.9  [7..14] |
| unsupported attributions | 0.00 | 0.30  [0..2] |

**The gap closed and went slightly negative.** 76 counted against 73
probe-shaped, because `asked_about` recognises a named probe in any phrasing
while the probe-shaped count matches two exact sentences. Counting more than the
narrow detector sees is the healthy direction.

**Predictions, against what happened**

Three right, one wrong, and the wrong one is the useful one.

| predicted | actual |
| --- | --- |
| probe-shaped 85 to around 74 | **73** |
| turns 1-2 fall hardest, turns 3+ barely move | **1-2 fell 27%, 3+ fell 10%, turn 0 flat** |
| sections over the allowance to single digits | **6 of 60** |
| mechanism 24% to the low 20s | **18%** |

**The miss, and what it shows.** Mechanism fell six points where two or three
were predicted. The reason is an instrument mismatch of exactly the kind fixed
this morning: the probe-shaped count matches **two** exact sentences, while
`probe_mechanism` runs on **nineteen** cues. Counting both arms with the same
nineteen gives 104 falling to 85, a drop of 19, which matches the mechanism drop
of 18 almost exactly.

So the ramp was producing about nineteen mechanism questions and the narrow
detector only ever saw twelve of them, because the ramp said "say something close
to this" and the model paraphrased: "How exactly did you check whether the
four-bit weights changed the routing decisions", "Explain how the Mistral-format
jinja template fits into this LoRA training". Those are mechanism questions the
probe-shaped count is blind to.

The prediction was made with a narrow instrument and the risk assessed with a
wide one, and the two were compared as though commensurable. That is the same
error as the duplicated cue set, one level up: not a wrong number, a comparison
between two things that do not measure the same thing.

**Mechanism did not fall to the floor.** 18% against a probing-off baseline of 3
to 5 percent, and the per-run spread narrowed from 9-to-43 to 10-to-27. Fewer
mechanism questions, more consistently placed, all of them now accounted for.

**Held.** The let-go still spoken, 15 against 16. Sections over the allowance
improved rather than regressed. The ramp still does its own job: stages two and
three still demand a number, a trade-off, a failure or a decision, and the
stage-3 zone rate is unchanged at 19%, which is the term block and not the ramp.
Deflecting was spot-checked over two runs: 24 probes and 6 let-gos, in line with
the older arms.

Attributions rose from 0.00 to 0.30, two instances in ten runs. Small, and the
previous arm's 0.00 was itself unusually low.

**Files**

- `backend/bot/brain/prompting.py` — ramp stages one, two and three.

**Not in this change**

F3, `_open_terms` stale across a boundary. The prediction stands and is now
checkable: it should move repeats and subjects and leave the counted-probe and
exceedance figures alone. Turn 0 probes held at 12 and 13 across this change,
which is where F3 would show.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 701 passed, 22 skipped
```

## 2026-08-14 — The probe count sees probes about terms it did not offer, and that turns out not to be the problem

`_count_if_it_asked` keyed on `_offered_term`, so a probe counted only when the
model unpacked the one term the prompt named. Unpack a different open term and
the question happened uncounted, uncapped, with no let-go. Measured across ten
runs: 89 probe-shaped questions against 67 counted, and ten of sixty sections
holding more probes than the per-section allowance permits.

**How a probe is recognised now.** Two passes, naming before pointing. First,
does the question NAME any term the judge currently has open, the offered one
first. Then, only if nothing was named, does it POINT at the offered term with a
deictic cue.

No similarity rule. The comparison is still "does this question contain a
distinctive word of this exact term", with no threshold and no scoring. What
widened is the set of terms checked, from one to the list the judge already
returned. The deictic branch deliberately did not widen: "how does that actually
work" refers to whatever was put in front of the model, and charging it to some
other open term would be a guess in a mechanism designed to fail towards not
counting.

**Naming before pointing was a correction, found by its own test.** The first
version checked the offered term with both tests before looking at the others,
and real probes say a name and point in the same breath: "You said bf16. Walk me
through how that actually works." The deictic branch fired and charged the
offered term even when the model had unpacked something else.

**What it still cannot see**, from categorising all 89:

| | |
| --- | --- |
| names a term the judge nominated | 73 (82%) |
| names a term the candidate said, never sanctioned | 15 (17%) |
| names something not in the candidate's speech at all | 1 (1%) |

The last is the model paraphrasing a definition. Catching it needs comparing
text, which this repo has refused six times.

**Measured, and the result is that this cannot work**

Ten runs, competent, `gpt-4.1-mini`, at 22 seconds, against the current arm, with
the corrected `probe_mechanism`.

| | before | after |
| --- | --- | --- |
| probe-shaped questions | 89  [6..14] | 85  [6..12] |
| counted by the brain | 67  [0..8] | 74  [0..12] |
| the gap | +22 | +11 |
| **counted, clipped to the budget of 8** | **67** | **67** |
| runs exceeding the interview budget | 0 | **3** (9, 10, 12) |
| sections past an allowance of 2, in the log | 0 | **5** |
| sections exceeding the allowance in behaviour | 10/60 | 15/60 |
| mechanism questions per opportunity, corrected | 25%  [12..47] | 24%  [9..43] |
| distinct authored answers reached, of 31 | 9.8  [6..12] | 11.3  [9..15] |
| distinct subjects per section | 2.6  [1..6] | 2.8  [1..6] |
| let-gos spoken | 13 | 16 |
| unsupported attributions | 0.60  [0..4] | 0.00  [0..0] |

**Every probe the widened count found was one the budget had already refused to
pay for.** Clipped to its own ceiling the new count totals 67, which is exactly
what the old one totalled. Six of ten runs reach the budget of 8 in both arms,
and 23 to 25 probe-shaped questions per arm happen beyond anything the budget
could ever sanction.

So the gap is not a counting problem. A counter cannot close it, because by the
time those probes happen there is no budget left to spend and nothing is being
offered. Closing it requires *preventing* them, and they are produced by a
mechanism the budget does not gate: the depth ramp, which says "that term is your
question" at turns one and two with no accounting at all. That is F2 in
`AUDIT_FOLLOW_UP.md` and it is not this change.

**The widened count also broke the invariant it was meant to serve.**
`_term_probes_spent` is both "how many probes happened" and "how much budget is
left", and widening one without widening the other set them against each other:
three runs of ten spent 9, 10 and 12 against a budget of 8. So the search now
respects the same gates as the offer. A counter that breaks its own ceiling is
worse than one that undercounts, because everything downstream reads it as
budget.

**Kept, at a smaller claim.** With the gate, the totals return to 67 and the
change stops being about how many. What it still does is charge the RIGHT term:
a probe that names `bf16` while `Mistral jinja template` was offered now spends
`bf16`'s allowance, so the cap binds on the term actually being asked about and
the let-go is owed to the candidate for the right word. That was wrong before and
is right now, and it is worth having on its own.

**Mechanism questions did not fall**, 25 to 24 percent inside a per-run spread of
9 to 47, so the allowance does not need raising on this evidence. Answers reached
and subjects per section both moved up slightly. Attributions fell to zero, which
is almost certainly unrelated to this change.

**The gated version has not been re-run.** The arithmetic determines the totals,
67 counted and a gap of about +18, but the distribution of let-gos and which
terms hit their caps would need another ten runs to state.

**What F2 and F3 should contribute, predicted before they are done**

F3, `_open_terms` stale across a boundary: small, two to four probes. It changes
which term is offered at a section's first turn rather than whether a probe is
counted.

F2, the ramp outside the arbitration: most of the remaining 23. If the residual
gap after F2 sits at turns one and two, the ramp was the source. If it is spread
evenly across turn positions, it is the model copying its own previous turn out
of the verbatim window, and no accounting change will reach it.

**Files**

- `backend/bot/brain/terms.py` — `allow_deictic` on `asked_about`.
- `backend/bot/brain/brain.py` — `_count_if_it_asked` widened and gated.
- `backend/tests/test_brain.py` — two tests.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 701 passed, 22 skipped
```

## 2026-08-14 — The mechanism signal could not see a second ask, and never could

`_MECHANISM_CUES` was defined twice in `scripts/compare_models.py`, at line 266
and again at 391. Python resolves module globals at call time, so both names
pointed at whichever was defined last and two different signals ran against one
cue list.

The one that lost is the term block's escalation. Its sentence is "You have said
X twice now. What does it actually do?", and the surviving list contains "what
does that actually" but not "what does it actually do". So **every second ask,
the question the whole term machinery exists to produce, was invisible**:
nineteen of nineteen in the ten-run arm.

It has been this way since `28e8277`, the commit that introduced `_probe_shape`.
Both definitions arrived together, so **no mechanism figure ever recorded in this
changelog was measured with a working signal.**

Fixed by separating them, because they were never the same question. The narrow
set belongs to `_section_coverage`, which asks whether a communication topic went
technical, and is now `_MECHANICS_CUES`. The broad set belongs to `_probe_shape`
and gains the two cues it was missing, `"what does it actually do"` and
`"what does that do"`.

**Nothing else in that file has the same shape.** Every module-level name and
every tuple, set and dict literal in the measurement and brain modules was
scanned for redefinition and repeated members. Two hits, both deliberate:
`score.py` appends `PROSE_STYLE` to its own `SYSTEM`, the idiom used in three
modules, and the refusing candidate says "I don't want to." three times because
that is the scenario.

**Every mechanism figure, recomputed from the runs on disk**

No new interviews. The reimplementation reproduces the recorded numbers to within
two points, so the absolute values below differ slightly from what the harness
printed; the deltas are computed the same way on both sides and are sound.

| what it measured | recorded | corrected |
| --- | --- | --- |
| seed rotation, three arms | 24% / 22% / 13% | **33% / 28% / 21%** |
| the back half, turns 4+: seed shown / wrapped / absent | 19% / 21% / 6% | **27% / 27% / 8%** |
| the `_advance_reason` reorder | 15% to 18% | **21% to 22%** |
| the dealbreaker block | 18% to 16% | **22% to 23%** |
| S2 and S11 together | 16% to 20% | **23% to 25%** |
| mechanism questions inside the communication topic | 1 | **0** |

**Two conclusions change and one gets stronger.**

The dealbreaker block **reverses**. It was recorded as costing two points of
mechanism, 18 to 16, and cited as one reason to revert it. Corrected, mechanism
rose, 22 to 23. The revert recommendation stands on its other evidence, which
did not come from this signal: `not_reached` rose from 42 to 45 percent, and in
the topic that owns "cannot describe a time they deployed or debugged" the
questions asking for a time did not move. But the mechanism half of that argument
was wrong and is withdrawn.

The single mechanism question that appeared in a communication topic, reported in
the S2 and S11 entry as one leak where there had been none, **was an artefact of
the shadowing**. With the cue set that function was written against it is zero.
Nothing leaked.

The back-half collapse gets **stronger**, and it is the figure that mattered most,
because it is why the seed rotation wraps rather than stopping at the last seed.
Recorded as 21 percent falling to 6 when the seed is taken away; corrected, 27
falling to 8. Nineteen points rather than fifteen. The decision to keep the wrap
was right and is now better supported.

The reorder and the S2/S11 pair both keep their direction and lose about half
their size, 3 points becoming 1 and 4 becoming 2. Neither conclusion rested on
mechanism alone and neither changes.

**What else in that file cannot be trusted**

Verified by hand against transcripts rather than by reading, because three
signals were wrong last week in ways that looked right in source.

| signal | claims | actually | verdict |
| --- | --- | --- | --- |
| `probe_mechanism` | mechanism questions per opportunity | now agrees, after this fix | **trust** |
| `probe_menu` | took the term as given | `"how did you measure"` and `"what metric"` are also `evaluation_reasoning`'s own seed questions, so a topic asking its own material scores as menu | **direction only** |
| `probe_new_topic` | advancing the plan | introduced a seed word not in the last answer. Counted the first turn of a six-probe loop as advancing | **direction only** |
| `seed_overlap_rough` | seed questions asked | its own docstring records 45 found where a person found 55, three of them scoring the acknowledgement | **do not set a bar on it**, as it says |
| `probe_terms_specific` | probes spent on this person's work | has a digit or an internal capital. Scores `peft`, `confusion matrix` and `routing decision` as generic | **delete** |
| `comms_vs_mechanism` | a communication topic going technical | agrees, now it has its own cue set | **trust** |
| `questions_asked` | questions asked | question marks. Three turns in one run carry two, so it absorbs the one-question rule being broken instead of reporting it | **rename or split** |
| `field_words_used` | domain vocabulary used | vocabulary present at least once in the whole interview. Twenty uses score the same as one | **presence only** |
| `words_over_limit`, `words_median`, `interviewer_share` | turn length | exactly that | **trust** |
| `returned_to_the_question` | did a repair get the same question back | reported as a fraction with the judgement left to a reader, which its docstring says plainly | **trust as a pointer** |

`probe_terms_specific` is the one to delete rather than fix. Its docstring already
says the question "cannot be decided reliably in code", it has been rewritten once
after being inverted, and a proxy for product names is not a proxy for
specificity. A signal nobody can trust is worse than no signal, because it gets
cited.

**Files**

- `backend/scripts/compare_models.py`

**Left undone**

The accounting problem itself. F1, F2 and F3 in `AUDIT_FOLLOW_UP.md` are the real
findings, and fixing them before the signals were verified is how this subsystem
got nine changes deep.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 699 passed, 22 skipped
```

## 2026-08-14 — The interview screen becomes two halves: the interviewer, and you

The call screen was a small orb centred on a dark page with a 148px self-view
tucked in the corner. It is now split: the interviewer on the left, the
candidate's own camera on the right.

**Layout only.** Not one line of the call's behaviour changed. Every element the
script reaches for kept its id, and the diff contains no JavaScript: 37 selectors
checked against the markup, none missing.

**The left half.** The orb at `clamp(150px, 24vh, 280px)`, the speaking label
under it, the hint, and the captions below that.

**Making a graphic read as a participant rather than a spinner.** This is the
part that needed thinking about, because the interviewer has no camera and half a
screen of shape can easily read as a page still loading.

Nothing rotates and nothing travels. Rotation is the language of loading, and a
shape going round in circles at this size would say "still thinking" for forty
minutes. The orb breathes instead: a 6 second, 1.8 percent scale cycle that reads
as alive and idle, the way somebody sitting still is obviously still there.

Speaking is a different KIND of motion rather than more of the same. The breathe
stops, the border takes the live colour, a soft halo appears, and a second ring
expands out of the edge in time with the turn. The change is legible across a
room and cannot be confused with the idle state. The words carry it too: the
label already said "{{BOT_NAME}} is speaking" against "Listening", and it now
sits under something big enough to look at.

`prefers-reduced-motion` turns all of it off and leaves a static scale step,
because the motion is decoration and the words carry the same fact.

**The captions.** Given half a screen the constraint stops being width and
becomes reading comfort. Line length is capped at 62 characters rather than 640
pixels, because a character measure stays readable when the font grows and a
pixel one does not. Text goes 16px to 18px, line height 1.5 to 1.65. The panel
keeps a floor of 8.5em so a one line question does not make the layout jump, and
a ceiling of `min(42vh, 400px)`, roughly double what it was: the point of
captions here is re-reading a question WHILE answering it, and a candidate who
has to scroll mid-answer has lost the thing this exists to give them.

The timing is untouched, and it is the reason this page has captions at all: the
current turn only, cleared when the interviewer next STARTS speaking rather than
when it stops.

**The right half, and what it is not.** The camera is inset in its own frame with
space around it, not bled to the edges. That is a judgement about what it is for:
nothing watches this video and nothing scores it, and it exists so somebody can
see their camera is on and pointing at them. Full bleed would give it the weight
of a thing being examined. It carries no name badge and no pin control, which is
most of what makes a video tile look like a meeting.

The frame is always drawn, with a quiet line inside it, so a candidate who never
granted the camera sees a considered empty panel rather than half a screen of
nothing.

**Not a meeting app**, which a two panel layout could easily become. There is no
participant grid: a grid implies tiles of the same kind and the same standing,
and there is only one person here. The two halves are deliberately not matching
rectangles, one being a composition of a shape and words and the other a single
video. No toolbar was added; the three controls that existed stay where they
were. No branding on the stage.

**On a phone.** Two columns do not fit, and forcing them would take the screen
away from the captions, which is where they matter most, because a candidate on a
phone is the one most likely to have rough audio. Below 900px the split
collapses to one column, the orb gives up height, and the self-view returns to
being the small corner tile it has always been. The captions keep the full width
and a `min(38vh, 260px)` panel, still larger than the 150px they had.

The frame is hidden with `display: contents` rather than `display: none`, because
a fixed-position element inside a `display: none` parent is not rendered at all
and hiding the frame outright would take the self-view with it.

**Files**

- `frontend/candidate/index.html`

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 699 passed, 22 skipped
```

## 2026-08-14 — Five small things: a crash on "yes", a silent .env, lost section labels, a stale pill, and two unread fields

### The clarification chat crashed when the employer agreed

An employer replying "yes" to the summary got `Clarification model returned no
reply text (got None).` in a 502, raw exception text with nothing to click.

**Reproduced before fixing, and the cause is broader than the report.** Put at
the point where the summary is confirmed, the blueprint model returned an empty
`reply` with `done` true and a complete spec in **three of four** confirmations:
"yes prepare it", "Yes." and "looks right" all did it, and only "yes" happened to
produce prose. The wording is not what decides. The shape in the prompt shows
`"done": false` and never says what to say once the conversation is over, so the
model reasonably stops talking. The key is present and holds `""`.

`reply` was checked before `done` and `spec` were looked at, so a completing turn
with nothing to say threw away the spec that the whole conversation existed to
produce.

The check moved after the verdict. A completing turn with no reply now hands over
the spec and says so in a sentence written here rather than asked for, because at
that point the system is reporting its own state and a generated sentence could
claim something about what the employer said. A turn mid-conversation with no
reply is a dead turn rather than a dead end, and gets the same treatment a
rejected spec already gets: an ordinary assistant message, the conversation
continues, and the next attempt has it in its own history.

The two tests that pinned the old behaviour asserted that it raised. The
requirement was never that it raise, it was that the employer not see "None", and
they now assert that.

### A .env in the wrong folder failed silently

`bot/config.py` loads dotenv from a path derived from its own location, so a
`.env` in `backend/` is read by nothing. No warning, no log line, a 500 on first
login, and twenty minutes to find.

Three places are checked and only three: the repo root, because it is the one
that works and its absence is worth saying out loud; `backend/`, because it is
the working directory for `uv run` and every script here, so it is where a
shell-completed path lands; and `backend/bot/`, because it is next to this file
and is where somebody debugging this reaches next. Anywhere further is a guess,
and a warning that fires on guesses trains people to ignore warnings.

**Which file is loaded does not change.** Changing the path silently would break
whatever is currently working on somebody else's machine.

### Section labels were dropped when a transcript was saved

The persisted transcript is built from the observer's STT frames, and
`TranscriptObserver._append` wrote `speaker`, `text` and `at_seconds` and nothing
else. `build_transcript` was already carrying `section_id` through; there was
never one to carry. Every stored turn had it empty, so grouping stored turns by
section returned one empty group.

The observer now takes a zero-argument callable and asks it at append time. A
callable rather than the brain itself, because the observer would otherwise
import the brain to read one attribute and this file is full of Pipecat frames,
which `bot/brain/` is not allowed to know about in either direction. None means
no labelling, which is what the scripts still pass.

**It does not change the stored shape.** `TranscriptTurn.section_id` already
existed and is optional, so old records still load and the panel, the scorer and
the comparison scripts read the same fields they did. What changes is that one
of them stops being blind: `assess_questioning` skips turns with no section, so
on the one real interview on the box it was computing over **0 interviewer turns
instead of 22**. It reported `degraded: False` because it had nothing to look at,
not because the interviewing was fine. The verdict on that particular interview
does not change, but a real interview where the interviewer did repeat itself
would have reported the same way.

### The status pill said "plan generating" when the plan was ready

Not a cache and not a stale poll. Blueprint generation is a BackgroundTask kicked
off at CV upload, and the profile screen is rendered once and never again, so the
pill stayed wrong until the row was clicked. The candidate detail view was the
only screen that polled.

The profile view polls while any candidate's plan is pending or generating, using
the same ticket mechanism, so navigating away cancels it rather than redrawing
over whatever was opened next.

### Two report fields were populated and rendered nowhere

`dealbreakers` now renders as a card covering every dealbreaker the employer
named, with the three states given different weight because they are different
kinds of fact: **observed** is a finding about the candidate, quoted and marked
like a failure; **cleared** is reassurance, quoted and held to the same evidence
standard, marked as settled rather than merely absent; **not reached** is a
limitation of the interview, muted, unquoted, and it says in words that it is not
a mark against the candidate. The wording comes from `as_sentence` on the
contract so it lives in one place, the way the health banners do.

Reports scored before the field existed fall back to the old observed-only card,
because they are still in the database and still readable.

`interview_duration_seconds` sits beside the generated-at line as "interview ran
N min". A thin report reads differently when the interview was eleven minutes of
a planned twenty, and that is the only question this number answers.

**Files**

- `backend/blueprint/clarify.py`, `backend/tests/test_clarify.py`
- `backend/bot/config.py`
- `backend/bot/observers.py`, `backend/bot/run_bot.py`
- `frontend/admin/admin.js`

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 699 passed, 22 skipped
```

## 2026-08-14 — The prompt bound is measured against every fixture, and raised to the edge of the evidence

### The test could not see the breach

`test_section_prompt_stays_small_and_bounded` called `load_blueprint()` with no
argument, so it only ever measured the default fixture. It is parametrised over
`FIXTURES_DIR` now, discovered rather than listed, so a blueprint added next
month is measured without anybody remembering — a hardcoded list would reproduce
the same bug one layer up.

It failed immediately on being rewritten, which is the point of rewriting it:

| fixture | worst case | against the old bound of 2400 |
| --- | --- | --- |
| `pm_senior` (the default, the only one ever measured) | under | passed |
| **`sre_staff`** | **2470** | **over, unnoticed** |
| **`ml_senior`** | **2781** | **over, unnoticed** |

Two fixtures, not one. `AUDIT.md` put `ml_senior` at 2516; the real figure is
2781, because the audit measured a lighter prompt than the test builds — twelve
carried claims clipped to 110 characters, a 400-character rationale and a
600-character contradiction all present at once.

This is the third test found this week asserting something real against an input
that could not fail it, after the two that pinned a false consent notice.

### Raised, not cut, and the measurement already existed

The table this bound came from measured four input sizes on streaming
`gpt-4.1-mini`, eight interleaved rounds each: ~1400 at 778ms, ~1800 at 757ms,
~2300 at 820ms, ~2800 at 742ms. **2781 sits inside that range**, bracketed by the
two nearest arms with no penalty, against a within-size spread of up to 358ms.

So the bound was stale rather than the prompt being fat. Holding 2400 would have
meant cutting material that no measurement says costs anything, which is the
trade this project has refused before.

**2800, and the nineteen tokens above the worst case are not headroom — they are
the end of the table.** Anything past 2800 leaves the range where prompt length
has ever been checked against time to first token. The next addition needs a
fresh measurement, not a larger constant, and the comment on the constant says
so.

No runs were needed and none were taken: nothing was cut, so nothing about the
rendered prompt changed.

### What two blocks both say — the more serious finding

Looking for redundancy turned up a contradiction rather than waste, in the
section that owns an askable dealbreaker. The prompt now carries the same
dealbreaker twice, forty lines apart, with opposite instructions:

```
THINGS WORTH NOTICING
The employer named these as dealbreakers. If one shows up, probe it gently and
factually. Ask for the specific example that would settle it.
- Cannot explain why specific evaluation metrics or methods were chosen
   …
The employer also rules out anyone who cannot explain why specific evaluation
metrics or methods were chosen. … It is a standard, not a question: never read
it out or say anything is being checked.
```

One says *probe it and ask for the example*. The other says *never read it out
and never say anything is being checked*. And "also" is false: it is not a
further dealbreaker, it is the same one restated.

That block was measured last week as having no effect — `not_reached` rose 42 to
45 percent, and questions asking for a time or an occasion in the owning section
did not move — with a revert recommended and not yet taken. This is a second,
independent reason to take it, and it is a better one: a block that contradicts
another is worse than a block that costs 40 tokens.

**Attribution is now stated four times**, in four wordings: `VOICE_RULES` ("if
you did not hear them say it, you may not say they said it"), the seed guard,
the CV heading added today, and `_render_footing`. The fourth is the odd one:
"Refer only to things that appear in the messages below **or in the notes
above**" grants permission over exactly the material the other three withdraw it
from, and the notes above are the CV block. Recorded twice before as the likely
residual source of CV-derived attributions; recorded again here as the clause
that now reads against its own neighbour.

Neither is fixed here. Both are prompt-content changes needing their own
measurement, and this change deliberately altered no rendered text.

**Files**

- `backend/tests/test_brain.py` — parametrised over the fixture directory; bound
  raised to 2800 with the reasoning on the constant.

**Left undone**

The duplicated dealbreaker and the four statements of the attribution rule.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 698 passed, 22 skipped
```

## 2026-08-14 — The scorer stops being told sections were squeezed, and a spec must have something to ask about

Two findings from `AUDIT.md`.

### S5 — the label said "SQUEEZED" about things that were never squeezed

`_render_sections` handed the scoring model `SQUEEZED: {shortfall_reason}`.
`_rebalance_remaining` is the only thing that squeezes anything and it has never
run in any interview on record. What actually sets that field is a section the
candidate declined, one that ran out of time, or one the interview never reached
— and the model was told each of those was a squeeze, in the one place we ask it
to be careful about attributing causes.

It reads `SHORTFALL:` now. **One neutral label carries all of them** because the
reason is already a whole sentence naming which it was, and the label only had to
stop contradicting it:

```
section ml_service_deployment · coverage=insufficient · turns=6 ·
  SHORTFALL: Section ended on budget_exhausted after 6 candidate turn(s)…
section client_communication · coverage=not_started · turns=0 ·
  SHORTFALL: Candidate chose to end the interview before this competency
  was covered… This reflects their decision to stop, not their ability.
section python_ml_basics · coverage=insufficient · turns=3 · DECLINED 3 turns ·
  SHORTFALL: Candidate declined to answer on this topic (3 time(s))…
```

**No scoring run.** The changed line renders only when `shortfall_reason` is set,
and it is set on **0 of 232 sections** across every arm run under the current
clock. Scoring six competent and three deflecting runs would have produced
provably identical output, at eighteen model calls, on a scorer whose control arm
already moves eight quotes on identical input. The effect is deterministic and is
shown above instead.

### coverage_shortfall — decided, not fixed, and it should come out

The flag is set only for `hard_stop` and `budget_exhausted`. `ceiling` decides
87 to 91 percent of sections and is not in that set, so the flag is false on
every section of every current-clock run.

**It is not made to fire on `ceiling`, because a ceiling-ended section is not a
shortfall.** It spent its whole allocation and stopped because it ran out of
turns. If the evidence is still thin that is what `coverage` is for, and
`questioning_health` now says whether the interviewer was the reason.

**The flag is redundant and should be removed.** Everything it tries to say is
already said, better, by something else:

- "never reached" is `coverage == not_started`, which is a distinct enum value
- "ran out of time" and "they declined" are in `shortfall_reason`, which the
  scorer already reads and which is a sentence rather than a boolean
- "did this section get what it needed" is `coverage`

And it is derived at a moment when the thing it derives from can still change.
In the one real interview, `applied_llm_depth` ended on `budget_exhausted` and is
recorded `insufficient` — the exact pair the flag fires on — with the flag
**False**, because `_finalise` read the coverage before a late judgement rewrote
it. That is the fourth mechanism this month evaluated at a point where its input
was not final.

**Not removed here.** It is a contract field with a `schema_version`, zero
readers, and no urgency; removing it is cleanup rather than a fix, and bundling a
contract change with a label fix would have made the measurement above harder to
argue about. Recorded as its own change.

An audit correction while here: `AUDIT.md` says the flag is set on 0 of 120
sections. That is right for the competent scenario under the current clock, but
it does fire 83 times across the older stale arms — all of them
`budget_exhausted`, from when the harness charged 45 seconds a turn and the
budget bound before the ceiling. The flag is near-constant today, not incapable.

### S13 — a spec with nothing to ask about was a valid spec

`EvaluationSpec` had no validator. Zero competencies was accepted, and so were
weights summing to 1.8. `clarify.py` normalises weights and then says in a
comment that "a genuinely malformed spec still fails validation in the contract",
which was not true, because there was nothing there to fail it.

**At least one competency, not four to seven.** CLAUDE.md asks for four to seven
and that is a statement about interview quality — "fewer cannot cover a role" —
rather than a data invariant. Checked before writing the rule: **`sre_staff.json`
ships with 3, and two stored specs in the local database have 2.** A range would
reject a shipped fixture and real stored records, none of which is malformed. An
employer who genuinely wants three gets three; the guidance stays where it
belongs, in the clarification prompt and in `generate.py`'s warning that thin
competencies will not fill the duration. Zero is different in kind: not a thin
interview, no interview.

**Weights within one percent of 1.0, not exactly.** Every consumer that reads a
weight divides by the total, so the sum is cosmetic to behaviour and the check
exists to catch gross authoring error. Erring toward accepting matters because
the system's own repair step rounds to four decimals, and a strict equality would
reject a spec `clarify.py` had just normalised. Measured: 0.005 of drift is
accepted, 0.05 is rejected, six-at-0.9 is rejected, zero competencies is
rejected.

**Every fixture and every stored record still validates**: three fixtures, two
job specs and four candidate blueprints, all sum to exactly 1.0000.

### The blueprint's missing half, and where it went

The blueprint validator catches `planned > duration` and not `planned < duration`,
so a plan spending 10 of 20 minutes was accepted. Added as a validator first —
and it **failed 49 tests**, every one a helper building a small blueprint to
exercise something else, none of them malformed.

So it is a test over the shipped fixtures instead, where the friction is zero and
the target is real. Overcommitment is a plan that cannot be executed;
undercommitment is one that is merely wasteful, and a rule that makes every
future test balance minutes it does not care about is a rule that gets worked
around.

**Files**

- `backend/feedback/score.py` — the label.
- `backend/shared/contracts.py` — `EvaluationSpec` validator.
- `backend/tests/test_blueprint.py` — every shipped fixture spends its duration.

**Left undone**

Removing `coverage_shortfall`, argued above.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 696 passed, 22 skipped
```

## 2026-08-14 — The third banner reaches the panel, the sweep flag reads like the others, and two dead fields stay dead

Three findings from `AUDIT.md`. Two fixed, one deliberately not — and the third
is the one worth reading.

### S9 — the sweep flag parsed truthiness its own way

```python
def sweep_enabled() -> bool:
    return not os.environ.get(SWEEP_DISABLED_ENV)
```

Bare truthiness on the raw string, so `RECORDING_SWEEP_DISABLED=0` and `=false`
both switched the sweep **off**: the string "0" is true to Python. An operator
writing `false` to keep retention running would have stopped it, and the sweep is
the only thing that deletes recordings Daily charges for.

It now reads `_TRUTHY` — "1", "true", "yes", "on" — like the other three flags.
Absent still means the sweep runs.

**It is the only one.** Every other boolean read of the environment was checked:
`term_probing_disabled`, `filler_enabled` and `recording_enabled` already share
`_TRUTHY`; `MAX_CONCURRENT_INTERVIEWS` parses an int with a warning and a floor;
and `stt.py` tests whether `DEEPGRAM_API_KEY` is *present*, which is the
documented switch rather than a flag.

**The test could not have caught it.** It asserted
`sweep_enabled() is (not os.environ.get(...))` — the implementation restated, so
it passed whatever the function did. It now asserts values: unset and every
falsy-looking word keep the sweep on, and only the `_TRUTHY` words turn it off.

### S1 — the third banner

`QuestioningHealth` was derived, attached to the report, fed to the scoring model
and used to cap the recommendation for a week, while never reaching the panel.
`admin.js` read two health objects under a comment that said "Two independent
degradations". There are three, and `banner()` was already generic over any of
them; only the list was not.

So the one thing it exists to tell a recruiter — that thin answers may be the
interviewer repeating itself rather than the candidate having nothing — was the
one thing a recruiter could not read. Three banners now render, in order, none
hiding another. The comment says three.

`dealbreakers` and `interview_duration_seconds` are also populated and unrendered
and are **not** included. The dealbreaker findings need a layout decision — three
states, evidence quotes, and a deliberate distinction between "cleared" and
"never put" that a list of strings would flatten — and that is a design change,
not a missing line. The duration is already visible on the interview row.

### S3 and S10 — the term record and the judge's rationale stay unwired

`SectionOutcome.unexplained_terms` holds what the term machinery exists to
produce: asked twice, never explained. `SectionOutcome.depth_rationale` holds the
judge's one-sentence reason for its verdict. Both are written every section,
persisted, and read by nothing.

**Neither is wired up, and the reason is not that they add nothing. It is that
the term record is not true.**

Across ten runs it recorded 19 terms in 60 sections. Every one was genuinely a
word the candidate used — that much is sound. But reading each against what the
candidate actually said:

| recorded as never explained | what they said |
| --- | --- |
| `systemd` (x4) | "systemd starts each agent and restarts it if it dies" |
| `OpenAI Realtime` (x2) | "speech to speech rather than a pipeline. You lose the transcript as the thing you can audit" |
| `LiveKit Agents` (x2) | "LiveKit Agents holds the audio session" |
| `VAD` (x1) | "the VAD was cutting the turn while the customer was still mid-sentence" |
| `prompt-level rules` (x1) | "they leaked, so I moved them into Python: the agent proposes a booking and the code refuses it" |
| `confusion matrix` (x1) | "the grid of what it predicted against what was actually true" |
| `routing decision` (x1) | "picks which tool to call, so the output is a routing decision rather than prose" |

Each of those is an actor, an action and a consequence — the judge's own STEP 2
test for an explanation. Of twelve distinct terms, one (`Mistral jinja template`,
recorded four times under two different strings) looks like a correctly recorded
failure. **The record is wrong more often than it is right**, and putting it in
front of a recruiter would state that a candidate could not explain things they
explained, in a hiring record.

It would not help the scorer either. The scorer reads the full transcript and can
see those exchanges; handing it this list would mislead a component that
currently gets it right.

**`depth_rationale` is rejected for different reasons.** It is the judge's
justification for its own verdict, and the judge has no equivalent of the
questioning guard: one stored rationale reads "answers were repetitive and lacked
specifics", which is precisely the attribution deliberately removed from the
report when `QuestioningHealth` landed. Surfacing it would reintroduce a defect
that was fixed one layer up. And the scorer already writes better prose from more
evidence: the same reports say "Interviewer repeatedly asked about prompt-level
rule leaks rather than..." in `coverage_gaps`, which is the honest version of the
same observation, correctly attributed.

**Rejected, and why:** into the scorer's prompt (feeds a wrong list to something
that currently reads the transcript correctly, and double-counts against a
coverage downgrade the report already makes); onto the report for a recruiter
(false statements about a person); both (both objections apply). Nothing was
wired, so no scoring run was needed.

**This corrects the audit's own framing of S3.** It was filed as "two weeks of
work terminating in fields nobody reads", implying the fix was to read them. The
fields being dead is currently doing the product a favour. What would have to
change first is the judge's precision on whether a term was explained, which is
its own measurement and is not this change.

**Files**

- `backend/app/recordings.py` — `sweep_enabled` reads `_TRUTHY`.
- `backend/tests/test_recordings.py` — the tautological test now asserts values.
- `frontend/admin/admin.js` — the third banner, and the comment.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 695 passed, 22 skipped
```

## 2026-08-14 — A closed section keeps its verdict, and the CV block says whose words it holds

Two fixes from `AUDIT.md`. **One worked. One did not, and should come out.**

### S11 — a closed section keeps the verdict it closed on

`_advance` queues one last `section_end` judgement for the topic it is leaving,
and that judgement lands after the section is over. `apply_judgment` wrote
`section.coverage` with no check that the section was still open, so the verdict
recorded for a section was never the verdict the brain acted on.

Of 8 sections that ended BECAUSE the judge said `sufficient`, only 2 still said
so afterwards. Five said `partial`, one `insufficient` — including a section that
ended at the two-turn floor and was written up as a topic the candidate had
failed rather than one that had enough.

**"Closed" alone was the wrong test and the suite said so.** The first version
froze any closed section's verdict and
`test_late_judgment_for_a_finished_section_is_still_recorded` failed, correctly:
a `depth` judgement requested while a section was live can also land after the
ceiling moved us on, and that one carries the only reading anybody ever made of
the topic. Refusing it loses the assessment and leaves the heuristic fallback.

So the rule is narrower. **A closed section keeps a verdict it already has; a
closed section that never got one still accepts a late reading.** A section can
only end on `depth_reached` if a verdict exists, so this targets the harm exactly
and leaves the late-arrival case alone.

Claims and contradictions on a late judgement are still applied. Only the
verdict, and the sentence explaining it, are decided once.

| | before | after |
| --- | --- | --- |
| **sections ending on a verdict that still record it** | **2 of 8** | **6 of 6** |

**Two things this does not fix, stated because they are still there.** The
`section_end` judgement is now close to inert: its claims arrive after
`_carry_claims_from` has run, so they are never carried forward and surface only
in the panel's section view, leaving contradictions as the sole benefit for six
to eight judge calls an interview. And the judge is still asked the wrong
question — `_advance` runs `_carry_claims_from` before building the request, so
the section's own claims reach it labelled "claims from earlier sections".
Freezing the verdict changes what we do with the answer, not what we asked.

### S2 — the CV block, and why the fix comes out

The interviewer read `claims_to_verify` back as candidate speech: "You said you
built six production voice agents on LiveKit Agents", at the second substantive
turn of an interview whose candidate had said only that they were building an AI
interviewer. Seven of these across six of ten runs.

The headings changed rather than a guard sentence being added: `ABOUT THIS
CANDIDATE` became `THEIR CV, NOT THEIR WORDS`, and `Claims from their CV worth
testing:` became `Also the CV. Never say "you said" or "you mentioned" about any
of it:`. A heading because "claims" is the word that does the damage — a claim is
something somebody claimed, so the model supplies the claimant — and because this
block renders on every turn of every section, making it the most expensive place
in an over-budget prompt to put a paragraph. Cost 26 tokens against roughly 45
for transplanting the seed guard whole.

**The direct fabrications fell and a worse path opened.**

| | before | after |
| --- | --- | --- |
| fabrications traced to `claims_to_verify`, hand-read | 7 across 6/10 runs | **4 across 2/10 runs** |
| attributions naming something the candidate **never says anywhere** | **0** | **2 runs** |

In one run the candidate never says "LiveKit" at any point. The interviewer
introduces it at turn 6 as its own question — "What was the bet behind using
OpenAI Realtime and LiveKit Agents" — the judge then reads the section transcript,
finds the term in the *interviewer's* words, nominates it as unexplained, and the
term block supplies "You said LiveKit Agents". Four times.

That is a laundering path: CV → our question → judge → term block → attributed to
the candidate. Forbidding the attribution pushed the CV content into a question
instead, and a different block attributed it for us. The heading stops the block
that was named in the audit and does nothing about the route through the judge,
which is the same defect recorded when the judge began reporting the
interviewer's own phrasing as the candidate's.

**Net, it is not an improvement.** Three direct fabrications traded for two runs
containing a worse kind: a word the candidate never uttered, repeated back to
them as theirs. Distinct authored answers reached also fell, 11.1 to 9.8.

**Recommendation: revert S2, keep S11.** The next thing to try is not a third
wording of this block. It is whether `claims_to_verify` belongs in the live
prompt at all — its job is to shape the blueprint's questions, and the seeds
already carry that.

**Also measured**

| | before | after |
| --- | --- | --- |
| mechanism questions per opportunity | 16% | **20%** |
| distinct authored answers reached, of 31 | 11.1  [9..14] | 9.8  [6..12] |
| answers already given in that section | 56%  [45-61] | 58%  [50-67] |
| distinct subjects per section | 2.7  [1..5] | 2.6  [1..6] |
| sections on one subject | 5/60 | 6/60 |
| let-go spoken | 3.1 | 2.1 |
| coverage: partial / insufficient / sufficient | — | 30 / 24 / 6 of 60 |
| `coverage_shortfall` set | 0/60 | 0/60 (unchanged, see AUDIT S5) |

Held: the opening still greets by name, both dealbreakers still reach their
owning sections, and no attribution traced to the indexed seed.

**Files**

- `backend/bot/brain/brain.py` — `apply_judgment` freezes a decided verdict.
- `backend/bot/brain/prompting.py` — the two headings.
- `backend/tests/test_blueprint_source.py` — repointed at the new heading, and
  the negative sibling test with it, which would otherwise have gone vacuous.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 695 passed, 22 skipped
```

## 2026-08-14 — The report says which dealbreakers it could answer and which it could not

**What**

`red_flags_observed` only ever held what fired. Silence there meant "we asked and
they were fine" or "nobody looked", and a reader could not tell which. Measured
across 24 stored reports against one spec: of four dealbreakers, one fired in 10
of 24 runs and the other three fired zero times, with nothing recording whether
they were cleared or never put. In the one real interview on the box the report
cited two of four and said nothing about the rest, and the story that would have
settled one of them was written into the blueprint, in a topic the interview
never reached.

`FeedbackReport.dealbreakers` now carries one `DealbreakerFinding` per
dealbreaker in the spec, always, in spec order, the way `competency_scores`
already backfills a competency the model skipped. Three states: `observed`,
`cleared`, `not_reached`.

**Who judges, and at what cost.** The scorer, in the pass it already makes. It
already reads the transcript against the spec and already produces
`red_flags_observed`, so this is one more field in the same JSON: no second
pass, no extra call, nothing on the live path. The prompt grows by about 190
tokens on a call that runs once per interview, off-path.

**`not_reached` is never the model's to assert.** It is the residue, computed in
code from whatever is left after the fired list and the cleared list are matched
back to the spec. The only way to report coverage of a dealbreaker is to produce
a quote for it, and a model that says nothing produces `not_reached`, which is
the truthful reading of having said nothing.

**What "cleared" requires.** A verified quote of the candidate doing the
opposite of the dealbreaker, through the same `verify_quote` machinery as every
other quote, unchanged. The prompt names the bar in the three shapes that
actually occur: volunteering a bad result, not being asked about one; giving the
reason for a choice, not naming the metric; an actual occasion with specifics,
not a description of how the thing is done in general. Silence never clears
anything.

A clearance whose quotes do not survive verification falls back to
`not_reached`, rather than being dropped the way an unevidenced red flag is.
Dropping is right for a flag because the entry is the accusation. Here the entry
is the report's account of what it could establish, and the honest account of an
unverifiable clearance is that nothing was established. The contract refuses to
construct anything but `not_reached` without evidence.

**`not_reached` reads as a limitation of the interview**, in words rather than
as an enum: "The interview did not put this to the candidate, and did not
observe it either way. This is a gap in the interview, not a finding about the
candidate." Same distinction `QuestioningHealth` exists for.

`red_flags_observed` is untouched and still carries exactly what it did. An
`observed` finding restates one of those, so old consumers keep working.

**Measured**

Six competent runs and three deflecting, scored with the committed scorer and
with this one. **The scorer runs at temperature 0.2, so a second before-arm was
run as a control**, and everything below is read against it.

| | control (before vs before) | change (before vs after) |
| --- | --- | --- |
| red flags, total absolute change | 1 | 2 |
| verified quotes, total absolute change | 8 | 13 |
| recommendation changed | **0 of 9** | **2 of 9** |

**The new field is stable where it matters.** Across two independent after-arms,
39 dealbreaker slots: 87 percent identical. All of the movement is between
`observed` and `not_reached`, which is the pre-existing red-flag noise the
control also shows. **`cleared` gained nothing and lost one**: 11 stable, 0
arriving, 1 leaving. Nothing became cleared that was not cleared before, in
either sample, and all 23 clearance quotes were found in candidate speech.
Under-claiming is the only direction this drifts.

**The split is consistent and it is informative**, over the six competent runs:

| dealbreaker | cleared | observed | not reached |
| --- | --- | --- | --- |
| cannot explain why a metric was chosen | 5 | 0 | 1 |
| vague or generic, no real examples | 1 | 2 | 3 |
| cannot describe a time they deployed or debugged | 1 | 0 | 5 |
| avoids or deflects on negative results | 5 | 0 | 1 |

The two that never fired in 24 reports are now answered in five of six runs
each. The one that is still mostly unanswered, "a time they deployed or
debugged", is the one whose seed question sits in a topic these runs reach last.

**The prong-open evidence clears what it should.** In all three runs where the
candidate says the detector performed at chance and that they told the client to
stop, the negative-results dealbreaker reads `cleared`, quoting that sentence.
Two more clear on a different volunteered failure. The sixth has neither and
reads `not_reached`.

**The deflecting runs are unchanged.** All three still fire the one dealbreaker
that is true of them, with the same evidence, and report the other four as never
put. Five dealbreakers, one fired, four `not_reached`, in all three runs.

**What moved that should not have.** The recommendation rose on two of nine runs
and did so in both after-arms, while the control moved none. Mean rank went 1.67
and 1.67 across the two before-arms, against 2.00 and 1.78 after. The cap logic
is untouched; what moved is the model's own signal, and a block asking what the
candidate put to rest plausibly reframes the whole report. It is small, it is
upward, and it is not noise. Recorded rather than fixed, because a fix belongs
in its own measurement.

**Files**

- `backend/shared/contracts.py` — `DealbreakerState`, `DealbreakerFinding`,
  `FeedbackReport.dealbreakers`.
- `backend/feedback/score.py` — the clearance block, `_match_dealbreaker`,
  `_dealbreaker_findings`, wiring for the empty report.
- `backend/tests/test_feedback.py` — six tests.

**Left undone**

The recommendation drift. Rendering dealbreakers to the interviewer, which needs
the askable-versus-observable distinction handled first: "describes conflict as
someone else's fault" is a pattern across answers, not a question, and asking it
announces the trap.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 695 passed, 22 skipped
```

## 2026-08-13 — A topic can end because the judge says it has enough

**What**

`_advance_reason` checked the ceiling fourth and the judge's verdict fifth. The
ceiling is derived from the section's own allocation, so a section reached it at
the moment it had spent everything it was given, and the verdict was consulted
at a point where the only thing it could do was agree with what had already
happened. Across roughly 320 stored competency sections the mechanism recorded 8
firings, and the only shape that could produce one was a short low-weight
competency whose judge answered before a four-turn ceiling arrived.

Nothing about the judge changes. "Enough" was made explicit when the judge
prompt was written. The check simply sat below one that always won.

The order is now: hard stop, candidate disengaged, declined topic, **verdict**,
ceiling, budget. The hard stop stays above because an interview out of time ends
whatever the judge thinks, and both refusal branches stay above because a
candidate declining is a fact about the person and must never be recorded as a
topic that went well.

**The floor moved with it, and that was not optional.** It was a bare
`turns_spent < floor` guard sitting between the ceiling and the verdict. A
verdict promoted above the ceiling without it could end a competency on its
first answer. It is a condition on the verdict branch now. Every other branch
reads as it did: the ceiling and the budget were already above the old guard, so
nothing below the floor behaves differently.

**Measured**

Ten runs competent and three deflecting, `gpt-4.1-mini`, at 22 seconds a turn.
The advance reason is not persisted anywhere, so it was recorded by patching
`_advance` in a measurement script rather than in this file.

**It fires, and beyond the one shape that could already produce it.**

| | |
| --- | --- |
| competency sections ending on the verdict | **8 of 60 (13%)** |
| ...on the two eight-turn competencies | applied_llm_depth 1/10, evaluation_reasoning 3/10 |
| ...on the six-turn ones | client_communication 2/10, voice_audio_exposure 2/10 |
| ...never | ml_service_deployment, python_ml_basics |
| per interview | [1 0 1 0 0 1 3 0 2 0], mean 0.8 |
| deflecting | **0 of 18**, every section at the ceiling |

Seven of the eight genuinely shortened a topic; one fired on the turn the
ceiling would have ended it anyway. 528 seconds were handed back across ten
interviews, 66 a topic. Interview length did not move, 15.8 minutes against a
configured 20 both before and after: the reclaimed time is not being spent
elsewhere, it is simply not spent.

| | rotation | + this |
| --- | --- | --- |
| turns with neither a seed nor a term | 109/382 (29%) | **100/380 (26%)** |
| mechanism questions per opportunity | 15% | **18%** |
| menu questions per opportunity | 35% | **28%** |
| sections on one subject | 5/60 | **4/60** |
| distinct answers reached, of 31 | 11.1  [6..15] | 10.7  [7..13] |
| worst competency, distinct subjects | 2.3 | **2.5** |
| unsupported attributions | 0.50  [0..1] | 0.30  [0..1] |
| let-go spoken | 2.9 | 2.4 |
| terms reaching the report, deflecting | 3.1 a run | 3.7 a run |

**The result that stops this shipping.** Of the eight sections that ended
because the judge said sufficient, **only two still say sufficient in the
record**. Five say partial and one says insufficient.

`_advance` queues a `section_end` judgment for the topic it is closing, and it
does so *after* `_carry_claims_from` has copied that topic's claims into the
carried list. The judge is then handed the section's own claims under "claims
from earlier sections" and re-judges the identical transcript, and
`apply_judgment` writes `section.coverage` with no check that the section is
still open. The verdict that ended the topic is overwritten by a second opinion
of the same words.

The worst single case: run 7, client communication, ended on the verdict at the
floor of 2 turns, and recorded **insufficient** with no shortfall flag. That
candidate lost four turns of opportunity on this topic and the report will read
it as a topic they failed. This defect predates the change and was harmless
while nothing ended on a verdict.

**What the report says about a topic that ended this way.** `coverage_shortfall`
is not set, which is correct, because `_finalise` only sets it on `hard_stop` and
`budget_exhausted`. But nothing marks the topic as complete either, and its
coverage has been overwritten, so a short topic that had enough is
indistinguishable from one that ran out of road. The reason belongs on
`SectionOutcome`, and the overwrite has to be fixed before any of this is true.

**Files**

- `backend/bot/brain/brain.py` — `_advance_reason` reordered, floor folded into
  the verdict branch.

**Left undone**

The coverage overwrite in `apply_judgment`, which is the blocker. The advance
reason is still not recorded on the outcome. And the reclaimed 66 seconds a
topic go nowhere.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 689 passed, 22 skipped
```

## 2026-08-13 — The topic offers one of its questions per turn instead of all four

**What**

The competency block rendered all four of the section's seed questions every
turn, under "adapted to what they have actually said. Do not read them out
verbatim, and do not work through them in order". That is a menu: a preference
between options all still on the table, which is the shape this repo has
measured at 0 percent compliance against 80 percent for naming the one thing to
say. `_HARD_QUESTIONS` fixed exactly this for the depth ramp and its own comment
says why, offering four also lets the model pick the same one twice. The seed
block was the same failure sitting forty lines above the fix.

It now renders one, indexed by `turns_spent`, in `_render_seed_question`. No new
state, and a repair already does not spend a turn, so a candidate who did not
hear the question is not moved on to the next subject.

Two things were then changed on the strength of the first measurement, and the
second of them did not work.

**A guard, because naming one question got its content handed back as candidate
speech.** These seeds are written from the CV, so the first version produced
"You said you fine-tuned Sarvam-M 24B" before the candidate had said either
word. The block now states that the question comes from the CV rather than the
conversation and names the three openers it must not use.

**Three rules already forbade this and one of them licensed it.** `VOICE_RULES`
says plainly that if you did not hear them say it you may not say they said it,
but says it while describing how to acknowledge an answer, and this failure is
in how a question is introduced. `_render_footing`, the block directly above,
says "refer only to things that appear in the messages below or in the notes
above" and the seed IS one of the notes above. That clause was left alone rather
than fixed in the same measurement.

**The wrap was removed, and that is the part that failed.** Four seeds, six to
eight turns. Wrapping put turn N and turn N+4 on the same seed, which produced 3
repeated questions exactly four turns apart. Stopping at the last seed removed
those and cost far more elsewhere.

**Measured**

Ten runs an arm, competent candidate, `gpt-4.1-mini`, at the measured 22 seconds
a turn. Deflecting was not run: five replies, returns the fifth forever, cannot
reach a new subject.

| | before | with wrap | wrap removed + guard |
| --- | --- | --- | --- |
| unsupported attributions / interview | 0.50  [0..1] | 0.90  [0..3] | **0.50**  [0..1] |
| ...overlapping any seed in that section | 3/5 | 9/9 | **3/5** |
| ...overlapping the seed live on that turn | 1/5 | 8/9 | **2/5** |
| **sections spending a topic on ONE subject** | 14/60 | 7/60 | **5/60** |
| distinct authored answers reached, of 31 | 9.5  [6..14] | 10.9  [7..14] | **11.1**  [6..15] |
| answers already given in that section | 56%  [50-69] | 54%  [45-61] | 54%  [44-62] |
| exact repeated questions | 10/381 | 16/392 | **1/382** |
| ...four turns apart | 0 | 3 | **0** |
| ...longest consecutive streak | 3 | 5 | **2** |
| worst competency, distinct subjects | 1.6 | 2.2 | **2.3** |
| **mechanism questions per opportunity** | **24%**  [14-47] | 22%  [8-61] | **13%**  [5-22] |

**The guard worked.** Attributions are back to the pre-rotation rate and the
coupling to the seed is gone with them: 9 of 9 overlapping a seed became 3 of 5,
which is what it was before any of this. The rotation's own gains held or
improved. One-subject sections went from 14 of 60 to 5.

**Removing the wrap broke the back half of every section, and the split says so
exactly.** Mechanism questions on turns four and later:

| | with a seed | with a wrapped seed | with none |
| --- | --- | --- | --- |
| mechanism | 19% | 21% | **6%** |
| menu | 31% | 29% | **54%** |

Without a subject in front of it the model takes the ramp's demand instead of
the window's content, and asks for the trade-off behind a term nobody has
established. That is the precise failure `_probe_shape` exists to catch. **The
ramp alone is not enough on a turn with no seed**; it supplies a shape and the
seed was supplying the subject, and the coupling was invisible until the seed
was taken away. The front half fell too, 33 percent to 22, of which the rotation
accounts for about 8 points and the guard about 3.

The wrap cost 3 repeated questions across ten runs. Losing the back half costs
about twenty mechanism probes over the same ten.

**Held.** The let-go still spoken and more often, 2.1 an interview to 2.9. The
communication branch still asks how they explained rather than how the thing
works, 27 against 21, with one mechanism question in a communication section as
there was with the wrap. The worst competency stayed above its 1.6 baseline at
2.3.

**Files**

- `backend/bot/brain/prompting.py` — `_render_seed_question`, one call site.

**Left undone**

The wrap should go back, with the guard kept. That combination has not been run.
And `_render_footing`'s "or in the notes above" is still there.

**Confirm**

```bash
cd backend && uv run pytest -q -m "not llm"          # 689 passed, 22 skipped
uv run python scripts/compare_models.py --scenarios competent --runs 10
```

## 2026-08-13 — The judge is asked which terms the interview NEEDS, not which are undefined

**What**

The judge's unexplained-terms instruction gains a first gate. It used to ask,
in effect, "did they say what this does" for every technical term. It now asks
first whether the claim is standing on the word at all.

**Why**

Every change to this feature has bounded HOW MANY probes happen. None improved
WHICH. The cap proved it: distinct answers per section on the deflecting
candidate read 1.17 in both arms, identical to two decimals. Four fewer probes,
the same character of repetition.

The instruction was the reason. It reported "bf16" from an ML engineer using it
correctly in a sentence about training precision and "session management
concurrency" from someone who cannot say what it does, because both are
technical terms without a definition attached. Only one is worth a question.

**The gate, and why this formulation**

> STEP 1. IS THE CLAIM STANDING ON THIS WORD?
> Take what the candidate is claiming around the term and ask one question: if I
> do not know what this word means, can I still judge that claim?

Written as a gate a term must pass rather than an exclusion it must escape,
because that is the shape this repo has measured twice: removing the alternative
beats adding emphasis. The old wording had "ordinary words of the field used in
passing" as the last of two exclusions, and it did not hold.

Two worked examples carry it, because a concrete example beats an abstract
instruction here every time. "I have introduced the layer of a session
management concurrency, which allows the multiple users to make the payment at
the same time" stands on the word; "we trained it with bf16 LoRA on a SageMaker
ml.g7e.2xlarge" does not.

**Explicitly not a seniority rule**, stated in the prompt: a senior person who
cannot explain a word their own claim rests on is the case worth catching, and
seniority never removes a term. **Explicitly not a stop-list**: the same word
passes or fails depending on the sentence, and the prompt says so with "fine
tuning" as the example.

**Tested on stored transcripts before any interview ran**

Old instruction against new, on four stored sections:

| section | OLD | NEW |
| --- | --- | --- |
| competent, evaluation_reasoning | guardrails, prompt-level rules | one clause |
| competent, applied_llm_depth | guardrails, **GPTQ 4-bit, quantised** | guardrails |
| deflecting, product_strategy | session management, concurrency control, OLTP, OLAP | **identical** |
| deflecting, stakeholders | concurrency control session | **identical** |

It drops the field vocabulary from the competent sections and returns the
deflecting lists unchanged, which is the separation the change is for. Four
judge calls, before ninety minutes of interviews.

**Measured, ten runs a side**

The before arm is the cap task's after arm, reused rather than re-run.

| competent, n=10 | before | after |
| --- | --- | --- |
| **consecutive on one subject** | **29% ± 5% [18..35]** | **16% ± 7% [4..26]** |
| sanctioned probes per interview | 7.8 | 6.7 |
| mechanism questions | 29% ± 14% [12..55] | 30% ± 8% [18..42] |
| distinct answers per section | 3.08 ± 0.54 | 2.93 ± 0.31 |
| terms reaching the report | 1.30 ± 0.90 | 1.50 ± 0.81 |
| let-gos spoken | 2.80 ± 1.33 | 2.40 ± 0.92 |
| unsupported attributions | 0.00 ± 0.00 | **0.40 ± 0.92 [0..3]** |
| median interviewer turn | 18.50 | 17.90 |

| deflecting, n=10 | before | after |
| --- | --- | --- |
| **consecutive on one subject** | **29% ± 7%** | **37% ± 4%** |
| sanctioned probes per interview | 8.0 ± 0.0 | 8.0 ± 0.0 |
| mechanism questions | 14% ± 4% | 13% ± 3% |
| **terms reaching the report** | **1.70 ± 0.78** | **1.60 ± 0.49** |
| let-gos spoken | 3.80 ± 0.40 | 3.80 ± 0.40 |
| median interviewer turn | 18.40 | 18.40 |

**What survives the spread.** Consecutive-same-subject on the competent
candidate, 29 to 16 percent, about two combined standard deviations, while the
probe count barely moved. And the same measure on the deflecting candidate going
the other way, 29 to 37. Everything else overlaps.

**The character changed on one candidate and not the other, and the reason is
visible in the cheap test.** The competent candidate's terms include plenty that
fail the new gate, so the judge returns a shorter, better list. The deflecting
candidate's four nominatable terms are all load-bearing: every one passes gate
one, the list is unchanged, and the same eight probes work through a shorter
list of targets more often.

**Sampled by reading**, sixteen probes from the after arm: twelve read as worth
asking, against nineteen of thirty-six before. Directional on that sample size,
not settled.

**How**

`backend/bot/brain/drivers.py`, the `UNEXPLAINED TERMS` block. The judge runs off
the live path, so this costs no latency.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 689 passed, 22 skipped

**Not done**

- **Unsupported attributions rose on the competent candidate**, 0.00 to 0.40 per
  interview, all four instances the same shape: "you said statistically flat" and
  "you said statistically significant" to a candidate who said "well inside the
  noise". Those words come from the blueprint's own claim and seed question, not
  from the candidate, so the interviewer is quoting the CV back as if it were
  speech. Pre-existing and small, but it went up and it is a claim about a person.
- The new gate occasionally returns a clause rather than a term, "refuses it if
  it violates a rule". That produces an awkward probe and was visible in the
  cheap test before the runs.
- Deflecting repetition got worse and the cap cannot help there: its probe count
  is pinned at 8 in both arms.

---

## 2026-08-13 — Term probes get a budget for the whole interview

**What**

`MAX_TERM_PROBES_PER_INTERVIEW = 8`, sitting above the per-section allowance,
which is unchanged.

**Why a total, when five attempts have changed which term gets probed**

Because the per-section allowance cannot bound this and that is now measured. A
section has two probes and the judge always has something unexplained to
nominate, because every answer contains a term it does not define. Explaining
`bf16` in the fixture took its ten probes to zero and handed all ten to
`Mistral jinja template`: 31 probes before, 31 after. Something always gets
probed. Only a total budget addresses that.

**Where the 8 came from**

Every sanctioned probe in six interviews, read, judging whether a human
interviewer would have asked that question. Worth-asking came to **7, 4 and 8**
on the competent candidate and **6, 6, 6** on the deflecting one. Set at the top
of that range rather than the mean, for the same reason `MIN_COMPETENCY_TURNS`
is the old flat ceiling: a cap must not be able to remove a question that was
worth asking.

About half of all probes now read as worth asking, 37 of 72, against 17 of 72
before the false-escalation fix. That is what makes a cap the right tool: when
most probes were waste the problem was which ones, not how many.

**Three decisions**

**Nothing is said when the budget runs out.** The let-go exists because somebody
pushed twice on one word and then met with silence is left wondering whether
they failed. A budget quietly ending is not that: nobody has been pushed, and "I
have run out of questions about what words mean" is not a sentence a person
says. The topic asks its own questions instead.

**Spent front to back**, which the reading says is safe: the worthwhile probes
cluster in the first two topics and the heaviest competency, and the latest of
them sat at position seven of twelve. The three late communication probes that
worried me survive at 8.

**The per-section allowance stays underneath.** It is what re-enabled a
communication topic asking how somebody explained a term a technical topic asked
the workings of, and it stops one topic spending the whole interview.

**Measured, ten runs a side, twenty interviews each arm**

Three runs has misled this project repeatedly, so this is ten. On identical code
the before arm produced consecutive-same-subject anywhere from **12% to 59%**,
which is wider than most differences read into three-run comparisons this week.

| competent, n=10 | before | after |
| --- | --- | --- |
| sanctioned probes | 9.00 ± 2.14 [6..12] | **7.80 ± 0.60 [6..8]** |
| mechanism questions | 38% ± 7% [30..52] | 29% ± 14% [12..55] |
| distinct answers per section | 2.58 ± 0.24 | **3.08 ± 0.54** |
| consecutive on one subject | 34% ± 14% [12..59] | 29% ± 5% [18..35] |
| terms reaching the report | 1.80 ± 0.98 | 1.30 ± 0.90 |
| let-gos spoken | 3.70 ± 1.00 | 2.80 ± 1.33 |
| median interviewer turn | 18.85 | 18.50 |

| deflecting, n=10 | before | after |
| --- | --- | --- |
| **sanctioned probes** | **11.90 ± 0.30 [11..12]** | **8.00 ± 0.00 [8..8]** |
| mechanism questions | 18% ± 6% [13..33] | 14% ± 4% [10..25] |
| distinct answers per section | 1.17 ± 0.00 | 1.17 ± 0.00 |
| consecutive on one subject | 33% ± 11% | 29% ± 7% |
| **terms reaching the report** | **1.70 ± 0.78** | **1.70 ± 0.78** |
| **let-gos spoken** | **5.70 ± 0.46** | **3.80 ± 0.40** |
| median interviewer turn | 18.40 | 18.40 |

**What survives the spread, and it is a short list.** Sanctioned probes on the
deflecting candidate, 11.90 to 8.00 with no overlap at all. Let-gos on the same,
5.70 to 3.80 with no overlap, which follows: fewer probes means fewer terms
reach their per-term cap. And the collapse in variance on the competent
candidate, standard deviation 2.14 down to 0.60, which is what a ceiling does to
an upper tail.

**Everything else overlaps and is reported as not surviving.** Mechanism
questions fell 38% to 29% and 18% to 14%, but the after arm's spread is 12 to 55
percent, so the fall cannot be told from noise. Consecutive-same-subject barely
moved. Distinct answers per section rose 2.58 to 3.08, which is about one
combined standard deviation and is the most promising of the soft numbers
without being conclusive.

**Nothing on the guard list moved.** Terms reaching the report on the deflecting
candidate are **identical**, 1.70 ± 0.78 in both arms, so deflection is caught
exactly as well. The let-go is still spoken. Mechanism questions at 29% and 14%
are far above the probing-off baseline of 3 to 5 percent, so the cap is not too
tight.

**How**

- `backend/bot/brain/brain.py` — the constant, an interview-wide counter beside
  the per-section one, and a gate above the per-section gate.
- `backend/tests/test_brain.py` — one test asserting both bounds, because a
  budget that replaced the per-section allowance would let one topic spend the
  whole interview.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 689 passed, 22 skipped

**Not done**

- **Repetition is bounded, not fixed.** Distinct answers per section on the
  deflecting candidate is identical to three decimal places, 1.17 in both arms:
  the candidate still gives the same answer to the probes that remain. There is
  simply less probing. A cap cannot make a question better, only rarer.
- The interview-wide budget makes the earlier N=3 reading properly void, and it
  should be re-read on these transcripts before the number is trusted a second
  time. The population changed again.
- The two arms were run as parallel per-scenario processes to fit forty
  interviews into ninety minutes rather than three and a half hours. The before
  arm ran through a scratch wrapper that neutralises the new constant, because
  the code was already edited by then; that reproduces the pre-cap condition
  without reverting and re-reverting.

---

## 2026-08-13 — The scripted candidate explains its own terms, and the loop moves rather than ends

**What**

Six answers in `competent_candidate` now explain the term that selects them.
Harness only; no production code.

The loop that motivated it, from the transcripts after the escalation fix:

    I:    "You said bf16. Walk me through how that actually works."
    CAND: "97.08 percent on the bf16 baseline. Same evaluation set, same
           prompt, same parsing."

The answer named the term and never explained it, so the judge saw bf16
unexplained again, nominated it again, and the interviewer asked again. It was
probed in five of six sections in one run.

**The split, and it is three tiers**

**Explained**, because they are checkable specifics a senior engineer would
define on request: `bf16`, `LoRA`, `GPTQ 4-bit`, `tool call classifier`,
`systemd` and `LiveKit Agents`, and `confusion matrix`.

**Left unexplained on purpose**: `agent`, `evaluator`, `fine tuning`, `peft`,
`LLM`. These are the words of the job. **A senior person uses them without
defining them, and an interviewer that spends a turn asking has wasted it** —
which is the failure this scenario was built to expose, so the fixture has to
keep making it possible. A candidate who explains everything the moment it is
asked about means the term block can never fail and stops being under test.

**The gap, untouched**: Docker, containers, version control, rollback still get
"I don't know about that one, so I can't tell you." That is the only real hole
this candidate has and the interview should still be able to find it.

Answers kept their voice and roughly their length: the longest is 58 words
against 51 before.

**Measured, three runs, competent only**

**It worked for the terms that were properly explained.**

| term | probes before | after |
| --- | --- | --- |
| `bf16` | **10** | **0** |
| `tool call classifier` | **5** | **0** |
| `Mistral jinja template` | 0 | **10** |
| `LiveKit Agents` | 2 | **8** |

**And that is the finding: the loop moves, it does not end.** Total probes are
unchanged, 31 against 31. The section still has two probes to spend and the
judge always has something unexplained to nominate, so the allowance is always
spent on whatever is left. Explaining `bf16` did not free the turn; it handed it
to `Mistral jinja template`.

**Two of my own explanations were too thin to count.** `LiveKit Agents` gained
"holds the audio session", which is a gesture rather than an explanation, and
the judge kept nominating it: 2 probes became 8. That is a fair criticism of the
edit rather than of the product.

| | before | after |
| --- | --- | --- |
| distinct answers per section | 2, 1, 1, 4, 2, 3 / 4, 4, 4, 4, 2, 3 / 3, 2, 3, 3, 3, 3 | 6, 3, 1, 3, 3, 3 / 2, 4, 2, 2, 2, 2 / 2, 5, 3, 3, 3, 3 |
| mechanism questions | 44/118 = 37% | 41/120 = 34% |
| consecutive questions on one subject | 23% | **41%** |
| questions quoting an earlier section | 19% | 25% |
| terms reaching the report | 3, 2, 2 | 2, 1, 5 |
| unsupported attributions | 0 | 1 |
| median interviewer turn | 17, 18, 17 | 17, 19, 19.5 |

Distinct answers per section is flat, mean 2.8 against 2.9. Consecutive-
same-subject got worse, and the honest reading is that it followed whichever
term happened to become dominant rather than tracking anything about the change.

**Three runs cannot read this and that is stated rather than worked around.**
The same code produced 2, 3 and 5 distinct probed terms, and one section drew 1
distinct answer in one run and 5 in another. To separate a real effect from that
spread would need something closer to ten runs a side, which is about forty
minutes of wall clock and twenty interviews. This entry reports direction, not
magnitude.

**How**

`backend/bot/brain/harness.py`, six answer strings. No triggers changed, no
selector change, no production code.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 688 passed, 22 skipped

**Not done**

- **The selector still returns the same answer to different questions**, and
  this change does not address it. It reduces the harm, because the answer it
  returns now explains its term, but a differently-worded question about the
  same subject still gets identical text.
- **The loop is structural and no fixture change can end it.** Any answer
  contains some term that is not defined in it, so the judge can always nominate
  something and a two-probe allowance will always be spent. Ending it needs a
  cap on probes per interview, which is the task this was meant to clear the
  ground for.
- `Mistral jinja template` is now the most-probed term and is not explained
  anywhere. Explaining it would move the loop again rather than end it, which is
  the point above.

---

## 2026-08-13 — A section's first question stops claiming it asked before

**What**

A section's first ask about a term said *"You have said X twice now. What does
it actually do?"* while the brain's own log recorded *"raised 1x, section
evaluation_reasoning has spent 1 of 2"* on the same turn. It happened 29 times
across six runs, 43 percent of every term probe fired.

**The cause was not what anybody thought, including this changelog**

It was written up here, and by me last week, as the model carrying the phrasing
across a section boundary out of the bridge. **That is wrong, and it was checked
rather than assumed.** For all 29, the reconstructed window the model actually
had was searched for the phrase: it was in the bridge 0 times and elsewhere in
the window 0 times. **The model was not imitating anything. The prompt told it
to say that.**

`times_raised` is a cached copy of a section's raise count, carried on the
`UnexplainedTerm` object. `_open_terms` is replaced only in `apply_judgment`,
and only for the section being judged, so at a new section's first turn it still
holds the previous section's objects with the previous section's counts.
`section.term_raises` correctly said zero; the field on the object said one or
two; and the field is what the prompt branch reads. Two representations of one
fact, disagreeing.

Proved directly before changing anything: drive a term to one ask in section
one, advance, and render — the block emits the escalated wording while the
section's own count is empty.

**How**

`unexplained_terms` refreshes `times_raised` from `section.term_raises` before
anything reads it. `apply_judgment` already makes exactly this assignment; doing
it here too means it no longer depends on a fresh judgement having arrived.

It also fixes the ordering, which sorted by the same stale field: at a section
start, terms were being ranked by the previous section's raise counts.

**Not a prompt instruction**, deliberately. Telling the model not to say "twice
now" would have been the sixth attempt to fix something by adding a sentence,
and it would have been aimed at a cause that does not exist.

**Measured**

Three runs each, competent and deflecting, `gpt-4.1-mini`, at 22 seconds.

| | before | after |
| --- | --- | --- |
| **escalation on a section's first ask** | **29** | **0** |
| the real escalation, later in a section | 8 | **22** |
| mechanism questions, competent | 22/120 = 18% | **44/118 = 37%** |
| mechanism questions, deflecting | 24/237 = 10% | **42/237 = 18%** |
| probes worth asking, read | 17/72 = 24% | **44/67 = 66%** |
| consecutive questions on one subject, competent | 41% | **23%** |
| consecutive questions on one subject, deflecting | 27% | 37% |
| questions quoting an earlier section | 27% and 27% | **19% and 20%** |
| let-gos spoken | 1,4,5 and 6,6,5 | 4,6,2 and 6,6,6 |
| terms reaching the report | 1,1,1 and 2,1,1 | **3,2,2** and 2,1,1 |
| unsupported attributions | 2 and 0 | **0 and 0** |
| median interviewer turn | 20, 20, 18 | 17, 18, 17 |

**The numerator moved, which was not expected.** The prediction was that
removing 29 false escalations would take probes-worth-asking toward 41 percent
by shrinking the denominator. It went to 66, because those turns were not merely
wasted — they were *displacing* real questions. With the count true, a section's
first ask is a plain first ask about whatever term is actually open, and the
sections stopped circling one word: the competent runs now probe `agent`, `tool
call classifier`, `bf16`, `LiveKit Agents`, `refuses`, `leaked` and
`deterministic` where they used to probe one term twelve times.

**One number to read carefully.** Mechanism questions doubled while sanctioned
probes fell from 72 to 67, so the rise is not the term block firing more — it is
more probe-shaped questions overall, including unsanctioned ones. It is a real
rise and it is not all attributable to this change.

**And one that moved the wrong way.** Consecutive questions on one subject rose
on the deflecting candidate, 27 to 37 percent. That candidate has about four
nominatable terms in total, so a section that asks about them properly returns
to them; it is not obviously wrong, and it is not dismissed.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 688 passed, 22 skipped

**Not done**

- The stale `_open_terms` at a boundary is still stale. This fixes the count it
  carries, not the fact that a new section's first offer comes from the previous
  section's list. That is still the prerequisite for any selection fix.
- Nothing here caps probes per interview. The reading that produced N = 3 was
  taken before this fix, on a run set where 43 percent of probes were the
  falsehood; it should be redone on these transcripts before a cap is chosen.

---

## 2026-08-12 — A baseline for the interview without term probing, and it is not what anyone expected

**What**

`TERM_PROBING_DISABLED`. Absent means today's behaviour, which is the opposite
polarity to `RECORDING_ENABLED` and `FILLER_ENABLED` and deliberately so: those
guard features nobody has switched on yet, this guards one that has shipped in
every measurement ever taken here.

**Nothing is removed.** The block, its state, its caps, its tests and its
default are untouched.

**What it suppresses, and what carries on**

One gate, in `unexplained_terms`. Everything downstream reads that: the prompt
block renders from what it returns, `_offered_term` is taken from it, the
director's filler asks it. Return nothing and no term is put in front of the
model, so none is counted, capped, escalated, let go of, or written to the
record.

Everything else runs. The judge still fires after every answer and still names
what it thinks is unexplained; `section.unexplained_terms` still records what it
said. The depth ramp still fires, contradictions still take the mandatory slot,
the section's own questions are untouched.

**Measured: twelve interviews, then all twelve scored**

**The transcripts. The feature does exactly what it was built to do.**

| | probing on | probing off |
| --- | --- | --- |
| mechanism questions, competent | 22/120 = **18%** | 3/114 = **3%** |
| mechanism questions, deflecting | 24/237 = **10%** | 13/237 = **5%** |
| consecutive questions on one subject, competent | 41% | **26%** |
| consecutive questions on one subject, deflecting | 27% | 36% |
| questions quoting an earlier section, competent | 27% | **17%** |
| questions quoting an earlier section, deflecting | 27% | **12%** |
| a section's first ask says "twice now" | **15 and 14** | **0 and 0** |
| terms reaching the record | 1,1,1 and 2,1,1 | 0,0,0 |
| unsupported attributions, competent | 2 | **0** |
| median interviewer turn | 20, 20, 18 | 18, 18, 18 |

**The off condition reproduces the pre-feature baseline**, which is what makes
this measurable: mechanism questions ran at 2 in 39 real opportunities, 5%,
before term probing existed, and off reads 3% and 5%. The feature triples to
quadruples that. It is not a small effect and it is real.

**The reports. This is where the expectation broke.**

| | probing on | probing off |
| --- | --- | --- |
| competent, recommendation | limited ×3 | limited, **mixed**, limited |
| competent, competencies scored | 6/6, 5/6, 5/6 | **6/6, 6/6, 6/6** |
| competent, gaps named | 6, 1, 3 | 3, 3, 3 |
| deflecting, recommendation | **insufficient_signal ×3** | limited_evidence ×3 |
| deflecting, competencies scored | 0/6 ×3 | 4/6, 6/6, 6/6 |
| deflecting, dealbreaker fired | **3 of 3** | **3 of 3** |

**The dealbreaker fires in both conditions on the deflecting candidate**, with
verified quotes, and it is true of them in both. The feature is not what catches
it. The scorer reads the transcript, sees a candidate answering every question
with the same sentence, and says so:

> The transcript shows the candidate repeating a generic explanation of
> concurrency and session management in response to nearly every question,
> regardless of topic. There is no evidence of specific decisions, trade-offs,
> stakeholder management, metrics, or leadership beyond this repeated phrase.

That is the off condition. It is as damning as the on condition's six
`insufficient_signal` verdicts, and it is arguably more useful, because a 2.0
with a rationale is a judgement a recruiter can act on where "we could not tell"
is not.

**And the finding that reframes the whole thing: the record never reaches
anybody.** `FeedbackReport` has no field for unexplained terms, and
`_render_sections` passes the scorer `coverage`, `turns`, `declined_turns` and
`shortfall_reason` — not `unexplained_terms`. **The asked-and-unexplained record
that the caps, the escalation, the let-go and three tasks of work all produce is
stored in `SectionOutcome` and read by nobody.** The feature's only route to the
report is its effect on the transcript.

**What the fixture does to this, and which result to trust**

62% of the concentration on the competent runs is the fixture: no answer in the
script defines "tool call classifier", so the judge can never drop it and the
probe can never resolve. **That inflates the case against probing on the
competent candidate** — with somebody who could explain the term, the on
condition would look better than it does here.

So the deflecting result is the one to trust, and it is the one where the two
conditions come out level.

**Read side by side, the competent transcripts are worse with probing.** Every
one of six sections in one run opens with the false escalation, and its
communication section asks the same question four times verbatim. Without
probing the same run's evaluation section runs: validate against GPT-4o, then
which metric and why, then statistical flatness against bf16, then what
statistical test established it. Roughly 27 distinct subjects with probing, 35
without.

**How**

- `backend/bot/config.py` — the flag, with its polarity explained.
- `backend/bot/brain/brain.py` — one gate in `unexplained_terms`, and a local
  import so `bot/brain/` stays free of anything that reads the environment at
  import time.
- `backend/tests/test_brain.py` — a test that off means off and that the judge
  still runs and its list is still recorded.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 687 passed, 22 skipped
    TERM_PROBING_DISABLED=1 uv run python scripts/compare_models.py \
        --scenarios competent deflecting --models gpt-4.1-mini --runs 3

**Not done**

- Nothing is switched off. The default is unchanged and this is a measuring
  instrument, not a deprecation.
- **The record not reaching the report is the thing to fix before judging this
  feature.** It is the one output the off condition cannot produce, and it is
  currently dead weight. Wiring it into `_render_sections` and the report, then
  re-running this comparison, is the honest next step — the feature has never
  been measured with its own output visible.
- The false escalation is gone in the off condition (0 against 15 and 14),
  which is a second demonstration that it comes from the term block's wording
  being carried across a section boundary.

---

## 2026-08-12 — The term-selection change is reverted

**What**

The second sort key added below is out. `unexplained_terms` orders by
`-times_raised` again, as it did before.

**The entry below stands.** It records the attempt and the measurement, and it
should not be read as describing the code: this supersedes it, the way the
reordering entry was superseded.

**Why**

It cannot fire where it was written for. `_open_terms` is assigned only in
`apply_judgment`, and only when the judgement is for the current section, so at
the first turn of a new section the list still holds the **previous** section's
terms. Every one of those was asked in an earlier topic, so the tie-break marked
them all equally stale, the judge's order decided again, and the same term was
picked. The rule was correct inside a section, where nothing needed changing,
and inert at the boundary, which was the only moment it was for.

And it was not free:

| | before | after |
| --- | --- | --- |
| mechanism questions, competent | 27% | **22%** |
| terms reaching the report, deflecting | 2, 2, 2 | **1, 1, 1** |
| worst single run | 8 of 10 asks on one term | **12 of 12** |

**The diagnosis that did not hold, which is the third in this area**

"The judge offers six distinct terms in a single section and the interviewer
asks about one" came from a judge replay on the **competent** candidate. The
deflecting candidate's whole script is five sentences containing about four
nominatable terms, so its ceiling on distinct terms is its own vocabulary and
not the selection rule. **"A selection problem, not a judge problem" was true of
one scenario and was stated without that qualification.**

The pattern is the useful part, and it is now three for three here: the
imitation diagnosis was really the carried-claims block; the brain-level term
state diagnosis moved a number and left the transcripts identical; and this one
generalised from the scenario where the fixture, not the product, causes the
concentration. Every one of them was confident. **The two things that worked all
week — the harness clock and the report fairness work — came from measuring
something and reading the output, not from reasoning about what the model was
attending to.**

**How**

- `backend/bot/brain/brain.py` — the key, and the `asked_elsewhere` set it
  needed. A short note stays on `unexplained_terms` saying what was tried and
  why it cannot work from there, so the next person does not rediscover it.
- `backend/tests/test_brain.py` — the test that came with it, and the top-level
  import it added. Every other use of `UnexplainedTerm` in that file has its own
  local import, so the import block is byte-identical to the committed one again.

Everything else from this week is untouched: the depth-ramp rotation,
`seconds_per_turn` from `SECONDS_PER_COMPETENCY_TURN`, the harness trigger fixes
and `--reverse-sections`, the per-section allowance with the interview-wide
record, `QuestioningHealth`, and the carried-claims boundary rule. Verified by
fingerprinting all twelve changed files before and after: only these three moved.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 686 passed, 22 skipped

**Not done — and this is the real state of the problem**

- **The stale `_open_terms` at a section boundary is the prerequisite for any
  selection fix.** Nothing can choose a fresh term at a boundary while the only
  candidates are the previous section's. Clearing it means no term block renders
  on the first turn of a section, which is a behaviour change in its own right
  and needs measuring alone rather than bundled with a selection rule.
- **The false escalation is still there.** A section's first ask says "you have
  said X twice now" 11 times on the competent runs and 13 on the deflecting
  ones, while the brain's own count correctly reads it as the first ask in that
  section. The model carries the phrasing across the boundary in the bridge.
  This is the falsehood `terms.py` was written to prevent arriving by a route
  that file cannot see.

---

## 2026-08-12 — The term is chosen differently now, and it changed nothing

**What**

The interviewer picked which term to ask about with one sort key,
`-times_raised`, then took the first item. Python sorts stably, so on ties it
kept the judge's own order, and the judge listed the same term first every time.
Counts reset at every section boundary, so every section began on the same tie
and picked the same word.

There is a second key now. `-times_raised` still leads, so within a topic
nothing moves and the second ask still escalates, still reaches the cap, still
closes, still gets let go of out loud. The tie-break prefers a term no earlier
topic has already spent its asks on, and the judge's order still decides among
those.

**It did not work, and the bar was set in advance**

Three runs each, competent and deflecting, `gpt-4.1-mini`. The bar was: on the
deflecting scenario, distinct sanctioned terms per run goes up and the top
term's share goes down, without mechanism questions falling. **All three
failed.**

| deflecting | before | after |
| --- | --- | --- |
| distinct sanctioned terms per run | 2, 2, 2 | **2, 2, 1** |
| top term's share of asks | 7/12, 6/12, 8/10 | **6/12, 6/12, 12/12** |
| mechanism questions | 36/237 = 15% | **32/237 = 14%** |
| terms reaching the report | 2, 2, 2 | **1, 1, 1** |

| competent | before | after |
| --- | --- | --- |
| distinct sanctioned terms per run | 2, 1, 4 | 3, 1, 2 |
| mechanism questions | 32/120 = 27% | **27/120 = 22%** |
| consecutive questions on one subject | 19% | 18% |
| questions quoting an earlier section | 25% | 25% |
| terms reaching the report | 0, 1, 2 | 2, 1, 1 |
| unsupported attributions | 1 | **0** |
| median interviewer turn | 16.5, 18.5, 15 | 17.5, 19, 18 |

**Why it did not work, and it is a flaw in the fix rather than a mystery**

`_open_terms` is assigned only in `apply_judgment`, and only when the judgement
is for the current section. **At the first turn of a new section no judgement
for that section has arrived yet, so the list still holds the previous
section's terms.** Every one of them has been asked in an earlier topic, so the
new tie-break marks them all equally stale, the judge's order decides again, and
the same term is picked.

The rule is right inside a topic and inert at the boundary, which is the only
moment it was written for.

**And the earlier diagnosis was drawn from the wrong scenario.** The judge
replay that found "six distinct terms offered in one section, one asked about"
was run on the competent candidate. The deflecting candidate says five
sentences containing about four nominatable terms in total, so its ceiling on
distinct terms is its own vocabulary, not the selection rule. "A selection
problem, not a judge problem" holds for one scenario and not the other, and it
was stated without that qualification.

**How**

- `backend/bot/brain/brain.py` — one sort key added to `unexplained_terms`.
- `backend/tests/test_brain.py` — a test asserting both halves: the escalation
  still leads inside a topic, and a term an earlier topic spent its asks on goes
  last in the next. It passes, which is the point: the rule does what it says
  and that turns out not to be enough.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 687 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent deflecting \
        --models gpt-4.1-mini --runs 3

**Not done**

- **The false escalation, deliberately left.** Every section after the first
  still opens with "you have said X twice now" on what the brain correctly
  counts as the first ask there: 10 of 6 runs before, 11 after. The brain's
  count is right; the model carries the phrasing across the boundary in the
  bridge. Fixing it needs its own mechanism and would have been confounded with
  this.
- **The stale `_open_terms` at a boundary**, which is the prerequisite for this
  rule to fire at all. Clearing it would mean no term block on the first turn of
  a topic, which is a behaviour change worth measuring on its own rather than
  bundling here.
- Mechanism questions fell 5 points on the competent candidate. Small, one
  scenario, three runs, and in the direction the term block exists to protect.

---

## 2026-08-12 — The report stops blaming the candidate for the interviewer repeating itself

**What**

A report was describing a candidate as repetitive when the interviewer had asked
the same question four times and the candidate had correctly given the same
answer each time. And a dealbreaker — "describes evaluation or deployment in
vague or generic terms with no real examples" — was firing against somebody
holding 97.08 percent on 308 held-out examples, GPTQ 4-bit at 14.2 GB and KL
divergence, because none of it was ever asked for.

Both are the same failure: **the report made claims about a real person that
were artefacts of how they were interviewed.**

`QuestioningHealth` is the third object of a shape this codebase already has
twice. `ConversationHealth` stops a bad microphone reading as an incoherent
person. `JudgmentHealth` stops our own analysis failing from reading as a quiet
candidate. This stops the interviewer repeating itself from reading as a
candidate who repeats themselves.

**The signal, and why this one**

Questions that ask again what an earlier question in the same topic already
asked. Derived from the stored transcript like the other two, so an interview
already in the database picks it up next time its report is rebuilt.

Scoped to a topic on purpose: the same question in two topics can be two
questions, which is what the section-aware term block is built for. Twice inside
one topic is not.

The threshold is measured rather than chosen, the way `ECHO_RUN_FRACTION` was.
Counting question pairs above each candidate line across nine stored runs:

| word overlap | competent, 135 turns | deflecting, 252 turns |
| --- | --- | --- |
| 0.4 | 22 | 68 |
| 0.5 | 21 | 33 |
| **0.6** | **17** | **14** |

At 0.4 it fires on a quarter of the deflecting interview, where the interviewer
is working correctly and the candidate is the one going in circles. At 0.6 it
separates: 12.6% of turns on the runs whose reports called the candidate
repetitive, 5.6% on the runs that did not. **A follow-up shares the subject;
asking again shares the question.**

It reaches the scorer exactly as the channel does — a sentence at the top of the
prompt when degraded, nothing when not — and caps a confident recommendation the
same way, checked separately from the other two so no degradation can hide
another.

**And a fourth "not the candidate's fault" rule in the scoring prompt**, next to
declining, could-not-be-heard and ran-short. It forbids describing the candidate
as repetitive, and forbids raising a red flag that turns on something being
absent. Not being asked is not the same as failing to answer.

**Measured**

Nine stored runs scored twice, six competent and three deflecting.

**The attribution flipped, which was the point.** Before, on the competent
candidate: *"most answers repeated the same high-level description"*, *"the
majority of answers repeated the same phrase"*. After:

> **The interviewer repeated the same question multiple times**, which limited
> the candidate's ability to elaborate.
> Most answers were repeated verbatim **due to interviewer repetition**.
> **The repetitive questioning** limited the opportunity for the candidate to
> evidence this competency.

**The deflecting candidate did not go quiet**, which was the thing to protect:

> The candidate responded to nearly every question with the same phrase about
> concurrency control and session management, without elaboration or variation.
> The candidate did not provide any metric, experiment, or measurement, only
> repeating the same phrase.

| | before | after |
| --- | --- | --- |
| the vague-and-generic dealbreaker, competent | fired 3 of 6 | **1 of 6** |
| the equivalent dealbreaker, deflecting | fired 3 of 3 | **3 of 3** |
| sentences blaming the candidate for repetition, competent | 3 | **2** |
| sentences naming the interviewer instead | 1 | **3** |
| competencies scored | 35 | 40 |
| quotes verified | 75 | 71 |
| quotes dropped as unverifiable | 0 | 0 |
| scores downgraded for lost evidence | 0 | 0 |

**It is a reduction, not an elimination, and the numbers say so.** The wrong flag
still fired once in six. Two sentences still describe the answers as repetitive
without naming why, one of them in the same report that elsewhere names the
interviewer correctly.

**One deflecting run moved and I cannot attribute it.** It went from six
competencies marked `insufficient_signal` to six scored at 2.0 — which on this
rubric is "generic or textbook answers, no concrete experience behind them", and
the rationales are unchanged in substance ("did not provide any concrete
examples", "did not describe"). The red flag still fires and the recommendation
is still capped. The scorer runs at temperature 0.2 and the other two deflecting
runs are identical across both passes, so this sits inside sampling.

**How**

- `backend/shared/contracts.py` — `QuestioningHealth`, and
  `FeedbackReport.questioning_health`.
- `backend/feedback/health.py` — `assess_questioning`, pure, transcript only.
- `backend/feedback/score.py` — the prompt rule, the prompt injection, the cap.
- `backend/feedback/run.py` — derived alongside the other two.
- `backend/tests/test_health.py` — four tests: repetition is caught, a genuine
  follow-up is not, the same question in two topics is two questions, and the
  cap fires.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 686 passed, 22 skipped

**Not done**

- The report still has nowhere to say a dealbreaker was **checked and not
  observed**. `red_flags_observed` holds only what fired, so "we asked about
  negative results and they answered well" is unsayable, and two of this
  employer's four dealbreakers went unaddressed in either direction.
- Nothing here makes the interviewer ask better questions. It stops the report
  blaming the candidate for the questions it asked.
- The residual cases above.

---

## 2026-08-12 — The term allowance and the term record are two fields now, and the record still does not fill

**What**

How many times a topic may ask about a word, and what the candidate was asked
about and could not explain, were one field. They answer different questions and
they are now two.

  ALLOWANCE  per topic. `Section.term_raises` and `Section.closed_terms`,
             unchanged. A fresh topic starts clean, which is what re-enabled a
             communication topic asking how they explained something a technical
             topic asked the workings of.

  RECORD     turns on having been ASKED, not on the allowance being spent.
             Written into the topic it happened in; a word asked about in two
             topics and explained in neither appears against both, which is the
             truth a reader wants.

  EXPLAINED  interview-wide. Struck from every topic's record, because "could
             they explain this" is a fact about a person and an answer given two
             topics late is still an answer.

**The three decisions**

*Asked once here and once two topics later, never explained.* Both topics carry
it. Neither spent its allowance, so under the old rule it reached no report at
all — that was the regression this is for.

*Asked, unexplained, then explained later.* It goes. The report says what
somebody could and could not do, and leaving "asked about X and could not
explain it" against a person who explained it is a written claim that is false
by the end.

*The let-go.* Unchanged, still on the allowance being spent within a topic. It
is the sentence said out loud when this topic has finished asking.

**The explained signal was wrong first, and measurably**

The first version struck any term that left the judge's list. The judge answers
"what is unexplained in what I can see now", so a term leaves that list when the
conversation moves on at least as often as when somebody explains it: it struck
two of the three terms one run recorded. The window is now the single verdict
that read the answer to the question about that term. Three strikes across six
runs, all of them terms this candidate does explain.

**Measured, and it did not do what it was for**

| terms reaching the report | before | after | target |
| --- | --- | --- | --- |
| competent | 2, 2, 2 | **0, 1, 2** | 5, 5, 4 |
| deflecting | 1, 1, 2 | **2, 2, 2** | 3, 3, 4 |

**Only eight distinct terms are ever asked about across six runs.** The record
can only record what was asked, and the interview asks about the same word in
every topic: one run put `prompt-level rules` to the candidate twice in each of
six sections. That is the per-topic allowance from the previous entry doing
exactly what was predicted of it, and it is upstream of anything here.

**The competent number falling is probably right.** That candidate explains
things, so a report listing five terms they could not explain was overstating
it — the 5, 5, 4 came from a state that recorded terms after they had been
explained. The deflecting number rising is the fix working on the candidate it
matters for: OLTP, session management and concurrency control session now reach
the report in every run, where one run previously recorded OLTP alone.

Other numbers, competent: material reached 40% to 43%, term machinery 45% to
38%, consecutive questions on one subject 21% to 18%, median turn 16.5 to 18.5
words, one unsupported attribution.

**Drift did not move, as expected.** Competent 17% to 26%, deflecting 25% to
24%. The 17% in the previous entry was noise around a measure that is fooled by
the scripted candidate repeating an earlier answer; 26% is where it has sat for
three entries. Nothing here was aimed at it.

**How**

- `backend/bot/brain/brain.py` — the record gates on `term_raises >= 1`;
  `_explained_terms` and `_asked_since_judged`; `_let_go_said` so the let-go
  stops inferring itself from the report list now the two have come apart.
- `backend/bot/brain/state.py` — unchanged this time.
- `backend/scripts/compare_models.py` — reads `brain.terms_asked_about()`, a new
  public accessor. It read `brain._term_raises`, broke when that moved onto the
  Section, was repointed at `section.term_raises`, and would have broken again
  today. What it wants is the question, not whichever field answers it.
- `backend/tests/test_brain.py` — five tests, and each was checked by breaking
  the code both ways: gating the record on the allowance again fails two of
  them, and letting the allowance run across topics fails two.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 682 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent deflecting \
        --models gpt-4.1-mini --runs 3

**Not done**

- The report does not fill because the interview asks about almost nothing: 8
  distinct terms in 6 runs. Until the same word stops taking every topic's
  allowance, splitting the record from the allowance cannot show much.
- Drift, untouched and unmoved. The loop is the judge reporting from the last
  answer, the candidate repeating an earlier answer, and the depth ramp pointing
  at whatever was just said.
- Distinct subjects per topic was not re-read for this run. It has read 4.0 to
  4.3 across the last three changes and nothing here targets it; the reading is
  an hour of transcript work and would not have been informative.

---

## 2026-08-12 — Term state is per section now, and it did not fix the drift

**What**

How many times a term has been asked about, and whether it has had its
allowance, are now facts about a section rather than about the interview. Each
section starts with a clean slate.

**Why**

One bug with two faces, written down two weeks before it was fixed. A term that
spent its two asks in the first competency was closed for the whole interview,
so a later section could not ask about it even where that was the right
question. And a term first raised in the first competency stayed live to the
last, which is what filled later sections with earlier sections' material: in
one measured run the sixth topic asked six questions and every one was about the
first topic's.

`term_probes_spent` was already scoped this way. This follows it.

**A term legitimately belongs to two sections and that is now possible again.**
The section-aware branch exists so a technical topic asks what a word means and
a communication topic asks how they explained it — a different question about
the same word, built deliberately. Brain-level closure suppressed the second one
whenever the first had used its allowance. Per-section closure restores it.

**What happens to a term explained in an earlier section.** The judge is asked
per section and sees only that section's transcript, so it has no memory that
section one explained something. If the candidate uses the word again in section
five without explaining it there, the judge reports it and the interviewer asks.
**Nothing stops that, and nothing did before either** — brain-level closure only
blocked it once the term had been asked twice, which is a different condition.
Whether it needs stopping is left open: a word used unexplained in a new context
is a fair question, and the case that was measured as a bug was inside one
section, which the judge already handles.

**Measured, and the result is negative**

Three runs each, competent and deflecting, `gpt-4.1-mini`, at 22 seconds.

| competent | before | after |
| --- | --- | --- |
| material reached in its own section | 12/30 = 40% | **14/30 = 47%** |
| distinct subjects per section, read | 4.3 | **4.2** |
| questions quoting an earlier section | 26% | **17%** |
| **terms reaching the report** | **5, 5, 4** | **2, 2, 2** |
| term machinery | 45% | 37% |
| consecutive questions on one subject | 21% | 26% |
| unsupported attributions | 1 | **0** |
| median interviewer turn | 16.5, 17, 17.5 | 18, 18, 17 |

| deflecting | before | after |
| --- | --- | --- |
| questions quoting an earlier section | 18% | **25%** |
| terms reaching the report | 3, 3, 4 | **1, 1, 2** |
| consecutive questions on one subject | 30% | 36% |
| unsupported attributions | 1 | **3** |

**The drift number moved and the drift did not.** Reading the transcripts, the
same pattern is there in a new form: one term now restarts its allowance in
every section and dominates several in a row. In one run `prompt-level safety
rules` opened four consecutive sections; in another `systemd` ran through five;
in a third `LiveKit Agents` ran through four. Before, a term stayed live because
it was never closed. Now it is closed and re-opened at every boundary, and the
transcripts look the same.

**And the measure is fooled here, which is worth writing down.** "Quoting an
earlier section" asks whether the candidate said the term in this section. The
scripted candidate answers a cross-section question with the same answer, which
puts the term into this section's turns, so the next question about it counts as
local. The 26 to 17 improvement is substantially that artefact. Distinct
subjects per section, which is read rather than counted, did not move: 4.3 to
4.2.

**Terms reaching the report dropped, and that is a real cost, not a wash.**
Two asks no longer accumulate across sections, so a term asked once in each of
three sections is never recorded as asked-and-unanswered anywhere. Deflection is
still caught — OLTP and concurrency control still reach the report in every
deflecting run — but the record is thinner: run 2 records `OLTP` alone where it
previously recorded `OLAP` and `concurrency control session` as well. The
product output says less about what the candidate could not explain.

**Unsupported attributions on the deflecting candidate went from 1 to 3.** Not
dismissed and not attributed: three runs at temperature 0.6, and this measure has
read 0, 1 and 2 across the last four comparisons without any change aimed at it.

**How**

- `backend/bot/brain/state.py` — `Section.term_raises` and
  `Section.closed_terms`.
- `backend/bot/brain/brain.py` — the brain-level dict and set are gone; every
  read and write goes through the current section.
- `backend/tests/test_brain.py` — a test asserting both directions: the cap
  still binds inside a section, and the next section starts the same term from
  nothing. **No existing test failed**, which is how this survived two weeks:
  nothing covered cross-section term behaviour at all.
- `backend/scripts/compare_models.py` — one read followed the state. The
  specific-terms signal reached into `brain._term_raises`, which no longer
  exists, and the comparison crashed on the first run until it was pointed at
  the sections instead.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 678 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent deflecting \
        --models gpt-4.1-mini --runs 3

**Not done**

- **The drift is not fixed and the cause is elsewhere.** Six questions in the
  voice topic about section-one material was never term tracking alone. The
  judge reports terms from whatever the candidate just said, the candidate
  repeats earlier answers when asked earlier questions, and the depth ramp
  points at whatever is in the last answer. That loop is what carries a topic,
  and none of it is term state.
- Nothing prevents a term explained in one section being asked about in another.
  See above; whether it should is an open question rather than an oversight.
- The report getting thinner is a genuine regression and this change does not
  address it. Recording a term as asked-and-unanswered when it has been asked
  once in each of three sections would need the report to count across sections
  while the allowance stays within one.

---

## 2026-08-12 — Two fixture faults that made the interviewer look worse than it is

**What**

Two things in the harness, neither in production. A gap the interview could
never find, and a turn cap that stopped one scenario finishing.

**Why, and what came of it**

**The gap.** This candidate has one genuine hole: they cannot describe Docker,
version control or a rollback. That hole was gated behind trigger words, and
across 133 real interviewer questions not one contained any of them, so the two
answers had never fired in any run at any setting. The topic read as the worst
in the table for a reason that was the fixture's.

The rule written into this file is that a trigger names what an answer is
ABOUT. **"I don't know" is about nothing** — its subject lives entirely in the
question — so gating it on keywords is that rule upside down. It is now the
topic's declared fallback: what this person says when a question here names
nothing they have. Two near-identical answers collapse to one, so every topic
now holds five.

It stays genuinely unanswerable. What changed is that the interviewer can reach
the gap, not that the candidate found an answer.

**And it changed nothing, which is the finding.** Verified directly: a Docker
question and a version-control question both reach it now. Across three fresh
runs it **fired zero times**, because the interviewer still never asks either,
though the blueprint has a Docker seed question sitting in that topic. The
fixture was hiding an interviewer failure, not causing one.

**The cap.** `max_turns` was a flat 60, chosen when a turn cost 40 seconds. At
the measured 22 a full `pm_senior` interview needs 84, so every deflecting run
for two tasks ended on the cap instead of finishing. Its topics could not get
longer, its numbers sat still by construction, and **the scenario that guards
deflection being caught had measured nothing for two tasks.**

Derived now instead of guessed: the sum of the brain's own section ceilings,
plus `MAX_REPAIR_ATTEMPTS` per section for turns that deliberately do not spend
one. That is 61 for `ml_senior` against a 45-turn interview and 100 for
`pm_senior` against 84 — loose enough never to bind, tight enough that a brain
looping on one topic still trips it. Production has no such cap and still does
not.

**Three trigger collisions, two of them mine from yesterday.** The audit that
catches triggers matching what the interviewer says on its own account had not
been re-run after the depth ramp gained new wording. It now fires on
`trade-off` and `cost`, because the ramp says "ask for the trade-off they made,
and what it cost" in so many words, and on `control`, which sits inside "version
control" and "concurrency control". All removed. **This makes that topic harder
to hit, not easier.**

**Measured**

Three runs each, competent and deflecting, `gpt-4.1-mini`, at 22 seconds.

**The deflecting scenario finishes for the first time in three tasks**: 84
turns, `brain_finished`, in all three runs, against 60 and `max_turns` before.

| competent | before | after |
| --- | --- | --- |
| material reached, like-for-like against 30 answers | 15/30 = **50%** | 12/30 = **40%** |
| distinct subjects per topic, read | 4.0 | **4.3** |
| consecutive questions on one subject | 25% | **21%** |
| **questions quoting an earlier topic** | **15%** | **26%** |
| terms reaching the report | 4, 4, 4 | **5, 5, 4** |
| term machinery | 47% | 45% |
| unsupported attributions | 0 | 1 |
| median interviewer turn | 16.5, 17.5, 19 | 16.5, 17, 17.5 |

| deflecting | before | after |
| --- | --- | --- |
| how it ended | `max_turns` at 60 | **`brain_finished` at 84** |
| competency turns | 171 | 237 |
| terms reaching the report | 3, 2, 3 | **3, 3, 4** |
| consecutive questions on one subject | 36% | **30%** |
| term machinery | 21% | 19% |

**Material reached went down, not up, and that is reported as it came out.**
The fixture was not holding the number down. Sections five and six were taken
over by terms from sections one and two — in one run the voice topic asked six
questions and every one was about bf16 LoRA, the jinja template or GPTQ. Cross-
topic drift rose from 15 to 26 percent of quoted questions, and that is what
moved it.

**I cannot attribute that rise to these changes.** Three runs, five answers a
topic, so one answer is twenty points of a topic's score. What can be said is
that the gap answer fired zero times, so it made nothing easier, and the trigger
removals make a topic harder to reach rather than easier.

**The honest figure for how much of a good candidate's material the interviewer
reaches is 40 to 50 percent**, and the spread between those two numbers is
run-to-run variance on the same code. It was reported as "about a third" while
the instrument was charging double for a turn; that part was real and is fixed.
Claiming better than "40 to 50" from three runs would be reading noise.

**How**

- `backend/bot/brain/harness.py` — the gap becomes the topic's declared
  fallback; `trade-off`, `tradeoff`, `cost`, `control`, `bet behind` and
  `why not` removed as triggers.
- `backend/scripts/compare_models.py` — `_runaway_bound()` derives `max_turns`
  from the blueprint.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 677 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent deflecting \
        --models gpt-4.1-mini --runs 3

**Not done**

- The blueprint has a Docker seed question in that topic and the interviewer has
  never asked it. That is the seed-coverage problem, not a fixture one, and it
  is now the only thing standing between the interview and the one gap this
  candidate has.
- Cross-topic drift at 26 percent is the largest thing in these numbers and
  nothing here addresses it.
- The trigger audit needs running whenever prompt wording changes. It was not
  run after the depth-ramp change and two collisions went in.

---

## 2026-08-12 — The measuring instrument was charging twice what a turn costs

**What**

The harness charged 40 seconds for every exchange. The measured figure is 22,
recorded in DECISIONS.md and used by the brain itself to work out how many
questions a topic can hold. Nothing connected the two, so they drifted.

The harness now takes the number from the brain's own constant, so they cannot
drift again.

**Nothing in production changed.** This is the thing doing the measuring.

**Why it mattered more than it sounds**

The brain sizes a topic at `budget / 22`. At 40 the harness ran the clock out
before any of those ceilings could bind, so **a three-minute topic got five
questions where production gives eight, and a two-and-a-half-minute one got four
where production gives six.** Every coverage number taken here for a week was
measured on topics at roughly 60 percent of their real length. One extra
question had already been measured as nearly doubling what a topic reaches.

**Measured**

Three runs each, competent and deflecting, `gpt-4.1-mini`, at 40 and at 22.

| | at 40s | at 22s |
| --- | --- | --- |
| **material reached, in its own topic** | 9/31 = **29%** | 15/31 = **48%** |
| **distinct subjects per topic, read from the transcripts** | **2.9** | **4.0** |
| questions per topic | 5, 5, 4, 4, 4, 4 | **8, 8, 6, 5.3, 6, 6** |
| what ended each topic | budget, every one | **the ceiling, every one** |
| terms the candidate could not explain, reaching the report | 2, 3, 2 | **4, 4, 4** |
| consecutive questions on one subject, competent | 27% | **25%** |
| unsupported attributions | 2 and 1 | **0 and 0** |
| median interviewer turn | 15.5, 17, 17 | 16.5, 17.5, 19 |
| term machinery, competent | 27/78 = 35% | 56/118 = **47%** |

**"The interviewer reaches about a third of what a good candidate has" was
substantially the instrument.** It reaches about half. The claim has driven three
days of work and it was measured on topics missing a third of their questions.

It is not entirely the instrument, and the remaining half is real. But the
priority list changes: the gap is smaller than reported, and the thing that
moved it was giving topics the length they are supposed to have, not any change
to how the interviewer asks.

**The turn ceiling fired for the first time.** It has existed since the
proportional-ceiling change and had never once been the operative bound —
budget always ran out first. At 22 it binds in every topic, and it binds
exactly where it was designed to: 8 questions for a three-minute topic, 6 for a
two-and-a-half-minute one.

**Two things went the wrong way and are not dismissed.** Term machinery rose
from 35 to 47 percent of questions, because more questions means more terms
nominated and more probes: 34 first-asks against 14. And on the deflecting
candidate, consecutive questions on one subject rose from 27 to 36 percent.

**The deflecting comparison is not clean and should not be read as one.** Both
its run sets hit the harness's global `max_turns=60` cap rather than finishing,
at 40 and at 22 alike, so its topic lengths could not change and its numbers
sit still by construction. Only the competent comparison measures what this
change did.

**How**

- `backend/scripts/compare_models.py` — `seconds_per_turn` now comes from
  `SECONDS_PER_COMPETENCY_TURN` rather than a literal 40.
- **`--reverse-sections` added.** Runs the competencies in reverse order and
  nothing else: every plan keeps its budget, weight, seeds and target depth, and
  only its slot moves. It exists because `ml_senior`'s weights descend
  monotonically with position, so "later topics reach less" and "lower-weighted
  topics reach less" are the same observation seen twice and no single ordering
  can separate them. Run as a one-off it overturned a finding four
  investigations had been built on. Output is written with a `__reversed`
  suffix so it cannot be mistaken for a normal run.

**A flat constant is still the wrong shape.** This only makes it the right flat
constant. A real exchange takes as long as the candidate speaks for, and this
scripted candidate's answers run from 26 to 46 words, so a topic of short
answers is charged the same as a topic of long ones. Charging by words actually
spoken would be closer. Not built here: an instrument change and a fixture
change in the same run cannot be told apart.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 677 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent deflecting \
        --models gpt-4.1-mini --runs 3
    uv run python scripts/compare_models.py --scenarios competent \
        --models gpt-4.1-mini --runs 3 --reverse-sections

**Not done**

- **Both fixture problems recorded last time still stand, and one still
  distorts.** `ml_service_deployment` gates two of its six answers behind
  docker, containers, version control and rollback: across 133 interviewer
  questions at 22 seconds, **not one mentioned any of those words**, and neither
  answer has ever fired. Its real ceiling is 4 of 6, so its 33% is 50% of what
  is reachable. `client_communication`, the other one, stopped mattering: it
  went from 1 of 5 to 3 of 5 once there were questions to spare, so its sparse
  triggers were only binding because the topic was short.
- The deflecting scenario needs `max_turns` raised before it can be compared at
  all. Left alone here so this change stays attributable.

---

## 2026-08-12 — The interviewer stops asking the same question every turn

**What**

Two parts. Yesterday's term-cap change is reverted, and the thing that was
actually consuming the topic is fixed.

**The cap goes back to two asks.** Cutting it to one did not move what it was
aimed at: distinct subjects per topic read 2.5 before and 2.5 after. The turns
it freed went straight to the depth ramp, which asked about the same term again
unprompted. And on a deflecting candidate it cost more, not less, because one
ask per term means a topic's allowance buys two different terms rather than two
attempts at one, each with its own let-go: 19 percent of turns became 29.

**Two things from that change are kept, because both are right at any cap.** The
harness trigger fix, and the let-go moving into `apply_judgment` — queueing that
sentence at the moment the question is asked said "let's leave that one there"
on the strength of having asked rather than on the strength of the answer, and
only survived at two asks because the second ask happened to give the judge a
turn.

**Then the real one.** The depth ramp's third stage rendered identically on
every turn from the third onward, and its first instruction is that the
technique in their last answer is the question. So it unpacked a term, then read
the same instruction against an answer about the same thing and unpacked it
again. One measured topic asked about LoRA five times running with a single one
of those sanctioned by the brain.

That stage now fires its unpacking instruction **once**, on the third turn, and
from the fourth names **one** demanding thing to ask for, rotated by the turn
index: a number and how they measured it, then the trade-off and what it cost,
then what went wrong, then a decision they would still defend.

**Why one named rather than four offered.** It used to end with "pick ONE
instead: the trade-off, the thing that went wrong, a decision they would defend,
or a number". That is a preference between available options, which is the shape
this repo has measured at 0 percent compliance against 80 percent for naming the
thing to say. Offering four also lets the model pick the same one twice, which
is the failure being fixed wearing a different hat. Rotating makes consecutive
turns different by construction rather than by hope.

**This removes unsanctioned repeats, not unpacking.** The term block is
untouched and still asks about a term the candidate has not explained, on the
brain's own two-per-topic allowance. What is gone is the ramp asking a fourth
and fifth time off its own bat.

**Measured**

Three runs each on the competent and deflecting candidates, `gpt-4.1-mini`.

**The number that has read 2.5 through every change this week moved.**

| | before | after |
| --- | --- | --- |
| **distinct subjects per topic, read from the transcripts** | **2.5** (45 across 18) | **2.9** (53 across 18) |
| consecutive turns on the same subject, competent | 17/59 = 29% | 16/60 = **27%** |
| consecutive turns on the same subject, deflecting | 80/153 = **52%** | 41/153 = **27%** |
| term machinery, competent | 38/77 = 49% | 27/78 = **35%** |
| term machinery, deflecting | 32/171 = 19% | 36/171 = **21%** |
| questions asking for a metric, trade-off or failure | 0, 3, 2 of 26 | **8, 6, 6** of 26 |
| questions unpacking a term | 8, 13, 7 of 26 | 5, 5, 4 of 26 |
| median interviewer turn | 16.5, 18.5, 16 words | 16, 17, 17 |

The consecutive-subject count is new. Nothing counted the failure directly
before, which is why it survived four investigations: two consecutive questions
in a topic count as the same subject when they share three or more content
words, applied identically on both sides.

**It is a modest result and the competent candidate is where it is weakest.**
Same-subject runs there went 29 percent to 27, and the longest single streak got
slightly worse in two of three runs. What moved on that candidate is the
composition: unpack-shaped questions nearly halved and metric-and-trade-off
questions went from almost none to a quarter of all turns. On the deflecting
candidate the repetition halved outright.

**The good questions survived.** The comparison was the evaluation topic in run
3, which previously reached three answers with real depth. It now reaches four:
*"How did you measure decision accuracy for that health-tech classifier?"* then
*"Walk me through how your decision accuracy metric actually works"* then *"How
did you validate that your held-out set matched production?"* then *"How many
examples were in your evaluation set?"* — and the rotated demand is visibly
producing them elsewhere too, *"How did you measure and confirm that difference
was within noise on those 308 examples?"*

Nothing on the guard list moved. Deflection is still caught, with OLTP, OLAP,
session management and concurrency control still reaching the report in every
run. The let-go is still spoken and still fires on the judgement. The
communication topic still asks about communicating, with zero mechanism
questions in all three runs. **No fabricated attributions**: three flags, all
three artefacts of the checker rather than the interviewer — twice the stem
"quantise" against a candidate who said "quantised", and once a hypothetical
("when you said no to a senior") parsed as a quotation. The one genuine
fabrication from yesterday did not recur.

**How**

- `backend/bot/brain/brain.py` — `MAX_TERM_ATTEMPTS` back to 2. The let-go stays
  in `apply_judgment`.
- `backend/bot/brain/prompting.py` — the escalated wordings restored; the depth
  ramp's third stage split at `turns_spent == 2`, and `_HARD_QUESTIONS` added.
  The new stage is shorter than the one it follows, so the prompt bound is
  unaffected and its worst case is unchanged.
- `backend/bot/brain/harness.py` — unchanged from yesterday; the trigger fix
  stands.
- `backend/tests/test_brain.py` — restored to its committed state apart from one
  test, which now applies a judgement after the second ask because that is where
  the report entry is written.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 677 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent deflecting \
        --models gpt-4.1-mini --runs 3

**Not done**

- The rotation is not section-aware. "A number and how they measured it" is a
  weaker ask in a communication topic than a technical one, and the ramp is not
  given the section. Left alone because the section-aware branch that already
  exists is in the term block, and widening this change would have made the
  result unattributable.
- Rough seed overlap fell, 4, 4, 9 to 3, 4, 5. It is a diagnostic and not a
  target, for the reasons recorded two entries below, but it is recorded here
  rather than left out.
- One competent run spoke no let-go at all where the previous baseline spoke two.
  Five against seven across the three runs, small numbers either way.
- **The first measurement of this was thrown away.** The run crashed partway
  through on an API timeout, and the transcripts left on disk were the previous
  day's. They were measured before the crash was noticed. Clear the output
  directory before a comparison run, so a partial run cannot be read as a
  complete one.

---

## 2026-08-12 — A term costs one question instead of three, and it did not buy what it was meant to

**What**

Two changes. A bug in the test harness, and a change to how long the interviewer
spends on one word.

**The harness bug first, because it was making the interviewer look worse than
it is.** The scripted candidate picks its answer by matching the question
against what each answer is about. One answer carried the trigger words
"interviewer", "working on" and "building" — and the interviewer's own greeting
is "I'm Mitra, an AI interviewer", while the opening question the prompt models
is "tell me what you're working on at the moment". So the greeting selected an
answer about the candidate's own product, the opening's real answer was
unreachable in every run, and it was counted against the interviewer as material
it had failed to reach.

**Then the change that matters.** Asking about a term used to cost three turns:
ask, ask again more pointedly, then say out loud that we are leaving it. In a
topic that gets four or five questions that is most of the topic. It now costs
one ask, and if the answer did not explain it, the next thing said is that we
are leaving it there.

**Why**

Measured on a candidate who answers well: term machinery took 38 of 77
questions. The single best stretch of questioning in any run was the topic where
no term was nominated until the fifth turn; the worst spent five turns on what
"bf16 baseline" means, to somebody holding a decision-accuracy number, a
significance argument and a named divergence metric.

**The result did not follow, and it is recorded here as a result rather than an
improvement.**

| | before | after |
| --- | --- | --- |
| term machinery, competent candidate | 38/77 = **49%** | 30/78 = **38%** |
| term machinery, deflecting candidate | 32/171 = **19%** | 50/171 = **29%** |
| distinct subjects per topic, read from the transcripts | **2.5** | **2.5** |

The escalation turns went away, nine of them. **The freed turns did not become
new subjects.** They went to asking about the same term again unprompted, which
the brain no longer sanctions and the depth ramp still asks for: it says the
technique in their last answer is your question, and it says it again on every
turn after the second. One topic asked about LoRA five times running with only
the first ask sanctioned.

**And on a deflecting candidate it costs more, not less.** One ask per term means
the topic's allowance now buys two different terms rather than two attempts at
one, and each of those two carries its own let-go. Four turns where there were
three.

**How**

- `backend/bot/brain/brain.py` — `MAX_TERM_ATTEMPTS` 2 → 1. The term closes the
  moment it has been asked about once.
- **The let-go moved to where the judge answers.** It used to be queued at the
  moment the question was asked, which was safe at two asks because the second
  ask gave the judge a turn to rule on the first answer. At one ask that would
  say "let's leave that one there" to somebody who had just explained the thing
  properly. It is now queued in `apply_judgment`, the first point at which we
  know whether the answer explained anything. Explained, and nothing is said and
  nothing reaches the report; still unexplained, and the sentence is queued and
  the term is written down as asked-and-unanswered.
- `backend/bot/brain/prompting.py` — the escalated wording is gone from both
  branches, and the let-go no longer claims two asks.
- `backend/bot/brain/harness.py` — three trigger words removed.
- `backend/tests/test_brain.py` — twelve tests rewritten to assert the new
  behaviour rather than relaxed. Two changed what they protect rather than
  losing it: the test that checked the escalation had a real ask behind it now
  checks the same thing about the let-go, which makes the same kind of claim
  about a person; the test that a term offered three times and asked about once
  stays open now asserts it for a term offered three times and never asked.

**Measured**

Three runs each on the competent and deflecting candidates, `gpt-4.1-mini`, with
the harness fix in place on both sides so the comparison is honest.

**Deflection is still caught.** The terms a candidate could not explain still
reach the report in every run: OLTP, OLAP, session management and concurrency
control, the same set as before. One ask was enough to establish it, because
what establishes it is the judge still reporting the term afterwards, not the
number of times it was asked.

**The let-go is spoken more, not less.** Competent 2, 1, 4 → 3, 1, 4. Deflecting
3, 3, 4 → 9, 5, 5. The queue-on-judgement path fires more reliably than the
queue-on-second-ask path it replaced.

Nothing else moved: the communication topic still asks about communicating
(3, 4, 4 against zero mechanism questions), and the median question is 18 to 19
words against a twenty-word rule.

**One thing did move the wrong way and it is not dismissed.** A fabricated
attribution appeared in one deflecting run: *"You mentioned handling
disagreements with your engineering lead"*, said to a candidate who never
mentioned an engineering lead or any disagreement. There were none in the runs
before this change. Three runs at temperature 0.6 cannot establish that this
change caused it, and the mechanism is not obvious, but there is a plausible
route: a let-go turn has to invent its own next question with no term to anchor
on, and there are now roughly twice as many of them. It needs watching whatever
happens to this change.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 677 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent deflecting \
        --models gpt-4.1-mini --runs 3

**Not done**

- The depth ramp still renders its final stage identically on every turn after
  the second, and that is where the freed turns went. Deliberately untouched, so
  this change could be attributed; it is the obvious next thing.
- The let-go's cost is overstated by the measure above. Its own block tells the
  interviewer to say the sentence and then ask its next question, and the
  transcripts show it doing exactly that, so a let-go turn is not a wasted turn.
  Counting only the turns whose whole content is the mandatory term question:
  competent 40% → 29%, deflecting 13% → 18%. Same shape, smaller numbers.
- Whether this change is worth keeping is a judgement for the person reading it.
  It does what it was designed to do on one candidate, the opposite on the
  other, and moves the thing it was aimed at — how much of a good candidate the
  interview reaches — not at all.

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

## 2026-08-11 — A scripted candidate who is good at the job, and a baseline against them

**What**

Every scripted candidate this repo has is a bad candidate. Vague, deflecting, or
jargon with nothing behind it, and every one of them uses an unexplained term in
every single answer. So the interviewer's own questions and the "ask about that
term" instruction never had to compete for a turn, and two changes made this week
were measured against conversations that could not detect what they changed.

There is now a candidate who answers well: ordinary vocabulary of the field
("LLM", "fine tuning", "agent"), real specifics next to it (Sarvam-M 24B, GPTQ
4-bit at 14.2 GB, 97.08 percent on 308 held-out examples, KL divergence), and a
few honest "I don't know"s where the real person had them. Every answer is taken
from a real interview rather than invented.

Also here: a blueprint fixture that carries claims about a real person, and a
signal that was inverted has been replaced.

**No behaviour changed.** Nothing in the prompt, the brain or the judge was
touched. This is a candidate to measure against and the measurement itself.

**Why**

The case that matters most for a hiring tool is the candidate who is good, and
nothing tested it. It is also the only case where a wasted turn is visible:
asking a bad candidate what they mean is the right question, and asking a senior
engineer what an LLM is, is a turn thrown away. Both have to be possible from
the same script or the difference cannot be seen.

**The old specific-terms signal was inverted and is gone.** It counted a probed
term as specific when it shared a word with the seed questions or the CV claims.
Scored against a real interview it called "agent", "LLM" and "fine tuning"
specific and "KL divergence" generic, which is exactly backwards: the seed
questions are written in the field's vocabulary too, so overlapping with them is
evidence of nothing.

**What the new one does, and what it gets wrong.** Deciding whether a term names
one particular thing or a category is semantic. "LLM" and "ECAPA" are the same
length, the same shape and the same capitalisation, and only knowing what they
refer to tells them apart. Doing it properly needs a model call, and this signal
runs fifteen times per comparison, so it does not make one. The proxy is: a digit
or an internal capital. It misses named methods and single proper nouns, so "KL
divergence", "GPTQ", "systemd" and "Whisper-small" read as generic when a person
would call all four specific.

That error direction is chosen. A false "specific" hides the failure the signal
exists to catch, which is what the old one did. A false "generic" overstates the
waste and sends a reader to the transcript. **The probed terms are now printed
verbatim next to the count**, because reading four strings settles in five
seconds what the proxy cannot settle at all.

**How**

- `backend/bot/brain/harness.py` — `competent_candidate()`, section-keyed across
  all six competencies plus the opening. Thirty-two answers, five or six per
  section, because a section runs four or five turns and a bank of two means the
  rest of it is the fallback reply. The first baseline was run and thrown away
  for exactly that: three sections were mostly fallback, which measured the
  script rather than the interviewer.
- `backend/bot/fixtures/ml_senior.json` — new fixture, derived from the real
  blueprint the last interview ran against. **It carries six `claims_to_verify`;
  the two existing fixtures carry none**, which is why the specific-terms signal
  had nothing to score against and still printed a number.
- `backend/scripts/compare_models.py` — `competent` scenario; `SCENARIO_BLUEPRINT`
  so a scenario can name its own fixture (`run_one` previously always loaded the
  Senior PM one, and an ML engineer's answers against a PM blueprint would
  measure the off-topic redirect instead); `_specific_terms` rewritten.

One defect was found in the new signal by reading its first output: the brain
keys raise counts by `term.lower()`, so "LiveKit Agents" arrived as "livekit
agents" and the internal-capital rule could never fire. The casing is recovered
from the transcript in the script rather than by changing what the brain stores.

**Deliberately not done**

- No prompt, brain or judge change of any kind.
- The reordering from earlier today stays unmerged and unproven.
- Nothing was tuned to make the baseline read better.
- The proxy does not make a model call, and is stated as a proxy everywhere it
  appears.

**Confirm it**

    cd backend
    uv run pytest -q -m "not llm"          # 677 passed, 22 skipped
    uv run python scripts/compare_models.py --scenarios competent \
        --models gpt-4.1-mini --runs 3

**What the baseline says**

Three runs, gpt-4.1-mini. Seed questions asked: 7, 8 and 10 of 24. The
interviewer reached 4, 6 and 5 of the six CV claims. Median interviewer turn: 15,
20 and 16 words, and it is now doing about 31 percent of the talking rather than
the 48 percent measured on a real call. The communication section asked about
communicating 4, 3 and 2 times and about mechanism zero times, in every run.
Let-gos: 3 queued, **1 actually spoken**.

**The finding is the evaluation section, and it is the imitation effect.** It
asked 0 of its 4 seed questions in all three runs. All five of its turns, in all
three runs, are the same sentence: "You said LoRA. Walk me through how that
actually works", repeated with the term barely changing. **The brain sanctioned
none of them** — no term probe was queued in that section in any run — so this is
the model copying the shape of its own previous turn out of the verbatim window,
which is now the fourth separate problem that effect has caused.

The cost is exact and readable. That section exists to test how this person
evaluates a model, and the candidate is holding 97.08 percent on 308 held-out
examples, why accuracy rather than a language metric, and KL divergence for
comparing distributions. **None of the three was ever asked for.** The section
spent every turn asking what LoRA is.

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
