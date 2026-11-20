"""Renders the per-section system instruction.

This replaces the whole-interview prompt from Milestone 1. The model is told
about *this section only*, plus whatever it must remember from earlier ones.

That is the trade the sectioned design makes: instead of a growing chat log, the
model gets a small, stable, section-scoped instruction and a short list of
carried claims. It keeps the operative instructions near the top of the context
instead of buried under thousands of tokens of history — which is what should
make a mini-tier live model viable.

Nothing here imports Pipecat.
"""

import re

from shared.branding import BOT_NAME
from shared.contracts import (
    Contradiction,
    UnexplainedTerm,
    CoverageLevel,
    InterviewBlueprint,
    KeyClaim,
    SectionKind,
)

from bot.brain.state import Section

# The length rule is a TURN limit, and it used to be a sentence limit.
#
# It said "roughly fifteen words" per sentence and allowed "two or three short
# sentences per turn", which permits forty-five. So the twenty-seven-word turns
# measured on a real interview were not breaking the rule; the rule had never
# set a bound on the thing a candidate actually sits through. The interviewer
# spoke 48% of all the words in that interview and the candidate said it felt
# long, which is what they were feeling: composition never exceeded 1943ms and
# the twelve-second gap everyone suspected turned out not to exist.
#
# Written the way the one-question rule is written, because that one holds: a
# number, and an instruction to count it. The rationale that used to sit here
# ("a long sentence is one rushed breath") is true and the model does not need
# it, so it lives in this comment instead of in every prompt.
#
# The alternative is removed rather than discouraged, per the measurement at the
# contradiction block below: the preamble is named as the thing that must not
# happen, because a preamble is what carries a reply past twenty words.
VOICE_RULES = """\
=== ONE QUESTION. NOT TWO. ===
Your whole reply contains exactly ONE question mark. Count it before you answer.

The tempting move is to ask a broad question and then narrow it in the same
breath: "Tell me about a project you led. What did you measure to judge whether it worked?" Do not
do that. It reads fine on a page and fails out loud, because the candidate hears
two things, cannot hold both, and answers the easier one. You then lose the
answer you actually wanted and have to spend another turn getting back to it.

If you want the narrower thing, ASK ONLY THE NARROWER THING. If you want the
broad opening, ask only that and save the follow-up for your next turn. You will
get another turn. There is no rush.

Say this:
  "What did you measure to judge whether it worked?"

Not this:
  "Tell me about a project you led. What did you measure to judge whether it worked?"

YOUR OUTPUT IS SPOKEN ALOUD by a text-to-speech engine. Therefore:
- Never use emoji, bullet points, markdown, headings, or numbered lists.
- Write in plain spoken sentences, as a person would actually say them.

=== TWENTY WORDS. COUNT THEM. ===
Your whole reply is TWENTY WORDS or fewer, including the question. Not twenty
a sentence: twenty in total. Count them before you answer.

You will want to set the question up first: what they just said, then why you
are asking. Do not. That preamble is what carries a reply past twenty words and
it tells them nothing they do not know. They were there.

Acknowledge, then ask. If it will not fit, ask the smaller half and keep the
rest for next turn.

- One idea per sentence. Use full stops, not commas, to join ideas.
- Never stack a greeting, an explanation and a question together.
- Never use an em dash or en dash (— or –). A dash reads as machine-written,
  and the employer reads these words back.

=== NEVER SAY AN ANSWER WAS GOOD ===
Do not praise or characterise what they said. Not "that makes sense", not "you
explained how that works", not "interesting". You are often wrong about whether
an answer contained anything, and a person reads this back later.

Acknowledge with only these, varied: "Thanks." "Okay, thanks." "Got it." After
"I don't know": "No problem." If you misheard: "Sorry, my mistake." Never echo
their answer, and never claim they said what you did not hear.\""""

# The acknowledgement used to be "Okay." or "Right." plus ONLY words they used,
# and "Begin with the question" above. Live in `int_39845b4313d4` that read as
# not listening: answers, "I don't know" and corrections were all followed
# straight by the next question, or by a misheard word said back to them ("You
# said automation dots", to which the candidate replied "But I didn't say it").
#
# So the acknowledgement is now a closed list, chosen by situation. A closed
# list keeps the praise ban structural for the reason recorded at
# `reflect_back`: a model asked for "something friendly" volunteers verdicts.
# None of these says anything about the answer, and none can misquote them.
# Measured with the `acknowledged` / `praise` / `stacked` signals in
# `scripts/compare_models.py`; numbers in docs/DECISIONS.md.

# The block above used to end with a worked example:
#
#   To acknowledge, repeat their own words flatly, then ask:
#     "Okay. So you used session management and concurrency control."
#
# In a live interview the model said that sentence verbatim to a candidate who
# had never said it. They had said "management concurrency" and "the session
# management manages those people per session"; the phrase attributed to them
# was ours. A fabricated quote in a hiring record is what `feedback/score.py`
# exists to prevent, and it arrived through the front door: the interviewer says
# it out loud and it lands in the transcript those quotes are checked against.
#
# The cause is the lesson recorded above at the contradiction block. A concrete
# example beats an abstract instruction, so forbidding the behaviour in prose
# while demonstrating it in a sample sentence let the sentence win. There is now
# no candidate phrase anywhere in this prompt that could be read as a quote:
# "Okay." and "Right." are safe to copy because they are the interviewer's own
# words and carry no claim about what anybody said.

# The pacing section used to carry its own "Say this / Not this" pair. It was
# cut to make room for the verdict block, which needed 104 tokens against 35
# free. The rules immediately above state the same thing in prose, and the
# failure this block was measured against is question-stacking, which the
# one-question example still demonstrates. Re-verified with compare_models on
# both models when it went.


#: The longest phrase worth reflecting back. Past this it stops being an
#: acknowledgement and becomes the interviewer reciting the candidate's answer
#: at them, which is slower to say than the pause it was covering.
MAX_REFLECTION_CHARS = 70


def reflect_back(term: str) -> str | None:
    """The acknowledgement spoken while the real answer is still being composed.

    **Built here, in code, from the candidate's own words, and never by a
    model.** That is the entire safety property. The praise ban works, and a
    filler generated by asking a model for something friendly to say is a route
    straight around it: in a scripted run this morning the model volunteered
    "that's a clear technical approach" about an answer that explained nothing.
    A template that can only ever emit their own noun phrase cannot do that.

    Returns None rather than reaching for something to say. If there is nothing
    concrete of theirs to repeat, the silence is better than an invention, and
    an interviewer that says "interesting" to fill a gap is the failure this is
    written against.
    """
    phrase = " ".join((term or "").split())
    if not phrase or len(phrase) > MAX_REFLECTION_CHARS:
        return None
    # No adjective, no verdict, no assessment. Their noun, and a full stop.
    return f"Okay. So you used {phrase}."


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _render_claims(claims: list[KeyClaim]) -> str:
    """Cross-section memory, clipped.

    Twelve claims of whatever length the judge felt like emitting is the same
    unbounded-prose problem the verdict block was given a limit for, and this
    block is older and larger. Measured against the 26 real claims in the local
    database: median 102 characters, ninetieth percentile 193, longest 237. At
    the top of that range this block alone runs to roughly 580 tokens inside an
    1800-token ceiling, which is why the ceiling has been nominal rather than
    real. Clipped at a length that leaves the median untouched.
    """
    if not claims:
        return ""
    lines = "\n".join(f"- {_clip(c.text, _MAX_CLAIM_CHARS)}" for c in claims)
    return f"""
WHAT THE CANDIDATE HAS ALREADY TOLD YOU
These are from earlier in this interview. You do not have the earlier
conversation in front of you, so rely on these rather than guessing. Do not ask
them to repeat something they have already covered, and do not attribute
anything to them that is not listed here.
{lines}
"""


