"""Tests for the blueprint seam: fixtures on disk vs generated records in the DB.

The point of this module is that nothing downstream can tell the difference. A
generated blueprint must drive the brain exactly as a fixture does, and a
missing or half-generated one must fail at startup rather than producing a bot
that joins a room with no interview plan.
"""

import pytest

from bot.blueprint_source import (
    BlueprintUnavailable,
    load_blueprint,
    resolve_blueprint,
)
from bot.brain.brain import InterviewBrain
from shared.contracts import Competency, CompetencyPlan, EvaluationSpec, InterviewBlueprint


def sample_blueprint(candidate: str | None = "Prateek Verma") -> InterviewBlueprint:
    return InterviewBlueprint(
        blueprint_id="cand_test123",
        evaluation_spec=EvaluationSpec(
            role_title="Lead Product Manager — AI & Data Products",
            seniority="Senior",
            experience_expectation="10+ years",
            duration_minutes=40,
            competencies=[
                Competency(id="judgement", name="Judgement", description="d", weight=1.0)
            ],
        ),
        candidate_name=candidate,
        candidate_summary="Twelve years leading AI product teams." if candidate else None,
        competency_plans=[
            CompetencyPlan(
                competency_id="judgement",
                name="Judgement",
                target_depth="Names AI ideas they killed.",
                seed_questions=["Tell me about an AI feature you decided not to build."],
                time_budget_minutes=30.0,
            )
        ],
        suggested_opening="Hello.",
    )


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/bp.db")
    from app import db as db_module

    await db_module.reset_engine()
    await db_module.create_all()
    yield db_module
    await db_module.reset_engine()


async def store_candidate(db, *, status, blueprint=None, error=None) -> str:
    async with db.get_sessionmaker()() as session:
        session.add(db.Job(id="job_1", jd_text="a jd", spec_status=db.SpecStatus.READY))
        await session.commit()
    async with db.get_sessionmaker()() as session:
        session.add(
            db.Candidate(
                id="cand_test123",
                job_id="job_1",
                cv_text="a cv",
                blueprint=blueprint.model_dump(mode="json") if blueprint else None,
                blueprint_status=status,
                blueprint_error=error,
            )
        )
        await session.commit()
    return "cand_test123"


# -- fixtures ---------------------------------------------------------------


def test_default_is_the_role_level_fixture():
    blueprint = load_blueprint()

    assert blueprint.role_title == "Senior Product Manager"
    # No CV attached, so no candidate context — the bot must not invent one.
    assert blueprint.candidate_name is None
    assert blueprint.candidate_summary is None
    assert blueprint.claims_to_verify == []


async def test_resolve_falls_back_to_fixtures_for_non_candidate_ids():
    blueprint = await resolve_blueprint(blueprint_id="sre_staff")

    assert blueprint.role_title == "Staff Site Reliability Engineer"


# -- generated blueprints ---------------------------------------------------


async def test_generated_blueprint_loads_from_the_database(db):
    candidate_id = await store_candidate(
        db, status=db.BlueprintStatus.READY, blueprint=sample_blueprint()
    )

    blueprint = await resolve_blueprint(blueprint_id=candidate_id)

    assert blueprint.candidate_name == "Prateek Verma"
    assert blueprint.role_title == "Lead Product Manager — AI & Data Products"


async def test_generated_blueprint_drives_the_brain_identically(db):
    """The seam's whole purpose: the brain cannot tell where this came from."""
    candidate_id = await store_candidate(
        db, status=db.BlueprintStatus.READY, blueprint=sample_blueprint()
    )

    brain = InterviewBrain(await resolve_blueprint(blueprint_id=candidate_id))

    assert [s.id for s in brain.sections] == ["opening", "judgement", "closing"]
    prompt = brain.plan_turn().system_instruction
    assert "Lead Product Manager" in prompt


async def test_candidate_context_reaches_the_prompt(db):
    """Without this the interview cannot say "based on your background" — the
    exact gap that made a live session feel impersonal."""
    candidate_id = await store_candidate(
        db, status=db.BlueprintStatus.READY, blueprint=sample_blueprint()
    )
    brain = InterviewBrain(await resolve_blueprint(blueprint_id=candidate_id))
    brain.observe(bot_text="Tell me about your background.", candidate_text="Twelve years in AI product.")

    prompt = brain.plan_turn().system_instruction

    assert "ABOUT THIS CANDIDATE" in prompt
    assert "Twelve years leading AI product teams." in prompt


async def test_fixture_prompt_has_no_candidate_section():
    """Better to say nothing than to invent a background."""
    brain = InterviewBrain(load_blueprint())

    assert "ABOUT THIS CANDIDATE" not in brain.plan_turn().system_instruction


# -- failing loudly, at startup ---------------------------------------------


async def test_unknown_candidate_fails_before_the_bot_joins(db):
    with pytest.raises(BlueprintUnavailable, match="No candidate"):
        await resolve_blueprint(blueprint_id="cand_nothere")


async def test_blueprint_still_generating_is_refused(db):
    """A bot that joins a room and then finds it has no plan is worse than one
    that never starts."""
    candidate_id = await store_candidate(db, status=db.BlueprintStatus.GENERATING)

    with pytest.raises(BlueprintUnavailable, match="no usable blueprint"):
        await resolve_blueprint(blueprint_id=candidate_id)


async def test_failed_generation_surfaces_the_original_error(db):
    candidate_id = await store_candidate(
        db, status=db.BlueprintStatus.FAILED, error="model unavailable"
    )

    with pytest.raises(BlueprintUnavailable, match="model unavailable"):
        await resolve_blueprint(blueprint_id=candidate_id)


async def test_corrupt_stored_blueprint_is_rejected(db):
    async with db.get_sessionmaker()() as session:
        session.add(db.Job(id="job_1", jd_text="a jd", spec_status=db.SpecStatus.READY))
        await session.commit()
    async with db.get_sessionmaker()() as session:
        session.add(
            db.Candidate(
                id="cand_bad",
                job_id="job_1",
                cv_text="a cv",
                blueprint={"blueprint_id": "cand_bad"},  # missing everything else
                blueprint_status=db.BlueprintStatus.READY,
            )
        )
        await session.commit()

    with pytest.raises(BlueprintUnavailable, match="failed validation"):
        await resolve_blueprint(blueprint_id="cand_bad")


def test_unknown_fixture_lists_what_is_available():
    with pytest.raises(FileNotFoundError, match="pm_senior"):
        load_blueprint(blueprint_id="does_not_exist")
