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