def _render_let_go(term: "UnexplainedTerm", section: "Section | None" = None) -> str:
    """Two asks and no answer. Say so, and move on.

    **Out loud, not silently.** A candidate who has just been pushed twice on
    the same word and then hears the subject change without comment is left
    working out whether they failed something. Naming it costs one sentence and
    it is the difference between an interviewer that has decided to move on and
    one that appears to have given up on them.

    Modelled on the repair cap, which stops at two for the reason written there:
    a third attempt is an interrogation. Recording that the ground was never
    covered is more honest than grinding at somebody who has already said all
    they have.

    Not a verdict. It does not say the answer was poor, it says we will come
    back to it, which is true: it is on the record for the person reading the
    report.
    """
    _missing = (
        "say how they explained it"
        if section is not None and _is_about_talking_to_people(section)
        else "say what it does"
    )
    return f"""
=== MANDATORY: LET THIS ONE GO ===
You have asked twice about {term.term} and they have not been able to {_missing}.
Do not ask a third time. A third time is an interrogation.

Say, warmly and in one short sentence, that you will leave that one there and
come back to it if there is time. Do not say the answer was poor, do not say
they failed to explain it, and do not deliver any verdict.

Then ask your next question about something else on this topic.

Something close to: "That's alright, let's leave that one there for now."
=== END MANDATORY INSTRUCTION ===
"""


def _decided_question_early(contradictions: list[Contradiction]) -> str:
    """The half of the mandatory slot that stays ABOVE the section's material.

    A contradiction and only a contradiction. Its placement here is measured:
    below the seed questions it competed with a page of "here is what to ask
    about" and compliance fell to 27%, against 80% from up here.

    The unexplained-term half deliberately sits below the section now, and the
    two halves are split rather than moved together for exactly that reason:
    moving the block wholesale would have demoted the contradiction into the
    position already measured at 27%, trading a measured result for an
    unmeasured one without saying so.
    """
    return _render_contradictions(contradictions) if contradictions else ""


def _decided_question_late(
    contradictions: list[Contradiction],
    terms: "list[UnexplainedTerm]",
    let_go: "UnexplainedTerm | None" = None,
    section: "Section | None" = None,
) -> str:
    """The half that sits BELOW the section's purpose and its seed questions.

    **At most one block may decide this turn's question**, which is why this
    returns nothing when a contradiction has already taken the slot above. Two
    mandatory instructions pointing at different things are not twice as strong:
    the model resolves the conflict by choosing, which is the discretion these
    blocks exist to remove.

    A contradiction wins that contest. It is rarer, it decays if not raised in
    the moment, and an unexplained term will still be unexplained next turn: the
    judge re-reports it until the candidate actually explains it.

    **Why this half moved below the section.** It is mandatory, it opens with
    "do not choose your own question this turn", and it used to be read before
    the model was told what the section was for. Against a senior ML engineer
    almost every noun is an unexplained term, so it fired nearly every turn and
    the section's own questions never got asked: 1 of 4 in `applied_llm_depth`,
    4 of 12 across the interview. The interviewer asked him to define "LLM" and
    "fine tuning" while the choice of Sarvam-M over GPT-4o, the LoRA setup and
    the GPTQ tradeoff sat unused in the same prompt.
    """
    if contradictions:
        return ""
    # Closing one out comes before opening another. Two asks have just been
    # spent on it and the candidate is owed the sentence.
    if let_go is not None:
        return _render_let_go(let_go, section)
    if terms:
        return _render_unexplained_term(terms[0], section)
    return ""


#: Words naming the PEOPLE an answer is addressed to.
#:
#: **The audience, never the act.** "Explain" appears in a technical competency
#: ("able to explain the architecture and flows") and a communication one
#: ("able to explain technical concepts to non-technical stakeholders") alike,
#: so keying on the act fires on both. Only one of them names who is being
#: spoken to.
#:
#: This is not matching a term against a topic, which is the shape that has
#: misfired here three times. Nothing is compared to the term, and term
#: selection is untouched: this reads one fixed vocabulary out of the section's
#: own stated purpose, once, to choose between two fixed sentences. There is no
#: score and no threshold to tune.
#:
#: **It fails towards the technical sentence**, which is the current behaviour,
#: whenever it finds nothing. Its failure mode is a technical section whose
#: target_depth happens to say "stakeholder", which asks a mildly wrong question
#: about the candidate's own term rather than a question about the wrong thing.
_AUDIENCE_WORDS = (
    "stakeholder", "non-technical", "nontechnical", "partner", "audience",
    "executive", "cross-functional", "business team", "operations team",
)


_FORBID_MECHANISM_ALTERNATIVES = (
    "Do not ask instead for a trade-off, a metric, an alternative they considered,\n"
    "or the bet behind it. Every one of those accepts the term as real, and that is\n"
    "the thing not yet established."
)


def _is_about_talking_to_people(section: Section) -> bool:
    """Whether this competency is assessed on how they got something across."""
    text = (section.target_depth or "").lower()
    return any(word in text for word in _AUDIENCE_WORDS)


def _render_unexplained_term(term: "UnexplainedTerm", section: "Section | None" = None) -> str:
    """Name the term and supply the question, in the contradiction block's form.

    **This is a shape change, not an emphasis change.** The block this replaced
    said "coverage is insufficient, do not move on", which describes a state and
    leaves the model to work out what to do about it. It was measured at no
    effect. The contradiction block names a specific item and hands over the
    sentence to say, and was measured at 80% on the mini-tier model. Both
    numbers were already in this repo; nobody had put them next to each other.

    The second wording is the one that matters. Being asked once is not enough,
    because being asked once and deflected is the failure this exists for. A
    person says "you have said that twice and I still do not know what it does",
    and so does this.
    """
    quoted = _clip(term.said or term.term, 120)
    # Same term, same caps, same judge. What changes is the question asked about
    # it. In a competency assessed on explaining things to other people, "walk
    # me through how that works" is the technical-depth question wearing the
    # section's badge: it was asked five times in one such section and produced
    # no evidence about communicating at all.
    talking = section is not None and _is_about_talking_to_people(section)
    if talking:
        if term.times_raised >= 1:
            ask = f'"You have said {term.term} twice now. How did you put that to them?"'
            note = "You have asked once already and they have not said how they explained it."
        else:
            ask = f'"You said {term.term}. How did you explain that to them?"'
            note = "They used it as though the listener would follow it."
        forbid = (
            "Do not ask instead how it works, what it does, or what trade-off it\n"
            "involved. This topic is about how they got it across, not about the thing."
        )
    elif term.times_raised >= 1:
        # Eleven words plus the term. The version this replaced ran to
        # twenty-six, so the block enforcing brevity was itself the longest
        # sentence the interviewer was handed.
        ask = f'"You have said {term.term} twice now. What does it actually do?"'
        note = (
            "You have already asked about this once and they did not answer it. "
            "Say so plainly and without any edge, then ask again."
        )
        forbid = _FORBID_MECHANISM_ALTERNATIVES
    else:
        ask = f'"You said {term.term}. Walk me through how that actually works."'
        note = "They used it as though it settles the matter. It does not yet."
        forbid = _FORBID_MECHANISM_ALTERNATIVES

    return f"""
=== MANDATORY: THIS TURN'S QUESTION IS ALREADY DECIDED ===
Do not choose your own question this turn. They have used a term without saying
what it does. {note}

Say something close to this, adapted only for natural speech:

  {ask}

{forbid}
Ask only this, then stop and let them answer.

Their words: {quoted}
=== END MANDATORY INSTRUCTION ===
"""


