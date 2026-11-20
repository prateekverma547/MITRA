// When the interview page is not the thing in front of the candidate.
//
// Records one fact: the page stopped being in view, and for how long. It is not a proctoring
// system and must never read as one. It catches the same-device case and nothing else: a phone,
// a second monitor with this window still focused, or a person in the room are all invisible to
// it, and reading a second window does not even need focus. Anyone drawing a conclusion from this
// data should know that first.
//
// There is no score, no threshold and no percentage, here or at the far end. What gets stored is a
// count and a duration for a person to read. Nothing here reaches the interviewer: no frame, no
// prompt, no transcript entry. The bot does not know and must not be told.

/**
 * Watch for the page leaving view. `onAbsence({ at_seconds, seconds, kind })` is called as each
 * absence ends; `onNote(seconds)` lets the page say so once, plainly.
 * Returns `{ start(), stop() }`.
 */
export function createFocusWatch({ onAbsence, onNote }) {
  let watching = false;
  let startedAt = 0;
  let awaySince = null;
  let awayKind = null;

  // visibilitychange and blur BOTH fire on an ordinary tab switch, so they are folded into one away
  // state rather than counted separately. Two events, one absence.
  const isAway = () => document.hidden || !document.hasFocus();

  function begin(kind) {
    if (!watching || awaySince !== null) return;
    awaySince = Date.now();
    awayKind = kind;
  }

  function end() {
    if (!watching || awaySince === null || awayKind === null) return;
    const seconds = (Date.now() - awaySince) / 1000;
    const at = Math.max(0, (awaySince - startedAt) / 1000);
    const kind = awayKind;
    awaySince = null;
    awayKind = null;
    if (seconds <= 0) return;
    onAbsence({ at_seconds: at, seconds, kind });
    onNote(seconds);
  }

  const onVisibility = () => {
    if (document.hidden) begin("hidden");
    else if (!isAway()) end();
  };
  const onBlur = () => begin("blur");
  const onFocus = () => {
    if (!isAway()) end();
  };

  return {
    // Started when the call starts, not when the page loads. The device check asks for camera and
    // microphone permission, and that dialog blurs the window on every browser, so watching earlier
    // would record an absence on every interview and mean nothing by it.
    start() {
      if (watching) return;
      watching = true;
      startedAt = Date.now();
      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("blur", onBlur);
      window.addEventListener("focus", onFocus);
    },
    // An absence still open at the end is dropped, not closed. Closing the tab is how an interview
    // ends, so recording it would put one final absence on every single interview.
    stop() {
      watching = false;
      awaySince = null;
      awayKind = null;
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("focus", onFocus);
    },
  };
}
