## 2026-11-18: dashboards, a candidate directory and ⌘K search

**What**

The home page is a dashboard, scoped to the person signed in. It filters by
business unit and by period (30 days, 90 days, all time), and shows:
- headline figures with eight-week trends;
- where everyone stands as a pipeline bar, where selecting a step opens those
  candidates;
- Needs you, oldest wait first;
- recent activity;
- each business unit's pipeline, where selecting a unit focuses the page;
- the screening score distribution against the cut-off;
- candidates added against decisions made, by week.

Every chart has a table view.

A new Candidates page lists everyone across every job and business unit. It
has search, filter chips with counts (unit, step, waiting for you, below the
cut-off or unclear), sorting and pages, all kept in the URL. ⌘K (Ctrl K)
opens a search for candidates, jobs and business units from anywhere. A
job's candidate list can be read as a board, one column per step.

**Why**

The user asked for the console to work as a premium product when there are
many business units and many candidates: how numbers are shown, and how HR
and BU heads find people.

**How**

- `hiring/insights.py`: `/api/overview`, `/api/candidates` and `/api/search`,
  all scoped by `job_scope`. Stages are grouped into the journey's six steps.
- Console `shell/viz/`:
  - stat tiles with sparklines, the pipeline bar and its thin version,
    the score histogram and the flow chart;
  - a shared tooltip, and chart cards with a table toggle.

  Colours were validated with the dataviz skill's script for both themes:
  - an ordinal blue ramp for steps, grey for closed;
  - blue and orange for the two weekly series.
- `features/directory/`, `shell/CommandPalette.tsx`, and the board view in
  `features/screening/CandidateList.tsx`.
- Headline figures now use the sans, not the serif; the dataviz skill flags a
  serif headline figure as an anti-pattern.
- `scripts/seed_demo_pipeline.py`: 70 invented candidates across 5 units and
  9 jobs, with a fixed seed so everyone sees the same charts.
- `docs/DEV_PLAN.md`:
  - decision 27: the HR round uses one combined recording, because there is
    no S3 bucket;
  - decision 28: the dashboard rules.

**Confirm**

```bash
cd backend && uv run pytest -q                       # 883 passed
uv run python scripts/seed_demo_pipeline.py          # demo data, local only
cd frontend/console && npm run build
```