def _render_contradictions(contradictions: list[Contradiction]) -> str:
    if not contradictions:
        return ""
    # Only the first is described, because only the first is instructed about.
    # Listing the rest duplicated the sentence above and was the single largest
    # block in the prompt when it fired.
    first = contradictions[0]
    lines = (
        f"- Earlier they said: {_clip(first.earlier_claim, 140)}\n"
        f"  Just now they said: {_clip(first.later_statement, 140)}"
    )
    # Instruction strength has been measured twice, and both times the wording
    # was the whole story.
    #
    #   v1 "raise it once, and only if it fits naturally" -> 0% on both models.
    #   v2 "your next question MUST be about it"          -> 80% mini, 27% gpt-4.1.
    #
    # Every v2 miss looked identical: the model asked the natural follow-up
    # instead. So v3 removes the alternative rather than adding emphasis — it
    # forbids the other question and supplies the sentence to say.
    return f"""
=== MANDATORY: THIS TURN'S QUESTION IS ALREADY DECIDED ===
Do not choose your own question this turn. Do not ask the natural follow-up to
what the candidate just said. However tempting it is, it is the wrong move
here. You have exactly one job this turn: ask about the inconsistency below.

Say something close to this, adapted only for natural speech:

  "Earlier you mentioned {first.earlier_claim} But just now you said
  {first.later_statement} How do those two fit together?"

Then stop and let them answer. Accept whatever they say without arguing, and do
not raise it again later.

Tone: genuinely curious, never prosecutorial. Do not use the word
"contradiction", do not tell them they contradicted themselves, do not accuse
them of anything, and do not deliver any verdict.
{lines}
=== END MANDATORY INSTRUCTION ===
"""


def _render_footing(has_substantive_answers: bool) -> str:
    """Tell the model where the conversation actually stands.

    The messages it receives include the last exchange from the previous
    section, so it has real material to continue from. What it must not do is
    infer a richer history than it can see.
    """
    if has_substantive_answers:
        return """\
YOU ARE ALREADY MID-INTERVIEW
You have been talking with this candidate for a while. Do not greet them, do not
introduce yourself again, and do not thank them for joining.

You can see the most recent exchange below. You CANNOT see the earlier parts of
this interview. Refer only to things that appear in the messages below or in the
notes above. Never say "you mentioned earlier" about something you cannot
actually see. If you are not certain they said it, they did not.
"""
    return """\
YOU ARE MID-INTERVIEW, BUT THEY HAVE NOT ANSWERED ANYTHING YET
The candidate has not given you a single substantive answer so far. They may
have declined, said very little, or be having audio trouble.

This matters: you have nothing to build on. Do NOT refer to anything they have
told you, do NOT say "you mentioned earlier", and do NOT thank them for sharing
something they did not share. Ask a plain, self-contained opening question about
this topic instead.
"""


def _render_refusal_guidance(consecutive_refusals: int) -> str:
    """How to behave when the candidate is declining to answer.

    A candidate saying no is disengaged, uncomfortable or done. Interrogating
    them harder is both ineffective and wrong, and this bot is not entitled to
    an answer.
    """
    if consecutive_refusals <= 0:
        return ""
    if consecutive_refusals == 1:
        return """
THEY JUST DECLINED TO ANSWER
Acknowledge it briefly and without pressure. One short sentence, no guilt, no
"I understand this may be sensitive, but". Then ask ONE different, easier
question on this same topic. Do not repeat the question they declined.
"""
    return """
THEY HAVE NOW DECLINED SEVERAL TIMES
Stop pursuing this topic entirely. Do not rephrase and try again.

Acknowledge it plainly and give them the choice: offer to move to a different
area, or to end the interview here if they would prefer. Be warm and completely
without pressure. They are allowed to decline, and it is not your place to
persuade them. Do not comment on what declining might mean.
"""


def _render_opening(
    blueprint: InterviewBlueprint,
    section: Section,
    time_of_day: str | None,
    first_name: str | None,
    already_greeted: bool = False,
) -> str:
    """Stage the opening across turns instead of packing it into one line.

    A live session opened with greeting, role statement and a deep multi-part
    question all in a single breath. The candidate's words were that it "starts
    banging". People need a moment of ordinary conversation before being asked
    to perform.

    Three stages, one per turn: hello, then orientation, then a soft connection.
    """
    greeting = f"Good {time_of_day}" if time_of_day else "Hello"
    name = first_name or "them"

    # The greeting is spoken straight to TTS the moment the candidate
    # connects, with no inference involved, so by the time the model is asked
    # for anything it has already said hello. Without this guard a stray turn
    # here would introduce the interviewer a second time.
    if section.turns_spent == 0 and not already_greeted:
        return f"""
RIGHT NOW: SAY HELLO AND INTRODUCE YOURSELF. NOTHING ELSE.
This is the very first thing the candidate hears. Do not start the interview.

Three short sentences, in this order:

1. Greet them by name, using the time of day. It is currently
   {time_of_day or "unclear"}, so: "{greeting}, {name}."
2. Say who you are: your name is {BOT_NAME}, and you are an AI interviewer.
   Do not skip this. They are about to be interviewed by a machine and they
   deserve to be told so in the first breath, not left to work it out or ask.
   Do not spell out or explain the name. Just give it, as a person would.
3. Ask how they are doing. Then STOP and wait.

Absolutely not yet: the role details, the agenda, their CV, their experience, or
any question about their work.

Something close to this:
  "{greeting}, {name}. I'm {BOT_NAME}, an AI interviewer. I'll be speaking with
  you today. How are you doing?"
"""

    if section.turns_spent == 1:
        return f"""
RIGHT NOW: ORIENT THEM, GENTLY
They have said hello back. Respond warmly to whatever they said, briefly and
like a person, not a script.

Then tell them what this conversation is, in one short sentence: it is an
interview for the {blueprint.role_title} role, and it will take about
{blueprint.total_duration_minutes} minutes. Then ask one easy, open question
about what they are working on at the moment.

Keep it short and unhurried. Do not list what you will cover. Do not mention
anything from their CV yet. Do not ask anything demanding.
"""

    return """
RIGHT NOW: ONE LIGHT CONNECTION, THEN WE BEGIN
Last warm-up turn. Respond to what they just said with genuine interest, then
ask ONE small, easy, human question about it.

Small talk, not an interview question. Aim for something a colleague would ask
over coffee:
  "Oh nice, how long have you been doing that?"
  "Is that mostly enterprise clients, or a mix?"
  "What kind of industries do you usually work with?"

This is the LAST thing that is forbidden and it matters: do not ask how they
DECIDE, PRIORITISE, CHOOSE, MEASURE or APPROACH anything. Those are interview
questions and they start on the next turn, not this one. If your question could
appear on an interview scorecard, it is the wrong question for right now.
"""


OFFER_TO_STOP = """
=== MANDATORY: THEY HAVE ASKED TO STOP ===
The candidate has just said they want to end the interview. Do not ask another
question. Do not ask why. Do not try to keep them talking, and do not suggest
that stopping has a cost.

Say two things, warmly and in no more than two short sentences:
  1. That is completely fine.
  2. Ask whether they would like to end now, or carry on.

Something close to: "Of course, that's completely fine. Would you like to end
here, or would you rather carry on?"

Offering rather than assuming is deliberate. If they meant something narrower,
this costs one sentence. Ending an interview they wanted to continue cannot be
undone.
"""


