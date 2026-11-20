# One sample, through every panel

Everything here is invented, so it is safe to upload anywhere.

| File | Who uses it | Where |
| --- | --- | --- |
| `job-description-ai-engineer.md` | BU head | New job request, "JD file" |
| `cv-sample-candidate.txt` | HR | The job's candidates, "Add a candidate" |

## Start clean

```bash
cd backend
uv run python scripts/reset_local.py --yes   # local only: keeps the four accounts, removes everything else
uv run uvicorn app.main:app --port 8000 --reload
```

Open http://localhost:8000/console/. The sign-in screen offers the four demo
accounts.

## The walkthrough

1. **AI BU Head.**
   - Choose "New job request", give the title "AI Engineer, Applied LLM", and
     upload the JD.
   - Chat with MITRA, check "What MITRA understood", then approve it.
2. **HR Partner.**
   - Open the job and add a candidate, Ananya Verma, with the CV. Check the
     text MITRA read, then choose Proceed.
   - Read the fit, then choose "Begin HR round" and "Book the HR round".
   - Join as HR. In a private window, open http://localhost:8000/join and
     enter the meeting ID and password as the candidate. Talk, then end the
     round.
   - When the evaluation is in, choose "Proceed to technical". MITRA prepares
     its plan.
   - Choose "Book the technical round" and join as the candidate on `/join`.
     MITRA interviews.

     To skip the call:
     `uv run python scripts/simulate_technical_done.py "Ananya Verma"`
   - When it is done, choose "Send to the BU head".
3. **AI BU Head.** Open the candidate from "Your decisions to make", read the
   results, and Shortlist, put On hold, or Reject.
4. **Org Admin.** Look at the overview and People. Then open Transparency, to
   see every decision beside MITRA's recommendation.

The HR round and the technical round need `OPENAI_API_KEY` and
`DAILY_API_KEY` in `.env`.
