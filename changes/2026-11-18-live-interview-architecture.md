## 2026-11-18: the live interview architecture, decided

**What**

Only the realtime model now sits between the candidate and Mitra's reply. Its
own transcript is the record, and Deepgram is removed from the new platform.
Transcripts are kept raw and cleaned. A small auditor model labels each
question as plan, follow-up or off-plan, and records only. Candidates see no
captions. A Check Intelligence tab in the admin panel shows how each interview
ran, turn by turn.

**Why**

Quality and latency are the two goals for the live interview. Anything placed
between the candidate and the reply adds latency, so nothing is. Quality is
held by binding the questions to the plan in the prompt, and it is measured,
not steered: steering notes risk derailing a conversation that the realtime
model already runs well. Steering becomes a new decision only if the auditor's
numbers show a real off-plan problem.

**How**

- `docs/DEV_PLAN.md` changes in three places:
  - Decisions 18–25.
  - Section 3, now "Models and the live interview": the live diagram, the
    rules for raw and cleaned transcripts, and Check Intelligence.
  - The feature catalogue, track work, testing and phases, updated to match.
    A transcript accuracy check is added. The Director and Deepgram are
    removed. Phase 5 now includes UI polish.
- `CLAUDE.md` "Current direction": the realtime reply path and the transcript
  record.

**Left undone**

- Nothing is built yet. On `dev` the old candidate page still shows Mitra's
  captions; the new candidate app ships without them.
- A transcript accuracy check on 5 recorded sessions comes before the
  realtime transcript is relied on for report quotes.

**Confirm**

Docs only. `cd backend && uv run pytest -q` is unchanged.