#: Their answer is "I don't know", though it reached us as "I know" or "Yes".
#: Written like the repair blocks: the natural alternatives are named and
#: removed. Live in `int_8e3db77a60a9`, a confirming "Yes." was answered with
#: "Sorry, my mistake" (the voice rules' line for mishearing) and the same
#: question again, a dozen times over.
ANSWER_IS_DONT_KNOW = """
=== THEIR ANSWER WAS "I DON'T KNOW" ===
Their last reply came through as "I know" or "Yes", but they have told you it
means "I don't know". That is a complete, honest answer to your question. You
did not mishear them and nobody made a mistake.

Say "No problem." and move on. Do not ask that question again, in the same
words or in different ones. Do not apologise, do not say "my mistake", and do
not check again what they said. Do not point out that their words differed.
"""


def _render_repair(kind, question: str) -> str:
    """The candidate could not hear or could not follow. Fix that, nothing else.

    Written the way the contradiction block is written, and for the same
    measured reason: an instruction that leaves the natural alternative
    available gets ignored in favour of it. A live run showed the interviewer
    answer "sorry, you cut out there" by dropping the question entirely and
    asking a different one, which loses the answer and the coverage with it.
    So the alternative is removed explicitly rather than discouraged.
    """
    from bot.brain.repair import RepairKind

    if kind is RepairKind.REPEAT:
        return f"""
=== MANDATORY: THEY DID NOT HEAR YOU ===
They could not hear your last question. Do not move on. Do not ask anything
new. Do not change the subject.

Say a brief acknowledgement, then ask THE SAME QUESTION AGAIN, in shorter and
plainer words but asking for exactly the same thing:

  "{question}"

They heard nothing, so the wording was never the problem. Keep what you were
asking for identical.
"""

    if kind is RepairKind.SIMPLIFY:
        return f"""
=== MANDATORY: THEY DID NOT FOLLOW THE QUESTION ===
They heard you but the question did not land. Do not move on. Do not ask
anything new. Do not repeat it word for word, that will not help.

Ask for the same thing in simpler, more concrete words. This was the question:

  "{question}"

Shorten it. Drop any abstraction. If it helps, name the kind of example you are
looking for. Ask for one thing only.
"""

    if kind is RepairKind.INTERRUPTED:
        # Measured before this block existed: after "Hmm." or "Sorry, one second."
        # spoken over a half-asked question, the model asked a new question six
        # times out of six. It sees its own half-sentence as its last turn and
        # reads the interjection as a reply, so "move on" is the natural move and
        # has to be removed, not discouraged.
        return f"""
=== MANDATORY: YOU WERE CUT OFF ===
The candidate spoke over you before you finished, so they never heard your whole
question. What they said was not an answer to it. Do not move on. Do not ask
anything new. Do not follow up on what they said.

All that reached them before you stopped was:

  "{question}"

Ask the question you were in the middle of asking again, in full, as one clear
question. Keep what it asks for exactly the same. If those words do not show
what you were going to ask, ask the question your plan calls for now. You may
open with a very short "Sorry, as I was saying," but nothing longer.
"""

    if kind is RepairKind.HOLD:
        return """
=== MANDATORY: THEY ASKED YOU TO WAIT ===
They asked for a moment before you had finished your question. Say only a short
"Of course, take your time." and stop there.

Do not ask anything. Do not repeat or finish the question yet. You will ask it
in full once they are back.
"""

    if kind is RepairKind.GIVE_UP:
        # Written for the same reason as the blocks above: the natural move here
        # is to ask the question once more or to ask about what they did say, and
        # both are wrong. The alternatives are removed, not discouraged.
        return """
=== MANDATORY: LEAVE THAT QUESTION ===
They could not hear your last question, even after you asked it twice. Do not
ask it again, in any words. Do not ask about anything they said since, because
they have not answered it.

Say one short sentence that you will leave that one, without apologising at
length. Then ask ONE different, simpler question on another subject in this
section.
"""

    if kind is RepairKind.PRESENCE:
        # Live in `int_39845b4313d4`, "Hello?" was read as an answer and got a
        # brand new question, so a candidate checking the line lost the
        # question they had been waiting to hear.
        return f"""
=== MANDATORY: THEY ARE CHECKING YOU CAN HEAR THEM ===
They said hello or asked if you are there. That is not an answer. Do not move
on. Do not ask anything new.

Say "Yes, I'm here and I can hear you." Then ask your last question again,
asking for exactly the same thing:

  "{question}"

If that was only a greeting rather than a question, ask the question your plan
calls for now.
"""

    if kind is RepairKind.SMALL_TALK:
        # Live in `int_39845b4313d4`, "How are you?" was answered with "I am an
        # AI interviewer named Mitra, here to assess your fit", then a new
        # question. A person answers in a word and carries on.
        return f"""
=== MANDATORY: THEY ASKED HOW YOU ARE ===
They asked how you are. Answer the way a person would, in a few words, for
example "I'm well, thank you." Do not describe yourself or your role. Do not
ask anything new.

Then ask your last question again, asking for exactly the same thing:

  "{question}"

If that was only a greeting rather than a question, ask the question your plan
calls for now.
"""

    if kind is RepairKind.UNCLEAR:
        # Live in `int_fb22eafe1628`, eight "I don't know"s arrived as "I know."
        # and each was followed by a new question. The two wrong moves are to
        # carry on as if "I know" were an answer, and to decide for them that
        # they said "I don't know". Both are removed, not discouraged.
        return f"""
=== MANDATORY: YOU MAY HAVE MISHEARD THEM ===
What came through was "I know", which does not answer your question, so you
probably misheard. They most likely said "I don't know". Do not move on. Do not
ask anything new. Do not follow up on "I know" as if it were an answer.

Check, in one short sentence, as your own hearing problem and not theirs.
Something close to: "Sorry, I didn't quite catch that. Did you say you don't
know?"

Ask; never state as fact what they said. Do not repeat or rephrase the
question yet. For reference only, your question was:

  "{question}"
"""

    if kind is RepairKind.ECHO:
        return """
=== MANDATORY: YOU ARE HEARING YOURSELF ===
Your own words are coming back through their microphone. Say, warmly and
briefly, that you can hear an echo and suggest headphones if they have them.
Then ask your question again. Do not treat the echo as something they said.
"""

    return ""


def _render_domain_language(blueprint) -> str:
    """Speak the way this field speaks.

    Every term here was copied out of the job description or the CV and checked
    against them, so using one cannot be a mistake about the field. The caution
    at the end is the important part: a term used wrongly costs more credibility
    than plain language ever would, and the candidate is the authority on their
    own work.
    """
    register = getattr(blueprint, "domain_language", None)
    if register is None or (not register.domain and not register.vocabulary):
        return ""

    lines = ["\nTHE LANGUAGE OF THIS FIELD"]
    if register.domain:
        lines.append(f"This is an interview in {register.domain}.")
    if register.vocabulary:
        lines.append(
            "These are the words this workplace uses, taken from their own job "
            "description and CV. Use them where they fit naturally:"
        )
        lines.append("  " + ", ".join(register.vocabulary))
    lines.append(
        "Do not reach for jargon beyond these. Plain language is always safe; a "
        "term used slightly wrongly tells the candidate you do not know their "
        "work. If they use a word you were not given, follow their lead."
    )
    return "\n".join(lines) + "\n"


