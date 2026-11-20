## 2026-11-20: MITRA's plans are pre-screen plans, and its preparation

**What**

Product owner's feedback, two rounds the same day: MITRA asked questions that were
too deep, and then, once shallower, still felt compact, résumé-bound and niche. It
is a pre-screener: a conversation that finds out what the candidate has worked on,
whether they know the basics of their field and can reason with them a little, and
whether the CV holds up. Its tone, pauses, follow-ups and latency were right and are
untouched. Nothing is hardcoded: every question, goal and note is written per
candidate by the plan generator, and MITRA words everything itself.

**1. The plan generator** (`blueprint/generate.py`)
- Pre-screen, not expert round: no design, deep trade-offs, scaling, post-mortems,
  puzzles or edge cases; every question answerable in about a minute by someone who
  did the work. Before this the prompt asked for "the trade-off, the failure, the
  number and how it was measured".
- The plan is also MITRA's preparation: the realtime model cannot look anything up
  mid-conversation, so the plan carries what it thinks with.
  - `opening_goals` (new): 3 or 4 broad goals for an exploratory opening about the
    candidate's work and field ("Learn which tools and methods he uses day to day").
    Never motivation, feelings or personality; the opening is not scored.
  - Per area (new): `key_ideas` (the field's ideas a good answer rests on, so she
    can tell a correct basic answer from a vague one), `level_signals` (how a newer
    and an experienced answer sound) and `analytical_angles` (light "how would you
    think about it" directions grounded in their own work).
- Each area goes broad, then a basic idea, then one light analytical question, then
  the CV; at most half of an area's questions name a CV item. Examples of the
  modern analytical level by field (AI engineer, ML engineer, sales executive,
  sales or BD manager, business analyst, software, product), from research on
  current screening practice, as levels, never templates.
- The opening is about 15% of the interview (3 to 6 minutes; it was a fixed 2), and
  is re-timed to the booked length when the interview starts (`bot/run_realtime.py`).
  Refinement keeps the plan's opening length and carries the preparation across
  (`blueprint/refine.py`).

**2. MITRA's prompt: only the opening and the area block** (`bot/realtime_prompt.py`)
- The first turn always ends with a greeting that invites a reply (how they are,
  whether they can hear clearly, whether they are ready; chosen per interview) and
  waits. Half the first turns used to ask an interview question in the same breath.
- With `opening_goals`, the opening is "getting to know their work": broad, their
  choice of example, used to learn their level. Older plans open as before.
- Each area block carries its briefing ("never recite, teach or correct them
  aloud") and asks her to open the area broadly and pitch it to the level the
  opening showed, still covering every goal. Older plans: no briefing, as before.

**3. Shared contract** (`shared/contracts.py`, additive, its own PR):
`InterviewBlueprint.opening_goals`; `CompetencyPlan.key_ideas`, `level_signals`,
`analytical_angles`. All default empty; every older plan loads and runs unchanged.
The shared sample plan carries the new fields, empty.

**Applies to** plans generated from now on.

**Confirm**

- Generated with the real model for the sample sales CV (Sales "Business
  Development Manager") and the AI/ML fixture CV ("AI engineer"): opening goals
  about their work; areas such as "What outbound prospecting work have you
  personally done?", "What makes a company worth targeting for custom software
  services?", "If outbound meetings were being booked but few became opportunities,
  what would you look at first?", then their CV.
- Run a MITRA round: Mitra greets, asks how you are (or similar) and waits; then a
  few broad questions about your work before the first area.
