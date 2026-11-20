## 2026-11-20: MITRA bridges out of the opening, and says the interview is live proctored

**What**

Product owner's feedback after a live test. Nothing is hardcoded: MITRA is told what
each moment needs, never the words.

1. **No "let's begin" in the middle** (`bot/realtime_coordinator.py`). Leaving the
   opening, the move_on result told her to say "let's begin" (round 18, when the
   opening was a two-exchange warm-up). After a real conversation about their work,
   it sounded as if the system had restarted. The first move now asks for three
   parts: one short, curious sentence about something specific they just said;
   from it, in her own words, that the conversation now goes deeper into their work
   and the topics the role involves, naming the first topic; then the area's first
   question. Never worded as the interview beginning or starting. The backstop's
   move out of the opening (time used up) bridges the same way
   (`NOTE_BACKSTOP_FROM_OPENING`). No move result quotes any words now.
2. **"You are under live proctoring" in the introduction**
   (`bot/realtime_prompt.py`). The first turn now requires three things before
   anything is asked: her name, that she is an AI interviewer, and that the
   interview is live proctored, in one friendly sentence of her own: calmly, never
   as a warning, never listing what is checked. Then the hand-over line, and she
   waits.
3. The plan prompt's example questions are marked as examples of the level only:
   never copied or adapted (`blueprint/generate.py`).

**Measured** (real realtime model, text out, AI engineer plan):

- First turn, 5 of 5: name, "AI interviewer", live proctoring, then a hand-over
  question and nothing else ("This interview is live proctored. ... How are you?").
- Leaving the opening, before the change in wording: 2 of 6 skipped the bridge and
  every reaction was a bare "Okay."; after: 6 of 6 react to a detail they said ("I'm
  curious about that VM deployment."), bridge ("We'll go deeper into retrieval
  now.") and ask; 0 of 6 announce a start.

**Confirm**

- Run a MITRA round: Mitra introduces herself, says the interview is live
  proctored, asks how you are (or similar) and waits; after the opening she moves
  into detail from something you said, without "let's begin".
