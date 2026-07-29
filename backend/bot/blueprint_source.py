"""Where the bot gets its InterviewBlueprint.

**This is the seam.** Nothing outside this module may know whether a blueprint
came from a fixture on disk or a generated record in Postgres — that is what
lets the interview brain be identical in both cases.

Two sources, one contract:

- **Fixtures** (`load_blueprint`) — hand-written role-level blueprints with no
  candidate attached. Used by tests and as the neutral default.
- **Generated** (`load_blueprint_for_candidate`) — produced by Milestone 2 from
  a real JD and CV, carrying the candidate's summary and the specific claims the
  interview should test.

`resolve_blueprint` is what the bot process calls; it picks between them.
"""

import json
from pathlib import Path

from shared.contracts import InterviewBlueprint

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: Used when nothing is specified. Milestone 1's role-level base.
DEFAULT_FIXTURE = "pm_senior"

#: Candidate ids minted by the employer API. Blueprints for these live in the
#: database, not on disk.
CANDIDATE_ID_PREFIX = "cand_"


class BlueprintUnavailable(RuntimeError):
    """A blueprint was requested that cannot be loaded.

    Raised at bot startup rather than mid-interview: a bot that joins a room and
    then discovers it has no interview plan is worse than one that never starts.
    """


def load_blueprint(*, blueprint_id: str | None = None) -> InterviewBlueprint:
    """Load a fixture blueprint by name.

    Raises:
        FileNotFoundError: if the requested fixture does not exist.
    """
    name = blueprint_id or DEFAULT_FIXTURE
    path = FIXTURES_DIR / f"{name}.json"

    if not path.exists():
        available = sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))
        raise FileNotFoundError(
            f"No interview blueprint '{name}'. Available fixtures: {available or 'none'}"
        )

    # Validation is the point: a malformed blueprint must fail here, loudly, not
    # produce a bot that quietly interviews for nothing.
    return InterviewBlueprint.model_validate(json.loads(path.read_text()))


async def load_blueprint_for_candidate(candidate_id: str) -> InterviewBlueprint:
    """Load a generated blueprint from the database.

    Imported lazily so the bot process does not pull in the database layer when
    it is running against a fixture.
    """
    from app.db import BlueprintStatus, Candidate, get_sessionmaker

    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)

    if candidate is None:
        raise BlueprintUnavailable(f"No candidate '{candidate_id}' in the database.")

    if candidate.blueprint_status != BlueprintStatus.READY or not candidate.blueprint:
        raise BlueprintUnavailable(
            f"Candidate '{candidate_id}' has no usable blueprint "
            f"(status: {candidate.blueprint_status}). "
            + (f"Error was: {candidate.blueprint_error}" if candidate.blueprint_error else "")
        )

    try:
        return InterviewBlueprint.model_validate(candidate.blueprint)
    except Exception as exc:  # noqa: BLE001
        raise BlueprintUnavailable(
            f"Stored blueprint for '{candidate_id}' failed validation: {exc}"
        ) from exc


async def resolve_blueprint(*, blueprint_id: str | None = None) -> InterviewBlueprint:
    """Return the blueprint this interview should run against.

    A `cand_...` id means a real candidate with a generated, CV-specific
    blueprint. Anything else names a fixture.
    """
    if blueprint_id and blueprint_id.startswith(CANDIDATE_ID_PREFIX):
        return await load_blueprint_for_candidate(blueprint_id)
    return load_blueprint(blueprint_id=blueprint_id)