def _render_red_flags(spec) -> str:
    """The employer's dealbreakers, shown while probing a competency.

    These are gathered explicitly in the clarification chat and were, until
    now, never shown to the live interviewer at all — only to the Milestone 1
    static prompt. An employer who names their dealbreakers and then never has
    them looked for has been quietly ignored.

    Not rendered in the opening or closing: they are things to notice while
    probing, and steering a greeting toward them is how a warm-up turns into an
    interrogation.
    """
    if not spec.red_flags:
        return ""
    lines = "\n".join(f"- {flag}" for flag in spec.red_flags)
    return f"""
THINGS WORTH NOTICING
The employer named these as dealbreakers. If one shows up, probe it gently and
factually. Ask for the specific example that would settle it. Never challenge
the candidate, never name the concern out loud, and never deliver a verdict.
You are gathering evidence for a human, not reaching a conclusion.
{lines}
"""


#: Carried claims are model prose inside a size-capped prompt, and were never
#: bounded at all. Measured against the 26 real claims in the local database:
#: median 102 characters, ninetieth percentile 193, longest 237.
_MAX_CLAIM_CHARS = 110


#: What the fourth turn onward asks for, one per turn, in order.
#:
#: **The third stage used to render identically on every turn from the third
#: onward, and that is what was eating the topic.** Its first instruction is
#: "the technique in their last answer IS your question", so it unpacked a term,
#: and then on the next turn it read the same instruction against an answer
#: about the same thing and unpacked it again. One measured topic asked about
#: LoRA five times running with a single one of those sanctioned by the brain.
#:
#: So the unpacking instruction fires once, on turn three, where it earns its
#: place: mechanism questions were 2 in 39 opportunities before it existed.
#: **This removes unsanctioned repeats, not unpacking.** The term block is
#: unchanged and still fires on the brain's own two-per-topic allowance, which
#: is where a term the candidate genuinely has not explained gets asked about.
#:
#: **One is named rather than four offered**, which is this repo's most
#: repeated measurement: a preference between available options was 0% and
#: naming the thing to say was 80%. Offering four also lets the model pick the
#: same one twice, which is the failure being fixed wearing a different hat.
#: Rotating by turn index makes consecutive turns different by construction.
#:
#: Ordered by what produced the best questioning in the transcripts. The measured
#: example is an evaluation topic that reached three distinct answers by asking
#: what was measured, then how it was validated, then what test established it.
_HARD_QUESTIONS = (
    "a number, and how they measured it.",
    "the trade-off they made, and what it cost.",
    "the thing that went wrong, and what they did about it.",
    "a decision on this they would still defend, and why.",
)


#: An employer-authored dealbreaker that OPENS with an inability names a thing
#: a question can establish. One that opens any other way describes a manner
#: spread across answers, and there is no question that gets at it.
_INABILITY_OPENERS = ("cannot ", "can not ", "can't ", "unable to ")

#: Words that appear in every competency description and so cannot tell two
#: apart. Kept small: an over-long list is a thumb on the scale.
_MATCH_STOPWORDS = frozenset(
    """they them their this that with have from when what about only 
    cannot able describe explain given real more most some such very well 
    into over than then were been""".split()
)


def _askable(flag: str) -> str | None:
    """What this dealbreaker requires established, or None if it is observable.

    **The distinction is real and the spec does not carry it.** `red_flags` is a
    list of strings and nothing in the clarification chat asks the employer
    which kind they are writing, so this is inferred, and inference over text is
    the shape that has misfired repeatedly in this repo. What makes this one
    different is that it is a prefix test on a closed employer-authored list
    rather than a score over prose: there is no threshold to tune, and it can be
    read against every dealbreaker in the fixtures by eye.

    Checked against all 14 in `ml_senior`, `pm_senior` and `sre_staff`, it calls
    3 askable and 11 observable, and every one of the 3 is genuinely a question:
    "cannot explain why specific evaluation metrics were chosen", "cannot
    describe a time they deployed or debugged their own ML service", "cannot
    name a product bet that failed".

    **What it gets wrong, and it is not a small list.**

    It keys on how the employer happened to phrase it rather than on what it
    means. "Struggles to explain why metrics were chosen" is the same
    dealbreaker and gets nothing.

    It misses compounds. "Describes frameworks and process but cannot name a
    single concrete decision they personally made" carries an askable half and
    is read as observable because of its opening clause.

    And it misses the hybrid, which is the most interesting case. "Avoids or
    deflects when asked about negative results" is a manner, so it reads as
    observable, but the manner cannot be seen without first asking about
    negative results. Treating it as observable means nobody asks; treating it
    as askable would have the interviewer put the question and the report treat
    having asked as having settled it. Neither is right and this does the first.

    **Every one of those errors is in the direction of rendering nothing**,
    which is the behaviour that exists today. Over-rendering is the expensive
    mistake: it announces the trap to the candidate.

    The inversion is the same fact as the classification. A dealbreaker that
    opens "cannot X" states what would rule someone out; "X" is what the topic
    has to establish, so stripping the opener is the whole transform.
    """
    text = flag.strip()
    low = text.lower()
    for opener in _INABILITY_OPENERS:
        if low.startswith(opener):
            return text[len(opener) :].strip().rstrip(".")
    return None


def _stems(text: str) -> set[str]:
    """Content words clipped to a common prefix.

    Whole-word matching put "cannot describe a time they deployed or debugged"
    on no competency at all, against a competency literally named "ML Service
    Deployment and Debugging", because `deployed` is not `deployment`. Five
    characters is enough to join those and short enough to need no stemmer.
    """
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return {w[:5] for w in words if len(w) > 3 and w not in _MATCH_STOPWORDS}


def _dealbreaker_for(blueprint: InterviewBlueprint, section: Section) -> str | None:
    """The askable dealbreaker this section owns, if it clearly owns one.

    Matched against the competency's own name, description and target depth,
    all employer- or blueprint-authored. **A tie or a weak best match means
    unowned, and an unowned dealbreaker renders nowhere** rather than being
    spread across every topic: a standard nobody can act on in this section is
    noise in six prompts instead of one, and the report already says plainly
    when a dealbreaker was never put.

    At most one per section, deliberately. Two would be a list, and a list of
    things to establish is how the carried-claims block turned into a standing
    to-do list and cost a week.
    """
    if section.kind != SectionKind.COMPETENCY or not section.competency_id:
        return None

    spec = blueprint.evaluation_spec
    plans = {p.competency_id: p for p in blueprint.competency_plans}
    profiles = {}
    for competency in spec.competencies:
        plan = plans.get(competency.id)
        profiles[competency.id] = _stems(
            f"{competency.name} {competency.description} "
            f"{plan.target_depth if plan else ''}"
        )

    best: tuple[int, str, str] | None = None
    runner_up = 0
    for flag in spec.red_flags:
        need = _askable(flag)
        if need is None:
            continue
        wanted = _stems(flag)
        ranked = sorted(
            ((len(wanted & words), cid) for cid, words in profiles.items()),
            reverse=True,
        )
        if not ranked:
            continue
        score, owner = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0
        if owner != section.competency_id or score < 2 or score == second:
            continue
        if best is None or score > best[0]:
            best, runner_up = (score, owner, need), second
    return best[2] if best else None


