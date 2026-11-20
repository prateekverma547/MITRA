## 2026-11-20: A more natural MITRA: varied openings, questions that follow the answer

**What**

Live tests: every interview opened the same way ("Good morning, I'm Mitra, an AI
interviewer, nice to meet you, how are you?", then "what are you working on
now?"), and questions followed the plan rather than the answers. Nothing was a
fixed string: the prompt prescribed each step, and the same instructions gave the
same words. Decided with the product owner: **same goals, conversational path**.

- **The opening, chosen in code per interview** (`bot/realtime_prompt.py`
  `choose_opening`, called once in `bot/run_realtime.py`):
  - the way in: their current role, their latest move, a piece of work their CV
    names, what drew them to this role, or their path so far;
  - the greeting's style (a simple hello, thanks for their time, glad to have them,
    "a conversation about your work, no rush"), worded by her;
  - whether she asks how they are (about half the time).
  The opener names one real detail from the CV summary ("I saw you joined
  Appinventiv last year"), and only what is written there. Her name and "an AI
  interviewer" are required in every first turn. Chosen once, so the cached part
  of the instructions stays identical between areas.
- **Questions follow the answer, on the same goals:**
  - a follow-up whenever an answer opens something worth exploring for the area
    (a decision, a trade-off, a number, a disagreement, what they did), not only
    when it is thin, in the candidate's own words;
  - the next goal is the one that connects to what they just said, not the next
    on the list, led into from their answer; a goal their answers already
    covered is skipped;
  - still three kinds of question and no fourth, at most 3 follow-ups per goal,
    every goal covered.
- **Time pressure is gentler** (`bot/realtime_coordinator.py`): "finish the thread
  you are on, with at most one more question" instead of "about one more
  question, then move on". The backstop that moves on when an area's time is used
  is unchanged, so interviews still end on time.

**Measured** (real `gpt-realtime-2.1`, a rich mid-interview answer, 5 trials each):

| | median time to first audio | replies |
| --- | --- | --- |
| new prompt, reasoning `minimal` | 1.36 s | all 5 built on the answer (the design head's disagreement) |
| new prompt, reasoning `low` | 1.50 s | the same, slightly more varied |
| old prompt, reasoning `minimal` | 1.71 s (one at 8.4 s) | 4 of 5 built on it; one stacked two questions |

Reasoning stays at `minimal`: `low` added about 0.14 s a reply for no difference
these trials could show. `REALTIME_REASONING_EFFORT=low` switches it if live
interviews show a need. Openings, 8 interviews on a real candidate's plan: every
one said her name and "an AI interviewer", and the openers differed ("What led you
to move to Appinventiv last year?", "What was it like working on document
processing at Appinventiv?", "What drew you to this role?").

**For Track I**

These are your files (`bot/realtime_prompt.py`, `bot/realtime_coordinator.py`,
`bot/run_realtime.py`), changed here at the product owner's decision. Expect merge
conflicts in them; the intent is above. Tests updated to the new wording:
`test_round18_fixes.py` (the opening note and the opening rule),
`test_one_voice.py` (the first turn and the area note); new
`test_natural_interview.py`.

**Confirm**

- Run three MITRA rounds: each opens differently, with something from the CV,
  and always says "I'm Mitra, an AI interviewer".
- Give a rich answer with a disagreement or a number in it: the next question
  should pick it up before moving to another goal.
- Backend: 2,179 passed.

**Update: reply latency, follow-ups, greeting**

The delay was measured before anything changed. Live MITRA rounds, 48 replies: the
model's own part was 0.71 s median; 14 of the 16 replies over 3 s were slow because
OpenAI's turn detection was still deciding whether the candidate had finished (up
to 9.2 s, with `semantic_vad` at eagerness `low`). Preparing the next question
while the candidate speaks was considered and not done: it targets the 0.7 s part,
cannot respond to the end of an answer, and would put a second model on the live
path.

- **Turn detection `low` to `medium`** (`bot/services/realtime.py`). Measured on
  spoken answers streamed in real time, 2 trials each: a complete answer was
  decided 1.3 to 1.6 s after the last word at both; a 3 s "um" pause mid-sentence
  ended the turn at neither; a trailing "and also..." pause ended it 0.4 s in at
  both (already so at `low`); the worst wait fell from 8.8 s to 5.0 s.
  `REALTIME_EAGERNESS=low` restores the old setting.
- **Follow-ups:** "usually one or two, never more than 3, then carry on with the
  plan's next goal", and only on what matters for the area.
- **Greeting:** the "a conversation about your work, and there is no rush" style
  is removed. "No rush at all, take your time" stays only as the answer to a
  nervous or stumbling candidate.
- **Not changed:** the five slowest model replies (about 2 s) were all the move
  into the first area: she calls `move_on`, waits for it, then speaks ("Let's
  begin..."). About 1.3 s, a few times an interview. A spoken transition before
  `move_on` would hide it, but Track I's rule is that she calls it silently; left
  for a decision.

Confirm by ear: two MITRA rounds, one with long, thoughtful answers. Replies after
a finished answer should come in about 2 s; she should not cut in during a pause.

**Update: Mitra leads; the candidate does not steer**

A live test: "I don't know, ask me about AWS" got AWS questions, "ask me about Google
Cloud" got Google Cloud, "we were talking about RAG" went back to RAG. The prompt
had rules for "I don't know" and for skips, none for a request to change topic, and
"pick the goal that connects best to what they said" was read as following requests.

- **YOU LEAD THE INTERVIEW** (prompt): a request to change topic is acknowledged
  first ("Thanks for letting me know.", "Noted, thank you."; a bare "okay" is not
  enough) and not followed: the plan's next question comes next, never a requested
  topic, never a finished question or area. A tool or experience mentioned inside an
  answer is still an answer and may be followed up within the goal.
- **"I don't know"**: fine, then the plan's next question in the area, never the
  same question again.
- **Two "I don't know" answers in a row in an area move the interview on**, in code
  (`bot/realtime_coordinator.py`, `said_dont_know`, `DONT_KNOW_LIMIT = 2`): asked in
  the prompt, she did it in 1 of 6 cases. If she has not started replying, her reply
  is replaced and she says it is fine and opens the next area; if she has, the next
  area starts at the next quiet moment. Not from the last competency area (the next
  would be the closing).

Measured, the same four moments of that interview replayed on the real model
(text, the candidate's real plan): followed a topic request in 2 of 6 replies before,
0 of 9 after; after the acknowledgement rule, all 9 acknowledged the request first.
