"""Field vocabulary, borrowed rather than invented.

A generic interview sounds like it was written by someone who has never done the
job. The fix is to speak the way the documents speak, and the safeguard is that
every term is checked against them: jargon used wrongly is far worse than plain
language, because a specialist hears one misused term and stops trusting the
whole interview.

Same shape as quote verification in the feedback scorer, and for the same
reason. A model asked for the vocabulary of a field will cheerfully supply
plausible terms nobody in this particular workplace uses.
"""

from blueprint.generate import MAX_VOCABULARY, build_register, verify_vocabulary

JD = """
We are hiring a Business Analyst for retail banking. You will run requirement
discovery sessions, produce BRDs and functional specifications, and drive
stakeholder sign-off across product and operations.
"""

CV = """
Business Analyst at Saxo Bank. Led requirement gathering for digital
self-service, authored user stories and BRDs, ran UAT with operations.
"""

SOURCE = JD + CV


# -- borrowed terms survive --------------------------------------------------


def test_terms_the_documents_use_are_kept():
    kept = verify_vocabulary(
        ["BRD", "requirement gathering", "stakeholder sign-off", "UAT", "user stories"],
        SOURCE,
    )

    assert kept == ["BRD", "requirement gathering", "stakeholder sign-off", "UAT", "user stories"]


def test_matching_ignores_case_and_punctuation():
    assert verify_vocabulary(["brd", "User Stories."], SOURCE) == ["brd", "User Stories."]


# -- invented terms do not ---------------------------------------------------


def test_plausible_jargon_the_documents_never_use_is_dropped():
    """The failure this exists to prevent: a bot confidently using a term from a
    neighbouring field, in front of someone who works in this one."""
    kept = verify_vocabulary(
        ["BRD", "story points", "OKRs", "sprint velocity", "RICE scoring"], SOURCE
    )

    assert kept == ["BRD"]


def test_a_partial_word_does_not_count_as_a_match():
    """"UA" must not match because "UAT" appears. Half a term is not a term."""
    assert verify_vocabulary(["UA", "requirement"], SOURCE) == ["requirement"]


def test_duplicates_are_collapsed():
    assert verify_vocabulary(["BRD", "brd", "B.R.D."], SOURCE) == ["BRD"]


def test_the_list_is_capped():
    """The live context is kept small deliberately, and an interviewer reciting
    thirty terms sounds like it is showing off rather than listening."""
    many = ["requirement"] * 3 + [f"BRD {i}" for i in range(40)]
    assert len(verify_vocabulary(["BRD"] * 1 + ["requirement gathering", "UAT"] + many, SOURCE)) <= MAX_VOCABULARY


def test_junk_input_is_survivable():
    assert verify_vocabulary(None, SOURCE) == []
    assert verify_vocabulary([None, 3, "", "   "], SOURCE) == []
    assert verify_vocabulary(["BRD"], "") == []


# -- assembling the register -------------------------------------------------


def test_a_register_is_built_from_verified_terms_only():
    register = build_register(
        {"domain_language": {"domain": "business analysis in retail banking",
                             "vocabulary": ["BRD", "sprint velocity"]}},
        SOURCE,
    )

    assert register.domain == "business analysis in retail banking"
    assert register.vocabulary == ["BRD"]


def test_no_register_when_there_is_nothing_to_say():
    """Empty is a fine answer. An interview with no field vocabulary just speaks
    plainly, which is never wrong."""
    assert build_register({}, SOURCE) is None
    assert build_register({"domain_language": {"domain": "", "vocabulary": ["OKRs"]}}, SOURCE) is None


# -- what the interviewer is told --------------------------------------------


def test_the_prompt_carries_the_borrowed_words():
    from bot.brain.brain import InterviewBrain
    from shared.contracts import InterviewRegister
    from tests.test_brain import tiny_blueprint

    blueprint = tiny_blueprint()
    blueprint.domain_language = InterviewRegister(
        domain="business analysis in retail banking",
        vocabulary=["BRD", "requirement gathering"],
    )

    instruction = InterviewBrain(blueprint).plan_turn().system_instruction

    assert "business analysis in retail banking" in instruction
    assert "BRD" in instruction
    # The caution matters as much as the words.
    assert "Plain language is always safe" in instruction


def test_a_blueprint_without_a_register_says_nothing_about_language():
    from bot.brain.brain import InterviewBrain
    from tests.test_brain import tiny_blueprint

    instruction = InterviewBrain(tiny_blueprint()).plan_turn().system_instruction

    assert "THE LANGUAGE OF THIS FIELD" not in instruction


# -- refinement must not quietly undo it -------------------------------------