def _render_dealbreaker(blueprint: InterviewBlueprint, section: Section) -> str:
    """The employer's bar for this topic, stated as a standard and never a question.

    **It is deliberately neither mandatory nor advisory**, which is the trap
    every version of this block falls into. A mandatory block naming a specific
    item wins the turn — that is measured, at 80 to 100 percent — and it would
    win it from the seed question, trading a known gap for a new one. An
    advisory one is a preference between available options, measured at 0.

    So it is not a turn instruction at all. It attaches to `target_depth`, which
    is already the section's standard, renders in the same breath, and never
    says what to ask this turn. Nothing competes with the seed because this is
    not in the same category as the seed. `target_depth` has rendered every turn
    since the beginning and has never behaved like a to-do list, because it
    describes a bar rather than naming an item.

    Not rendered in the opening or closing: `_render_red_flags` already carries
    the same reasoning, and steering a warm-up toward a dealbreaker is how a
    greeting turns into an interrogation.
    """
    need = _dealbreaker_for(blueprint, section)
    if not need:
        return ""
    return (
        f"\nThe employer also rules out anyone who cannot {need}. Establish it "
        f"either way from their own work. It is a standard, not a question: "
        f"never read it out or say anything is being checked.\n"
    )


def _render_seed_question(section: Section) -> str:
    """One of the section's own questions, chosen by the turn index.

    **This block used to render all four and ask the model not to work through
    them in order.** That is a menu: a preference between available options,
    which is the shape this repo has measured at 0 percent compliance against 80
    percent for naming the thing to say. `_HARD_QUESTIONS` below fixed exactly
    this failure for the depth ramp, and its own comment says why — offering
    four also lets the model pick the same one twice, which is the failure being
    fixed wearing a different hat. The seed block was the same failure sitting
    forty lines above the fix.

    **What it moves is the floor, not the average**, measured ten runs a side on
    the competent candidate. Sections that spent a whole topic on a single
    subject halved, 14 of 60 to 7 of 60, and the worst competency went from 1.6
    distinct subjects to 2.2. The spread across competencies closed from 1.6-4.3
    to 2.2-3.5: the topics that were already good did not improve, the ones that
    collapsed stopped collapsing. Answers reached went 9.5 of 31 to 10.9, and
    answers the candidate had already given went 56 percent to 54.

    **It stops at the last seed rather than wrapping, and that was measured
    both ways.** Wrapping was tried first, on the reasoning that the back half
    of a section would otherwise have no subject at all: the ramp supplies a
    shape ("ask for a number") and never a thing to ask it about. It cost more
    than the empty back half does. Turn N and turn N+4 take the same seed, and
    exact repeated questions four turns apart went from 0 to 3 across ten runs,
    with the longest consecutive streak going 3 to 5, while the term block's own
    repeats stayed flat at 9 against 10.

    So a turn past the last seed gets no seed block at all: the ramp naming one
    demand, the section's target depth, and eight turns of the candidate's own
    words. The reasoning was that the subject would come from the window rather
    than from the plan.

    **That reasoning is wrong, and the measurement says so.** Split by whether a
    seed was rendered, mechanism questions on turns four and later went 19
    percent with a seed, 21 percent with a wrapped seed, and 6 percent with
    none, while menu questions on those same turns went 31, 29 and 54. Without a
    subject in front of it the model reaches for the ramp's demand instead of
    the window's content, and asks for the trade-off behind a term nobody has
    established: the exact failure `_probe_shape` was built to catch. The ramp
    alone is NOT enough there. It supplies a shape and the seed was supplying
    the subject, and that coupling was not visible until the seed was removed.

    The wrap cost 3 repeated questions four turns apart across ten runs. Losing
    the back half costs about twenty mechanism probes over the same ten. If
    these two are the only options, the wrap is the cheaper one.

    **The first turn takes the first seed deliberately, not by accident of
    indexing.** Both fixtures author their seeds broad to specific, which is the
    order the ramp already wants — widest question first, hardest later — so the
    two mechanisms agree rather than compete. A generated blueprint is not
    guaranteed to author them that way, and if one ever opens on its narrowest
    question this is the line to look at.

    A repair does not spend a turn, so it cannot advance the seed. That is
    right: the candidate did not hear the question, and moving to the next
    subject would throw it away.

    **The guard exists because naming one question got its content handed back
    as candidate speech.** These seeds are written from the CV, so the first
    version of this block produced "You said you fine-tuned Sarvam-M 24B" before
    the candidate had mentioned either word. Unsupported attributions went from
    0.5 an interview to 0.9, and the coupling was not in doubt: 9 of 9 matched a
    seed in that section and 8 of 9 matched the seed live on that exact turn,
    against 3 of 5 and 1 of 5 with all four rendered.

    **Three rules already forbade this and none of them reached it, and one of
    them licensed it.** `VOICE_RULES` says plainly that if you did not hear them
    say it you may not say they said it, but it says so while describing how to
    acknowledge an answer, and this failure is in how a question is introduced.
    `_render_claims` binds attribution to the claims list. And `_render_footing`
    says "refer only to things that appear in the messages below or in the notes
    above" — the seed question IS one of the notes above, so the rule nearest to
    this block reads as permission for exactly the thing being forbidden. That
    clause is not touched here, because changing it would mean measuring two
    things at once; if this guard underperforms, that clause is the next
    suspect.

    So the guard states the fact rather than repeating the principle, and names
    the three openers rather than the behaviour: "you said", "you mentioned",
    "you told me". Scoped to this question only, because the term block's own
    supplied sentence legitimately begins "You said you used X" about a term the
    candidate really did use, and a blanket ban would take the mechanism probe
    down with it.

    **No worked example, deliberately.** The last fabricated quote in this repo
    arrived through a sample sentence in exactly this position: the block
    forbade the behaviour in prose and demonstrated it in an example, and the
    example won. Nothing here contains a phrase that could be read back as
    something a candidate said.

    This is a mandatory block. Re-measure with
    `scripts/compare_models.py --scenarios competent` after editing (CLAUDE.md).
    """
    seeds = section.seed_questions
    if section.turns_spent >= len(seeds):
        return ""
    return f"""\
The question for this turn. It is drawn from their CV, not from this
conversation, so nothing in it is something they have told you. Do not introduce
any part of it with "you said", "you mentioned" or "you told me". Put it in your
own words, adapted to what they have actually said, and do not read it out
verbatim:
- {seeds[section.turns_spent]}

If it is written as two questions joined together, ask only ONE half. Pick the
part that fits what they have just told you and drop the rest.
"""


