## 2026-11-18: Track H phase 0, backend: organisation, sign-in, stages

**What**

The console now has a backend:
- organisations, business units and user accounts with roles;
- sign-in with a session cookie;
- a BU head only ever sees their own BU;
- the candidate's stage, which only changes through one function that records
  who moved them and why.

The database changes through versioned migrations, and stg's existing database
is adopted rather than rebuilt. The shared data shapes Track I builds against
(fit analysis, HR questions, HR round evaluation, decisions) exist, with
sample data.

**Why**

This is phase 0 of Track H (docs/TRACK_H.md). Every later screen needs to know
who is signed in, what they may see, and where a candidate stands.

**How**

- `shared/stages.py`, `shared/screening.py`, `shared/fixtures/`: the
  contracts.
  - Fit percentages are computed in code; a typed-in number is refused.
  - HR rejects at screening and after the HR round; after the technical round
    only the BU head decides.
- `alembic.ini` and `migrations/`:
  - 0001 is the schema as stg left it; 0002 adds the organisation tables.
  - `app.db.prepare_schema()` runs at startup and stamps databases built
    before migrations existed.
- `org/`: models, auth (scrypt, signed cookie, `current_user`,
  `require_role`, `job_scope`), users, routes under `/api`, and the demo seed.
- `hiring/`: decision and audit tables, `move()`, and the move and history
  routes.
- `live/`, `proctoring/`, `analytics/`: empty routers for Track I, registered
  once in `app/main.py`.
- `tests/route_walk.py`: route sweeps now walk included routers. With FastAPI
  0.141 the old sweep had silently skipped every `/interviews` route.
- `scripts/seed_org.py` and `scripts/create_user.py`.
- `.env.example`: `SESSION_SECRET`.

**The console (added the same day)**

- `frontend/console/`: React 19 + Vite 8 + TypeScript 7, with exact versions
  pinned in the lockfile.
  - Hash routing.
  - Feature folders register their own routes, so the shell is never edited
    to add a screen.
  - The welcome page has three doors to one sign-in.
  - A home screen per role, fed by the already-scoped `/api/home`.
- FastAPI serves the built app at `/console`, and says how to build it when it
  has not been built.
- `Dockerfile`: a Node stage builds the console; only `dist/` reaches the
  runtime image. Not yet built locally: Docker is not installed here.
- `tests/hiring/test_console_contracts.py` fails if the console's copy of the
  stage and role values drifts from `shared/stages.py`.

**Left undone**

- `npm audit` reports a high-severity ReDoS in `picomatch`, which Vite uses
  at build time only. The suggested fix downgrades Vite to 6.2, so it is left
  until Vite ships a patched release.
- Rate limiting on sign-in.
- User management screens (phase 4).

**Confirm**

```bash
cd backend && uv run pytest -q              # 838 passed
cd frontend/console && npm install && npm run build
uv run python scripts/seed_org.py           # demo org, prints the sign-ins
```
