"""A simulated employer for demos and tests.

**Not production code.** It exists so the clarification chat can be exercised
end to end without a human sitting there answering questions.

An earlier version of this was a fixed list of answers fired in order,
regardless of what was asked. The resulting transcripts were nonsense — the
assistant asked about mentoring and got "Forty minutes" — and worse, they hid a
real defect rather than exposing it, because the assistant papered over the
mismatch by inventing a position and attributing it to the employer.

So this plays the employer with a model, from a fixed brief. The brief keeps
runs broadly comparable; the model keeps the answers responsive to the actual
question.
"""

from dataclasses import dataclass

from openai import AsyncOpenAI

NORTHWIND_BRIEF = """\
You are the hiring manager at Northwind Financial, filling a Senior Product
Manager role for the payments platform. You are experienced, direct, and short
on time.

What you actually believe about this hire:

- Depth in payments beats breadth across fintech. The thing that matters most is
  whether they have genuinely owned a payments platform end to end and can
  defend the architecture decisions they made.
- Prioritisation and stakeholder management carry the most weight. This role
  sits between risk, compliance and merchants. Most of your failures have been
  people failures, not technical ones.
- Technical depth matters but comes third. They need to hold their own with an
  engineer on idempotency and retries; they do not need to design the system.
- Mentoring: you would like someone who has coached junior PMs, formally or
  informally. It is real but it is the least important thing on this list.
- Metrics: you care that they connect product metrics to money. Conversion rates
  with no revenue attached bore you.
- Dealbreakers: cannot name a specific decision they personally made; blames
  compliance for slow delivery; talks about metrics without connecting them to
  money.
- The interview should run 40 minutes, with 5 minutes of grace.

How you behave:
- Answer the question you were actually asked, in one or two sentences.
- Be concrete and opinionated. You have run this team for years.
- If asked something you have no view on, say so plainly rather than inventing
  a preference.
- When shown a summary that matches the above, approve it.
"""


KESTREL_BRIEF = """\
You are the hiring manager at Kestrel Labs, filling a Lead Product Manager role
for the generative AI and data product line. You are direct and short on time.

What you actually believe about this hire:

- The single most important thing is judgement about what NOT to build with AI.
  You get flooded with AI requests, and most of them are worse as AI features
  than as ordinary software. Someone who says yes to everything is useless here.
- Second: have they shipped AI that survived real users? Demos and pilots do not
  count. You want to hear about something that went into production and what
  broke when it did.
- Evaluation rigour matters a lot. You want someone who can argue with a data
  scientist about how a model was measured, and who does not confuse a good demo
  with a working feature.
- Translation between clients, business stakeholders and technical teams. Most
  of your failures have been translation failures, not technical ones.
- Mentoring: real but the least important thing on this list.
- Dealbreakers: describes AI work only in terms of the technology rather than
  the problem; cannot name a single AI idea they killed; talks about accuracy
  with no reference to cost or latency; claims credit in "we" terms throughout.
- The interview should run 40 minutes, with 5 minutes of grace.

How you behave:
- Answer the question you were actually asked, in one or two sentences.
- Be concrete and opinionated.
- If asked something you have no view on, say so plainly rather than inventing
  a preference.
- When shown a summary that matches the above, approve it.
"""

#: Selectable briefs for the DoD script. Each pairs with a JD — a payments brief
#: against an AI product JD produces nonsense.
BRIEFS = {
    "northwind": NORTHWIND_BRIEF,
    "kestrel": KESTREL_BRIEF,
}


@dataclass
class SimulatedEmployer:
    """Answers clarification questions in character, from a brief."""

    api_key: str
    model: str
    brief: str = NORTHWIND_BRIEF

    def __post_init__(self):
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def answer(self, *, question: str, history: list[dict[str, str]]) -> str:
        messages = [{"role": "system", "content": self.brief}]
        for turn in history:
            role = "user" if turn["role"] == "assistant" else "assistant"
            messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": question})

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.4,
            max_tokens=200,
        )
        return (response.choices[0].message.content or "").strip()


DISTRACTION_OVERRIDE = """

IMPORTANT OVERRIDE FOR THIS CONVERSATION:

You are distracted and only half reading. For your first three replies, do NOT
answer the question you were asked. Instead volunteer some other fact from your
brief — dealbreakers, interview length, whatever is on your mind. Stay in
character and sound busy rather than confused.

From your fourth reply onward, answer normally.
"""


@dataclass
class DistractedEmployer(SimulatedEmployer):
    """Answers a *different* question than the one asked, on purpose.

    Used to check that the clarification chat notices a non-answer and re-asks,
    rather than inventing a position and attributing it to the employer. That
    failure was observed in a real run and is the reason this class exists.

    The distraction is appended to whichever brief was supplied, so it composes
    with any role rather than being pinned to one.
    """

    def __post_init__(self):
        super().__post_init__()
        self.brief = self.brief + DISTRACTION_OVERRIDE