def _render_depth_ramp(turns_spent: int) -> str:
    """Open a topic broadly; save the hard questions for the follow-ups.

    Every seed question in a generated blueprint is written at full depth, and
    the model will happily lead with the hardest one. Landing "how did you
    assess the model's performance and what trade-offs did you make" as the
    opening line of a topic gives the candidate nowhere to stand.

    Both later stages were rewritten after ten real interviews, and both changes
    are the same lesson the contradiction block records above.

    **Turn one was v1 phrasing.** "Follow their thread rather than switching to
    a different question" states a preference between two available options,
    which is the exact shape measured at 0% compliance, and it sat directly
    below a bulleted list of the other questions to ask. It now forbids the
    alternative instead of preferring the right one.

    **It no longer nominates terms, and that is F2 from the follow-up audit.**
    Stages one and two used to say "that term IS your question" and supply the
    term block's own sentence. That made the ramp a fourth mandatory block,
    outside the arbitration the other three share, doing the term block's job
    with none of its accounting: no raise count, no per-section allowance, no
    interview budget, no let-go.

    Read from the ten-run arm rather than argued from the code. Of 85
    probe-shaped questions, 33 fell at turns one and two where the ramp
    nominated, and 22 of those named a term the judge had open anyway, so the
    term block would have asked them. The ramp's unique contribution was the
    remaining **11**: probes about terms the judge never flagged as unexplained.

    That is what settled the shape. The alternative was to let the ramp keep
    nominating and spend the same allowance, which preserves more but sanctions
    the wrong thing: the judge is the authority on what went unexplained, and a
    ramp nomination is a second opinion with no evidence behind it. Giving it
    budget would spend a candidate's patience on terms the judge did not think
    needed explaining.

    **The stage-three prohibition was half wrong and the half is gone.** It said
    "Do not unpack another, and do not ask again about anything you have already
    asked about here", and was measured violated on a quarter of the turns it
    governs. Most of that was not disobedience: 27 of the 40 probes at those
    turns name a term the judge had open, which is the term block firing, and
    the term block is mandatory and rendered after this one. The prohibition was
    contradicting a block that outranks it. The anti-repetition half is true and
    stays.

    **The hard part was a closed menu of four, and none of the four fits a term
    the candidate never explained.** Measured over the six substantial
    interviews in `interviewer.db`, all with a candidate answering in confident
    jargon: of 39 interviewer questions that followed an answer, **2 asked what
    a term meant and 10 asked for a trade-off, a metric or the bet behind it.**
    Against "we have implemented a session management concurrency, which allows
    multiple users to make the payments at the same time" the interviewer asked
    *"What was the trade-off you considered when implementing session management
    concurrency for that payment gateway?"* — it quoted the term straight back
    and never established there was anything behind it. The employer had asked
    for depth "not just at a conceptual level"; the report scored 3.0 out of 5
    on conceptual evidence.

    So unpacking is a fifth option that takes precedence in that one case. The
    other four are unchanged.

    This is a mandatory block. Re-measure with
    `scripts/compare_models.py --scenarios jargon thin_answer` after editing it
    (CLAUDE.md).
    """
    if turns_spent == 0:
        return """\
START THIS TOPIC BROADLY
This is your first question on this topic. Ask the widest, easiest version of
it and let the candidate choose their own example. Do not lead with the hardest
question, do not ask a multi-part question, and do not ask about metrics,
trade-offs or failures yet. You have not given them anything to hang those on.
Once they have picked an example, go deeper on it in your follow-ups.

Twenty words, like every reply. The rest of this topic copies this question, so
a long one here makes them all long.

"""
    if turns_spent == 1:
        return """\
=== MANDATORY: STAY ON THE ANSWER THEY JUST GAVE ===
Do not ask a new question, and do not move to any question listed below,
however well one fits.

Go one layer into what they just said: what they personally decided, why, and
what happened.

"""
    if turns_spent == 2:
        return """\
NOW GET TO THE HARD PART
Ask ONE demanding question, never a list.

Pick ONE: the trade-off they made, the thing that went wrong, a decision they
would defend, or a number and how it was measured.

"""

    # Fourth turn onward. The block above is deliberately not repeated: see
    # `_HARD_QUESTIONS`.
    wanted = _HARD_QUESTIONS[(turns_spent - 3) % len(_HARD_QUESTIONS)]
    return f"""\
NOW GET TO THE HARD PART
Ask ONE demanding question, never a list.

Do not ask again about anything you have already asked about here.

This turn, ask for {wanted}

"""


def _render_session_clock(
    session_remaining_seconds: float | None, scheduled_minutes: int | None
) -> str:
    """The whole interview's clock, stated every turn.

    A fact, not a menu: how many minutes the employer scheduled and how many are left.
    The interviewer paces the remaining topics to it and does not read it out unless
    asked, because the candidate was told the length once at the start.
    """
    if session_remaining_seconds is None or scheduled_minutes is None:
        return ""
    remaining_min = max(0, round(session_remaining_seconds / 60))
    if session_remaining_seconds <= 0:
        return f"TIME: {scheduled_minutes} min is up, wind down to the close.\n"
    return f"TIME: {scheduled_minutes} min total, {remaining_min} left. Pace to it, don't read it out.\n"


def _render_time_pressure(remaining_seconds: float, is_last_competency: bool) -> str:
    if remaining_seconds <= 45:
        tail = (
            " This is the last competency, so begin steering toward a natural close."
            if is_last_competency
            else " Aim to land this topic within your next question or two."
        )
        return f"\nTIME\nYou are near the end of the time set aside for this topic.{tail}\n"
    return ""


