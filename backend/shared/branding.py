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

#: The logo, served from `frontend/assets/`. Named here rather than written into
#: each page for the same reason the name is: one file, one URL, every screen
#: agrees. Swapping the artwork is dropping a new file in that folder and
#: changing this line.
#:
#: Two things follow from what the artwork is. It is the full lockup, mark and
#: wordmark and expansion together, so a page that shows it must not print the
#: name beside it as well. And it is the transparent cut, so it sits on the page
#: rather than inside a pale rectangle: the version with the background baked in
#: reads as a grey box on any surface that is not exactly its own. That original
#: is kept beside it as `logoMitra.webp`.
LOGO_FILE = "TrasparentLogo.png"
LOGO_URL = f"/assets/{LOGO_FILE}"

#: Square, cut from the mark alone. The lockup is close to 3:1, which in a
#: browser tab shrinks to an unreadable sliver.
FAVICON_URL = "/assets/favicon.png"

#: Who built the product, as distinct from what the interviewer is called.
#:
#: **Kept apart from `BOT_NAME` on purpose.** Mitra is the thing the candidate
#: talks to and is introduced by name in the greeting; Appinventiv is the company
#: behind it and is never spoken aloud, never part of the interviewer's persona,
#: and never reaches a prompt. A candidate being interviewed by "Appinventiv"
#: would be a different product from one being interviewed by Mitra.
COMPANY_NAME = "Appinventiv"

#: How the company is credited on screen. One phrase, here, so the panel and the
#: candidate pages cannot end up wording it differently.
COMPANY_CREDIT = f"by {COMPANY_NAME}"

#: The company lockup, served from `frontend/assets/` like the product logo.
#:
#: **Dark ink on transparency, which decides how it is used.** The artwork is a
#: near-black wordmark with one blue element, cut out cleanly: 91 percent of the
#: file is fully transparent and the corners carry no matte, so it sits on a
#: light surface as it is. On the dark call screen it would be almost invisible,
#: so the page paints it white with a CSS filter rather than shipping a second
#: file. That costs the blue, which is the honest trade: a two colour mark
#: rendered in one. A supplied light-on-dark cut would be better and is the only
#: thing that would bring the blue back.
COMPANY_LOGO_FILE = "appinventiv-logo.png"
COMPANY_LOGO_URL = f"/assets/{COMPANY_LOGO_FILE}"

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
