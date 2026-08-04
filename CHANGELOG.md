# Changelog

A running record of every change made to this codebase, newest first.

The point is that anyone can see what was touched and why without reading
diffs. Each entry says what changed in plain language, what problem it solved,
which files moved, what was deliberately left undone, and the exact commands to
confirm it works.

Write for someone who has never seen this repository. Keep jargon out of
**What** and **Why**; file names belong in **How**.

---

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
