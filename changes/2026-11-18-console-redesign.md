## 2026-11-18: console redesign, and an Appinventiv footer on every page

**What**

The console has its own look now, made with the `frontend-design` skill:
- MITRA's navy and the blue of its M mark.
- Newsreader for headings and anything you read as evidence, and Instrument
  Sans for controls. Both are served from our own build, not a font CDN.
- A quiet paper side rail.
- On the welcome page, a typeset example of how a score appears with the
  quote behind it.
- Every page ends with "Built by" and the Appinventiv logo, recoloured to the
  theme so it sits on the page rather than on a white box.

Every screen also works in dark mode and on a phone.

**Why**

The user asked for a premium feel. Research on what reads as premium pointed
the same way each time: restraint in colour, typography as the brand, a
disciplined type scale, and crafted states rather than decoration.

**How**

- `src/shell/shell.css`: rewritten from one set of tokens.
  - Status is shown as a dot and a word, never colour alone.
  - Home shows one line of figures instead of tiles.
  - Tables have fixed columns.
- `src/assets/`: theme-matched logo files.
  - `appinventiv-light.png` and `-dark.png` are made from the supplied JPG,
    with the white background removed and the text recoloured to the theme.
  - `mitra-mark.png` is cut from MITRA's logo.
- `Brand.tsx`, `Footer.tsx` (new).
- Rewritten screens: welcome, sign-in, the shell and home, and the job request
  list, form and request page. The request page shows the conversation as a
  transcript and "What MITRA understood" as a brief, with MITRA's assumptions
  in highlighter.
- HR's home names business units with no requests in one line instead of
  showing empty sections.
- The design was checked from screenshots in Chrome: light and dark, desktop
  and phone. Two bugs were found and fixed that way:
  - The new request page scrolled sideways.
  - The dark-mode highlighter turned muddy.

**Confirm**

```bash
cd frontend/console && npm install && npm run build
cd backend && uv run pytest -q          # 853 passed
```
