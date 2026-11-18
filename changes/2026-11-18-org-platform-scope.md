## 2026-11-18: MITRA becomes an organisation's hiring platform

**What**

The plan now covers a whole organisation:

- BU heads raise job requests for their own unit.
- HR screens CVs against them and runs a human HR round inside MITRA.
- MITRA runs the proctored technical round.
- The BU head makes the final call.
- An org admin sees everything, with system, business and transparency analytics.

No code changed in this entry. It records the scope and the decisions so that
both engineers start from the same page.

**Why**

The product goal moved from a single employer running AI interviews to a
production platform an organisation runs its hiring through. The scope is
fixed; the intelligence gets deeper later.

**How**

- `docs/DEV_PLAN.md`: rewritten. It covers:
  - roles, the journey and the candidate status machine;
  - 15 recorded decisions and the model slots;
  - Track C (candidate and intelligence) and Track E (organisation and
    console), and the contracts between them;
  - conflict rules, proctoring, the HR round, live testing, and phases with
    checkpoints.
- `CLAUDE.md` "Current direction" now records:
  - the organisation platform and the three roles;
  - who decides at each gate;
  - integrity as a separate report section, with captions-only reminders
    capped at three;
  - withdrawal always confirmed once;
  - one configurable text model for off-call work;
  - candidates in India.
- `docs/REALTIME_ARCHITECTURE.md`: proctoring rule 4 is changed. It forbade
  any mid-interview integrity reminder; it now allows neutral, capped,
  on-screen captions.

**Left undone**

- Everything in the plan is unbuilt; phase 0 starts after `dev` is pushed.
- `gpt-6.2-sol` is not in OpenAI's API docs today. The plan defaults to
  `gpt-6.1-sol` until it is.

**Confirm**

```bash
cd backend && uv run pytest -q   # unchanged: 782 passed
```
