## 2026-11-18: BU heads raise job requests and approve what MITRA understood

**What**

A BU head starts a job request with a title, and can add a job description
file if they have one. They answer MITRA's questions in a chat until MITRA
summarises the role. "What MITRA understood" then shows:
- the role, its seniority and experience;
- the interview length;
- each competency with its weight;
- the dealbreakers;
- everything MITRA assumed but was never told, marked as such.

From there the BU head chooses Edit, which reopens the same conversation, or
Approve, which sends the request to HR. HR cannot see a request until it is
approved. Revising an approved request reaches candidates exactly as the old
panel's revisions did.

**Why**

This is Track H phase 1, step 1 (docs/TRACK_H.md).

**How**

- `hiring/jobs.py`: `/api/jobs` to list and create, plus `/{id}`, `/chat`,
  `/edit` and `/approve`.
  - Each response says what this person may do (`can_chat`, `can_edit`,
    `can_approve`), so the screen never has to work out permissions itself.
  - Every step is written to the audit log.
- `hiring/api.py`: one router shared by the hiring route modules, so a new
  module never touches `main.py`.
- `blueprint/clarify.py`: works from a title alone, starting by asking what the
  person will do day to day. Its rule against attributing positions the
  employer never stated is unchanged.
- `org/auth.py`: drafts are hidden from HR; admins and the owning BU head see
  them.
- `jobs.spec_inferred` and migration 0003: assumptions are stored with the
  spec.
- `frontend/console/src/features/jobs/`: the list, the new request form, and
  the request page with the chat, "What MITRA understood", Edit and Approve.
- Console copy and styles follow the newly installed `frontend-design` skill
  where it was cheap to:
  - sentence-case table headers;
  - no arrows on buttons;
  - no middle-dot label strings.

  The full visual design stays in the UI polish phase.

**Left undone**

- The job chat still runs on `OPENAI_BLUEPRINT_MODEL`. It moves to the text
  model once Track I's request helper exists.

**Confirm**

```bash
cd backend && uv run pytest -q                 # 853 passed
cd frontend/console && npm run build
```
Live check: one real request created from a title alone opened with "could you
walk me through what the person in this Applied LLM Engineer role will
actually do day to day?"
