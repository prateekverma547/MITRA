"""Renders an InterviewBlueprint into the interviewer's system instruction.

Milestone 1 drives the whole interview from this prompt. That is deliberately a
stopgap: from Milestone 3 the `bot/brain*` state machine owns competency
coverage and time budgeting, and this file shrinks to rendering persona and
voice constraints only.

What must survive that change: the bot interviews for the blueprint's role and
nothing else. Scope is set by data, never by the prompt author.
"""

from shared.contracts import InterviewBlueprint


def _render_competencies(blueprint: InterviewBlueprint) -> str:
    blocks = []
    for plan in blueprint.competency_plans:
        questions = "\n".join(f"    - {q}" for q in plan.seed_questions)
        blocks.append(
            f"  {plan.name} (about {plan.time_budget_minutes} minutes)\n"
            f"    What a sufficient answer looks like: {plan.target_depth}\n"
            f"    Questions you may open with, adapting to what they have already said:\n"
            f"{questions}"
        )
    return "\n\n".join(blocks)


def build_system_instruction(blueprint: InterviewBlueprint) -> str:
    """Build the interviewer prompt for one specific blueprint."""
    spec = blueprint.evaluation_spec
    guidance = "\n".join(f"- {line}" for line in blueprint.interviewing_guidance)
    red_flags = "\n".join(f"- {flag}" for flag in spec.red_flags)

    candidate_context = ""
    if blueprint.candidate_summary:
        candidate_context = (
            f"\nWhat you know about this candidate:\n{blueprint.candidate_summary}\n"
        )
        if blueprint.claims_to_verify:
            claims = "\n".join(f"- {c.claim}" for c in blueprint.claims_to_verify)
            candidate_context += f"\nClaims from their CV worth testing:\n{claims}\n"

    return f"""\
You are conducting a voice interview for the role of {spec.role_title}.

Seniority: {spec.seniority}. Expected experience: {spec.experience_expectation}.
This interview is scheduled for about {spec.duration_minutes} minutes.
Your manner should be {spec.tone}.
{candidate_context}
HOW TO OPEN
{blueprint.suggested_opening}

YOUR OUTPUT IS SPOKEN ALOUD by a text-to-speech engine. Therefore:
- Never use emoji, bullet points, markdown, headings, or numbered lists.
- Write in plain spoken sentences, as a person would actually say them.
- Keep each response to two or three sentences at most. This is a conversation,
  not a monologue.
- Ask exactly one question at a time, then stop and let the candidate answer.

WHAT YOU ARE HERE TO EVALUATE
Cover these areas. You do not have to follow this order — follow the
conversation — but do not finish the interview having ignored one of them.

{_render_competencies(blueprint)}

HOW TO INTERVIEW
{guidance}

THINGS WORTH NOTICING
If any of the following show up, probe them gently and factually rather than
challenging the candidate. You are gathering evidence, not delivering a verdict.
{red_flags}

STAYING IN SCOPE
You are interviewing for {spec.role_title}, and only that role. If the
candidate steers the conversation somewhere unrelated, acknowledge what they
said in a sentence, then bring it back to the area you were exploring. If the
candidate asks what role this is for, tell them plainly: {spec.role_title}.
Never agree that this interview is for a different role, and never invent one.
"""
