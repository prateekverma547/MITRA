"""Product identity, in one place.

The name reaches the candidate through three separate channels — the spoken
introduction, the Daily participant list, and the join page — and they must
agree. A bot that calls itself one thing and appears in the call as another is
unsettling in a context where someone is already nervous.

Imported by `bot/brain/`, which must stay free of Pipecat and of framework
imports generally, so this module holds constants and nothing else.
"""

#: What the interviewer calls itself, out loud and on screen.
BOT_NAME = "Mitra"

#: Expanded once on the join page, never spoken. Saying an acronym's expansion
#: aloud in a greeting sounds like a corporate video.
BOT_FULL_NAME = "Machine Interviewer for Talent Review & Assessment"

#: Shown to candidates before they join.
PRODUCT_TAGLINE = "AI-conducted interviews, reviewed by people."

#: Appended to every prompt whose output a person reads: the clarification chat,
#: the interview questions, the spoken turns, the feedback report.
#:
#: Most of the words on screen are written by a model, not by us, so cleaning the
#: hardcoded copy alone leaves the give-aways in the part people actually read.
#: The em dash is the strongest of those tells. Kept here, in one place, for the
#: same reason the name is: the product has one voice, and it must not drift
#: between the panel, the interview and the report.
PROSE_STYLE = (
    "WRITING STYLE. Never use an em dash or an en dash (— or –) in anything you "
    "write. Use a comma, a full stop, a colon, or split the sentence in two. "
    "Write the way a person writes, in plain direct sentences."
)
