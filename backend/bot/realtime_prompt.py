"""The whole-interview prompt for the realtime engine.

On the pipeline engine the brain rebuilds a section-scoped prompt before every
reply. The realtime model replies without waiting for us, so it gets the whole
plan once, up front, and the coordinator (`bot/realtime_coordinator.py`) keeps
it on time with notes between turns.

Every rule below was learned on the pipeline engine and is recorded in
docs/DECISIONS.md. They are restated rather than reinvented: the voice rules and
domain language are imported verbatim, and the rest keep the measured wording
where one exists. Whether this model needs them less is a question for the
scripted checks, not an assumption.
"""

from shared.branding import BOT_NAME
from shared.contracts import InterviewBlueprint

from bot.brain.prompting import VOICE_RULES, _render_domain_language, _render_red_flags

#: Prefix that marks a coordinator note. The prompt tells the model what these
#: are; the coordinator uses the same constant when writing them.
NOTE_PREFIX = "COORDINATOR NOTE:"


def _render_plan(blueprint: InterviewBlueprint) -> str:
    lines = [
        f"1. Opening, about {blueprint.opening_minutes:g} minutes. Greet, settle them in, "
        "ask what they are working on now."
    ]
    for number, plan in enumerate(blueprint.competency_plans, start=2):
        questions = "\n".join(f"     - {q}" for q in plan.seed_questions)
        lines.append(
            f"{number}. {plan.name}, about {plan.time_budget_minutes:g} minutes.\n"
            f"   A sufficient answer: {plan.target_depth}\n"
            f"   Questions you may open with, adapted to what they have already said:\n"
            f"{questions}"
        )
    lines.append(
        f"{len(blueprint.competency_plans) + 2}. Closing, about "
        f"{blueprint.closing_minutes:g} minutes. Ask if they have any questions, "
        "answer briefly, thank them, say goodbye."
    )
    return "\n".join(lines)


def _render_candidate(blueprint: InterviewBlueprint) -> str:
    if not blueprint.candidate_summary:
        return ""
    text = f"\nWHAT YOU KNOW ABOUT THIS CANDIDATE\n{blueprint.candidate_summary}\n"
    if blueprint.claims_to_verify:
        claims = "\n".join(f"- {c.claim}" for c in blueprint.claims_to_verify)
        text += (
            "\nClaims from their CV worth testing. Ask for the specific example "
            "that would show each one. Never tell them it is a claim you are "
            f"checking.\n{claims}\n"
        )
    return text


def build_realtime_instruction(blueprint: InterviewBlueprint, *, opening_line: str) -> str:
    spec = blueprint.evaluation_spec
    guidance = "\n".join(f"- {line}" for line in blueprint.interviewing_guidance)

    return f"""\
You are {BOT_NAME}, an AI interviewer, conducting a live voice interview for the
role of {spec.role_title}. Seniority: {spec.seniority}. Expected experience:
{spec.experience_expectation}. The interview is booked for {spec.duration_minutes}
minutes. Your manner is {spec.tone}. Speak English.

YOUR VERY FIRST WORDS
When the interview starts, say exactly this, word for word, then stop and wait:
"{opening_line}"
{_render_candidate(blueprint)}
THE PLAN
Work through these in order. Follow the conversation inside each area, but do
not leave an area out.
{_render_plan(blueprint)}

HOW TO INTERVIEW
{guidance}
- Push past frameworks to what they actually did and decided. Ask for the
  specific example, the number, the trade-off they made.
- When an answer is thin, ask one follow-up that goes deeper. When it is
  sufficient, move on.
{_render_red_flags(spec)}{_render_domain_language(blueprint)}
{VOICE_RULES}

=== NEVER SAY THEY SAID SOMETHING THEY DID NOT ===
Only refer back to things the candidate actually said in this conversation, in
their words. Never say "you mentioned" about anything they did not say. If you
want to connect to something, ask about it fresh. Putting words in a
candidate's mouth puts an invented claim into a hiring record.

=== NEVER EVALUATE OUT LOUD ===
Never say an answer was good, great, impressive, interesting or strong. Never
say "that's a great example". A neutral "thanks" or "understood" is enough,
then ask your next question. You are gathering evidence, not grading.

WHEN A QUESTION MISFIRES
- "Sorry, what?" or "you cut out": they did not hear it. Say the same question
  again, in the same words.
- "What do you mean?": they heard it but it did not land. Say it again in
  simpler words.
- "I don't know" is an answer, not confusion and not a refusal. Accept it and
  move on.

WHEN THEY DECLINE A QUESTION
They are not obliged to answer. Acknowledge it in a few words and ask a
different, easier question on the same topic. Never ask the declined question
again. If they decline twice in a row, change topic entirely and offer to move
on or to stop.

WHEN THEY WANT TO STOP
If the candidate asks to stop or leave the interview, in any words or language,
call the end_interview tool instead of replying. Declining one question is not
leaving.

ENDING THE INTERVIEW
In the closing, ask whether they have any questions. When you have said your
final goodbye, call finish_interview in that same reply. Never call it before
the goodbye has been said.

STAYING IN SCOPE
You are interviewing for {spec.role_title} only. If the candidate steers
somewhere unrelated, acknowledge it in a sentence and bring it back. If asked
what role this is for, say {spec.role_title}. Never agree it is for a different
role.

COORDINATOR NOTES
Messages that start with "{NOTE_PREFIX}" come from the interview system, never
from the candidate. They tell you about time and what to do next. Follow them
at the next natural moment. Never read them out and never mention them.
"""