async def test_refining_a_plan_keeps_the_vocabulary():
    """Refinement changes the plan, not the field.

    The revision payload carries no vocabulary, so without carrying it across,
    one refinement would strip the interview of the language it was taught and
    nothing would say so.

    Calls the real `refine()` with a stubbed model. An earlier version of this
    test reimplemented the carry-across inline and would have passed with the
    fix deleted.
    """
    import json
    from types import SimpleNamespace

    from blueprint.refine import BlueprintRefiner
    from shared.contracts import InterviewRegister
    from tests.test_brain import tiny_blueprint

    blueprint = tiny_blueprint()
    blueprint.domain_language = InterviewRegister(
        domain="business analysis in retail banking", vocabulary=["BRD"]
    )

    payload = {
        "candidate_name": "A",
        "candidate_summary": "B",
        "claims_to_verify": [],
        "suggested_opening": "Hello.",
        "interviewing_guidance": [],
        "competency_plans": [
            {"competency_id": c.id, "name": c.name, "target_depth": "deep",
             "emphasis": 1.0, "seed_questions": ["q"]}
            for c in blueprint.evaluation_spec.competencies
        ],
        "reply": "Spent less time on the second one.",
    }

    class FakeCompletions:
        async def create(self, **kwargs):
            message = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    refiner = BlueprintRefiner(api_key="unused", model="unused")
    refiner._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    result = await refiner.refine(
        blueprint=blueprint, cv_text="a CV that mentions BRD", message="less time on beta"
    )

    assert result.blueprint.domain_language is not None
    assert result.blueprint.domain_language.vocabulary == ["BRD"]
    assert result.blueprint.domain_language.domain == "business analysis in retail banking"


# -- refinement must not claim a length change it cannot make ----------------
#
# Observed twice. Asked for a five-minute interview, the refiner replied "I
# reduced the emphasis for every competency to 0.5 and limited each to one seed
# question so the interview can fit into five minutes." The second time it had
# genuinely cut the seed questions, and described that accurately, with a false
# conclusion attached. Section budgets were untouched: 8.5, 5, 7, 5, 7, 3.5,
# still summing to forty minutes.
#
# Lowering every emphasis to the same number cannot shorten anything: minutes
# are split proportionally, so a uniform change divides the same total the same
# way. The code was already right. Only the claim was false.


def test_the_refiner_is_told_it_cannot_change_the_length():
    from blueprint.refine import SYSTEM

    assert "YOU CANNOT CHANGE HOW LONG THE INTERVIEW RUNS" in SYSTEM
    # And where it can be changed, so the employer is not left guessing.
    assert "Change this" in SYSTEM


def test_the_refiner_is_told_not_to_claim_an_outcome_it_did_not_produce():
    from blueprint.refine import SYSTEM

    assert "DESCRIBE WHAT YOU CHANGED, NOT WHAT YOU HOPE IT ACHIEVES" in SYSTEM
    # The exact false sentence that was observed, as the counter-example.
    assert "so the interview can fit into five minutes" in SYSTEM


async def test_a_refinement_cannot_shorten_the_interview():
    """The regression guard. Whatever the model returns, the stored budgets and
    the total length come out of the spec, unchanged."""
    import json
    from types import SimpleNamespace

    from blueprint.refine import BlueprintRefiner
    from tests.test_brain import tiny_blueprint

    blueprint = tiny_blueprint()
    before_total = blueprint.evaluation_spec.duration_minutes
    before_budgets = [p.time_budget_minutes for p in blueprint.competency_plans]

    payload = {
        "candidate_name": "A",
        "candidate_summary": "B",
        "claims_to_verify": [],
        "suggested_opening": "Hello.",
        "interviewing_guidance": [],
        # What it actually did: dropped every emphasis to 0.5 and cut the
        # questions. Uniform emphasis divides the same minutes the same way.
        "competency_plans": [
            {"competency_id": c.id, "name": c.name, "target_depth": "deep",
             "emphasis": 0.5, "seed_questions": ["q"]}
            for c in blueprint.evaluation_spec.competencies
        ],
        "reply": "I cut each competency down to one seed question.",
    }

    class FakeCompletions:
        async def create(self, **kwargs):
            FakeCompletions.sent = kwargs["messages"]
            message = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    refiner = BlueprintRefiner(api_key="unused", model="unused")
    refiner._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    result = await refiner.refine(
        blueprint=blueprint, cv_text="a CV", message="make it five minutes"
    )

    assert result.blueprint.evaluation_spec.duration_minutes == before_total
    # Emphasis may move time BETWEEN competencies, which is what it is for. It
    # cannot take any out: the interview is exactly as long as it was.
    assert sum(p.time_budget_minutes for p in result.blueprint.competency_plans) == sum(
        before_budgets
    )
    # And the model was handed the fixed length as a fact, not left to infer it.
    context = " ".join(m["content"] for m in FakeCompletions.sent)
    assert f"fixed at {before_total} minutes" in context
