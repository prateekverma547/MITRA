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

**Left undone**

- The console app (React shell, role homes, the welcome page) needs Node.js.
- Rate limiting on sign-in.
- User management screens (phase 4).

**Confirm**

```bash
cd backend && uv run pytest -q              # 835 passed
uv run python scripts/seed_org.py           # demo org, prints the sign-ins
```
