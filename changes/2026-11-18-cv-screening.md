## 2026-11-18: HR adds candidates, and MITRA screens their CVs with evidence

**What**

HR opens an approved job request and adds a candidate: a name and a CV. They
see the text MITRA read from the file and choose Re-upload or Proceed. Proceed
starts screening, which:
1. removes the candidate's identity from the CV;
2. rates the CV against each competency the BU head defined, with the CV lines
   behind every rating, each line checked against the CV;
3. calculates the score in code;
4. writes ten questions for the HR round, each with its reason.

HR then decides: Begin HR round or Not now. Below the cut-off, or when the CV
says too little to judge, the choices are Reject or Proceed anyway, and
proceeding needs a reason. Every decision is recorded with MITRA's reading
beside it.

**Why**

Track H phase 1, step 2. A score HR can trust has to show where it came from.

**How**

- `screening/redact.py` removes:
  - the name and contact details;
  - links;
  - personal details (date of birth, gender, marital status, address, and
    the like);
  - institution names.

  Employers stay in. A test checks that the same CV under another name and
  gender redacts identically.
- `screening/fit.py`:
  - The model rates 0–4 or "no evidence" and must quote the CV word for word.
  - `verify_quote` checks each quote, ignoring case and punctuation, and drops
    invented ones. A rating left with no verified quote becomes "no evidence".
  - The HR questions get one retry before failing.
- `shared/screening.py`: `MIN_ASSESSED_WEIGHT_PERCENT` (75) and
  `passes_screen`.
  - Found in the first live run: a product manager's CV scored 50% for an LLM
    engineering job on 10% of the weight.
  - Too little evidence now counts at the gate like a low score.
- `hiring/candidates.py`: add a candidate, re-upload, proceed, the candidate
  view and list, the background screening task, and the gate rules.
  `hiring/routes.py` uses the gate rules on every move.
- `hiring/models.py`: the `Screening` table, in Track H's own module.
  Migration 0004.
- Console (`features/screening/`): the candidate list with filters, Add
  candidate, and the candidate page:
  - checking the CV, then screening, then the result;
  - the score against the cut-off, ratings with their CV quotes, and the HR
    questions;
  - the decision, with a reason where one is required, and the history.

  HR now has Job requests in the sidebar, and job titles open the candidate
  list.
- `docs/DEV_PLAN.md`: decision 26.

**Live check**

The real model screened two of the repository's test CVs:
- An AI engineer's CV scored 100% on 100% of the weight, every rating quoted.
- A product manager's CV scored 50% on 10% of the weight, and is now marked
  "too little evidence to judge". One invented quote was caught and dropped.

**Confirm**

```bash
cd backend && uv run pytest -q                 # 870 passed
cd frontend/console && npm run build
```