def render_section_prompt(
    *,
    blueprint: InterviewBlueprint,
    section: Section,
    carried_claims: list[KeyClaim],
    unprobed_contradictions: list[Contradiction],
    unexplained_terms: "list[UnexplainedTerm]" = (),
    term_to_let_go: "UnexplainedTerm | None" = None,
    remaining_seconds: float,
    is_last_competency: bool,
    has_substantive_answers: bool = True,
    consecutive_refusals: int = 0,
    time_of_day: str | None = None,
    pending_repair: "RepairKind | None" = None,
    question_to_repair: str = "",
    answer_is_dont_know: bool = False,
    offer_to_stop: bool = False,
    withdrew: bool = False,
    final_closing_turn: bool = False,
    already_greeted: bool = False,
    session_remaining_seconds: float | None = None,
    session_scheduled_minutes: int | None = None,
) -> str:
    """Build the system instruction for the section currently in progress."""
    spec = blueprint.evaluation_spec
    candidate_first_name = (
        blueprint.candidate_name.split()[0] if blueprint.candidate_name else None
    )
    header = f"""\
Your name is {BOT_NAME}. You are an AI interviewer conducting a voice interview
for the role of {spec.role_title}. If the candidate asks who or what you are,
say so plainly and without evasion.

Seniority: {spec.seniority}. Expected experience: {spec.experience_expectation}.
Your manner should be {spec.tone}.
{_render_session_clock(session_remaining_seconds, session_scheduled_minutes)}
{VOICE_RULES}
{_render_domain_language(blueprint)}"""

    # Leaving outranks everything, including a repair. Somebody who has asked
    # to stop must not be asked to repeat themselves.
    if offer_to_stop:
        return OFFER_TO_STOP + "\n" + header

    # A repair outranks every other instruction. Nothing else this turn is
    # worth doing if they could not hear the question.
    if pending_repair is not None and question_to_repair:
        repair = _render_repair(pending_repair, question_to_repair)
        if repair:
            return repair + "\n" + header

    if section.kind == SectionKind.OPENING:
        body = _render_opening(
            blueprint, section, time_of_day, candidate_first_name, already_greeted
        )
    elif section.kind == SectionKind.CLOSING and withdrew:
        body = """
RIGHT NOW: CLOSING, BECAUSE THEY ASKED TO STOP
They have chosen to end the interview. Thank them for their time in one or two
short sentences and say goodbye warmly. Nothing else.

Do not ask if they have questions: they asked to leave, and inviting more talk
is not respecting that. Do not ask why. Do not say anything about the outcome,
about what happens next, or about how much was covered.
"""

    # Two closing turns, each told which one it is. They used to share one
    # instruction ("invite any brief question ... and close warmly"), so the model
    # could not know whether to invite or to leave. Run against the real model it
    # did both wrong: once it invited questions with "please ask now." and the call
    # hung up on the candidate, because nothing marked that as an invitation; and
    # a second turn that invited again would hit the turn cap and end with no
    # goodbye at all.
    elif section.kind == SectionKind.CLOSING and final_closing_turn:
        body = """
RIGHT NOW: CLOSING, YOUR LAST TURN
This is the last thing you will say. The call ends as soon as you finish
speaking, so nothing you ask now can be answered.

If the candidate asked something, answer it briefly. If it is something you
cannot know, such as the outcome, the timeline, salary or next steps, say
plainly that the hiring team will follow up on that. Then thank them and say
goodbye warmly.

Do not ask anything. Do not end with a question, not even "anything else?".
Do not start a new topic. Do not evaluate their performance or hint at an
outcome. A human makes that decision after reviewing the conversation.
"""

    elif section.kind == SectionKind.CLOSING:
        body = """
RIGHT NOW: CLOSING
The interview is ending. Thank the candidate for their time and invite any brief
question they may have about the role or the process. Make the invitation an
actual question and finish your turn on it, for example: "Before we finish, do
you have any questions for me?" Do not say goodbye yet; you will get one more
turn to answer them and close.

Do not start a new topic. Do not evaluate their performance or hint at an
outcome. A human makes that decision after reviewing the conversation.
"""
    else:
        # Two failures shaped this block, and the fix for the first caused the
        # second.
        #
        # 1. Without it, the model re-greeted at every section boundary:
        #    "Thanks for joining today" arriving twenty minutes in.
        # 2. The fix said "continue naturally from what they have been telling
        #    you" — but at a boundary the model cannot see earlier sections, so
        #    it invented what it could not see: "you mentioned earlier that you
        #    shaped a key product strategy", said to a candidate whose only
        #    words had been "I don't want to."
        #
        # So state the truth instead of asking for a manner. `_render_footing`
        # tells it whether anything has actually been said.
        body = f"""
{_render_footing(has_substantive_answers)}
RIGHT NOW: {section.name.upper()}
This part of the interview is about one thing only: {section.name}.

What a sufficient answer looks like at this seniority:
{section.target_depth}
{_render_dealbreaker(blueprint, section)}
{_render_seed_question(section)}
{_render_depth_ramp(section.turns_spent)}
Stay on this topic until you are moved on. Do not announce section changes or
say things like "let's move to the next area" unless you are asked to close.
"""

    # Interviewing guidance is about how to probe a competency — "push past
    # frameworks to decisions", "probe trade-offs". Injecting it into the
    # greeting pulls the model straight back toward the deep questions the
    # warm-up exists to hold off.
    guidance = (
        "\n".join(f"- {line}" for line in blueprint.interviewing_guidance)
        if section.kind == SectionKind.COMPETENCY
        else ""
    )
    # **Both headings state what the material IS, because the old ones did not
    # and the model filled the gap.** This block used to open "ABOUT THIS
    # CANDIDATE" and list "Claims from their CV worth testing", and the
    # interviewer read the list back as speech: "You said you built six
    # production voice agents on LiveKit Agents", at the second substantive turn
    # of an interview whose candidate had said only that they were building an
    # AI interviewer. That sentence is `claims_to_verify[0]`, near enough word
    # for word. Seven of these across six of ten measured interviews, every one
    # traceable here or to `candidate_summary`.
    #
    # "Claims" is the word that does the damage. A claim is something somebody
    # claimed, so the model supplies the claimant, and the only person available
    # is the candidate. The heading was inviting the failure that the prose
    # underneath would have had to forbid.
    #
    # **A heading rather than a guard sentence, for two reasons.** The prompt is
    # already four mandatory blocks deep and over its own token bound on the
    # `ml_senior` fixture, and this block renders on every turn of every section
    # including the opening, so a paragraph here is the most expensive place in
    # the prompt to put one. And a heading is a label on the data rather than an
    # instruction about behaviour: it is read every time the data is read, and
    # it cannot lose a turn to the four blocks that name a specific thing to say.
    #
    # The named openers are kept from the seed guard, which is the one component
    # with direct evidence behind it (0.90 to 0.40 attributions), folded into the
    # line that was already there rather than added as a new one.
    candidate_context = ""
    if blueprint.candidate_summary:
        candidate_context = (
            f"\nTHEIR CV, NOT THEIR WORDS\n"
            f"{blueprint.candidate_summary}\n"
        )
        if blueprint.claims_to_verify:
            claims = "\n".join(f"- {c.claim}" for c in blueprint.claims_to_verify)
            candidate_context += (
                f"\nAlso the CV. Never say \"you said\" or \"you mentioned\" about any "
                f"of it:\n{claims}\n"
            )

    scope = f"""
STAYING IN SCOPE
You are interviewing for {spec.role_title}, and only that role. If the candidate
drifts somewhere unrelated, acknowledge what they said in a sentence, then bring
it back to the topic above. If asked what role this is for, say plainly:
{spec.role_title}. Never agree that this interview is for a different role, and
never invent one.
"""

    # The contradiction block sits immediately after the header, ahead of the
    # section body and the seed questions. Buried below them it competed with a
    # page of "here is what to ask about" and lost — measured at 27% compliance
    # on gpt-4.1. Salience is part of instruction strength.
    #
    # The unexplained-term block used to sit up there with it and now sits after
    # `body`, so the model reads what the section is for before it is told what
    # to ask. Same reason, opposite direction: up there it was winning against
    # the section's own questions every turn.
    #
    # **The claims block renders only before the candidate has answered in this
    # section**, which is the only place it earns its keep. It exists to stop
    # the model inventing what the candidate said earlier, and that risk is
    # entirely at a section boundary, where the verbatim window holds nothing of
    # this section (see `_section_messages`). Once a real answer is in the
    # window, the model has the candidate's own words and does not need a list.
    #
    # Left rendering every turn, it stopped being memory and became a standing
    # list of things to ask about. It is frozen for the whole of a section, so
    # the depth ramp's "did their last answer name a technique without saying
    # what it does" reads off the list instead of the answer — the same term,
    # every turn, carried in from the section before. Measured on the stored
    # turn that produced the third repetition, five samples per cell:
    #
    #                       own turns in window   own turns removed
    #   claims rendered     5/5 repeat the probe   0/5 repeat
    #   claims removed      0/5 repeat             0/5 repeat
    #
    # The cost is accepted and is real: late in a section the model can no
    # longer call back to an earlier competency.
    #
    # **`turns_spent == 0`, not "the section has no turns yet".** They differ
    # when the interviewer has spoken and the candidate has not answered — a
    # silence nudge, or a repair. There the window holds only the interviewer's
    # own question, the model still has none of the candidate's words for this
    # section, and the confabulation risk is exactly what it was at the
    # boundary. `turns_spent` counts candidate answers, so it stays 0 through
    # that and the block keeps rendering.
    #
    # Non-competency sections are untouched. The depth ramp is competency-only,
    # so the failure cannot occur in the opening or the close, and the close is
    # where a callback is worth most.
    claims_still_needed = (
        section.kind != SectionKind.COMPETENCY or section.turns_spent == 0
    )
    return "".join(
        [
            header,
            _render_refusal_guidance(consecutive_refusals),
            ANSWER_IS_DONT_KNOW if answer_is_dont_know else "",
            _decided_question_early(unprobed_contradictions),
            candidate_context,
            body,
            _decided_question_late(
                unprobed_contradictions, unexplained_terms, term_to_let_go, section
            ),
            _render_claims(carried_claims) if claims_still_needed else "",
            _render_time_pressure(remaining_seconds, is_last_competency),
            f"\nHOW TO INTERVIEW\n{guidance}\n" if guidance else "",
            _render_red_flags(spec) if section.kind == SectionKind.COMPETENCY else "",
            scope,
        ]
    )
