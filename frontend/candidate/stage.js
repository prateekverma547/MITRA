import DailyIframe from "../vendor/daily-js-0.94.0.esm.js";
import { h, setHidden } from "../shared/dom.js";
import { createFocusWatch } from "./focus-watch.js";
import { enableNoiseFilter, MIC_CONSTRAINTS } from "./noise-filter.js";

/** Shown when the interview ends on its own (the interviewer leaves). */
const ENDED = "Your interview has ended. Thank you for your time. You can close this tab.";
const HINT = "Take your time. There is no rush, and pausing to think is fine.";

/**
 * Step 2: the call. A call object, not Daily's prebuilt iframe: no participant grid, no screen share,
 * no Daily branding. The candidate should see an interview, with their own picture small in the corner.
 *
 * Video is a talking-head for a person to review afterwards and is never scored, so it is capped at
 * 640x480 and 15 frames a second, with one 200 kbps send layer. Audio is what the assessment rests on,
 * and it must never be starved to carry video.
 *
 * Captions are the interviewer's speech only. The backend never sends the candidate's words.
 *
 * Returns the stage's element. The call starts as soon as this is called. `onEnded(message?)` is called
 * once, when the interview is over.
 */
export function renderStage(config, { session, credentials, onEnded }) {
  const bot = config.bot_name;

  // ---- state ---------------------------------------------------------------------
  let call = null;
  let timer = null;
  let focus = null;
  let finished = false;
  let newTurn = false;
  let micWanted = true;
  let segments = []; // { key, text }
  let noteTimers = [];
  const audioElements = [];

  // ---- elements ------------------------------------------------------------------
  const noteEl = h("div", { class: "noted", id: "noted" });
  setHidden(noteEl, true);
  const timerEl = h("div", { class: "timer", id: "timer" }, "00:00");
  const speakerEl = h("div", { id: "speaker" }, "Connecting…");
  const hintEl = h("div", { id: "hint" }, HINT);
  const orbIcon = h("span", { id: "orb-icon" }, "🎙️");
  const orb = h("div", { class: "orb", id: "orb" }, orbIcon);
  const captionsEl = h("div", { id: "captions" });
  const ccToggle = h("button", { id: "cc-toggle", class: "cc", type: "button", onClick: () => toggleCaptions() }, "Hide captions");
  const selfView = h("video", { id: "selfview", autoplay: true, playsInline: true, muted: true });
  const selfEl = h("div", { id: "self" }, selfView, h("div", { class: "off" }, "Your camera is off"));
  setHidden(selfEl, true);
  const muteButton = h("button", { id: "mute", type: "button", onClick: () => toggleMic() }, "Mute");
  const cameraButton = h("button", { id: "camera", type: "button", onClick: () => toggleCamera() }, "Camera off");
  const controls = h(
    "div",
    { class: "controls", id: "controls" },
    h("img", { class: "watermark", src: config.company_logo_url, alt: config.company_name }),
    muteButton,
    cameraButton,
    h("button", { id: "leave", class: "leave", type: "button", onClick: () => setConfirming(true) }, "End interview"),
  );
  const confirmEl = h(
    "div",
    { class: "confirm", id: "confirm" },
    h("p", {}, "Are you sure you want to end the interview?"),
    h("p", { class: "sub" }, "This cannot be undone, and you will not be able to rejoin."),
    h(
      "div",
      { class: "row" },
      h("button", { id: "stay", type: "button", onClick: () => setConfirming(false) }, "No, keep going"),
      h("button", { id: "confirm-leave", class: "leave", type: "button", onClick: () => leave() }, "Yes, end it"),
    ),
  );
  setHidden(confirmEl, true);

  const element = h(
    "section",
    { id: "stage" },
    noteEl,
    h(
      "div",
      { class: "bar" },
      h("div", { class: "role", id: "role" }, `${bot} · ${session.role_title}`),
      h("div", { class: "by" }, config.company_credit),
      timerEl,
    ),
    h(
      "div",
      { class: "centre" },
      h(
        "div",
        { class: "side listen" },
        orb,
        speakerEl,
        hintEl,
        h("div", { class: "cc-row" }, ccToggle),
        captionsEl,
      ),
      h(
        "div",
        { class: "side you" },
        h(
          "div",
          { class: "camframe" },
          h("div", { class: "camnote" }, "Your camera appears here."),
          selfEl,
        ),
      ),
    ),
    controls,
    confirmEl,
  );

  renderCaptions();

  // ---- display helpers -------------------------------------------------------------

  function setSpeaker(text) {
    speakerEl.textContent = text;
  }

  function setBotSpeaking(speaking) {
    orb.classList.toggle("speaking", speaking);
    orbIcon.textContent = speaking ? "💬" : "🎙️";
  }

  function renderCaptions() {
    captionsEl.replaceChildren(
      ...(segments.length === 0
        ? [h("p", { class: "placeholder" }, `What ${bot} says will appear here.`)]
        : segments.map((s) => h("p", {}, s.text))),
    );
  }

  function toggleCaptions() {
    const shown = captionsEl.classList.contains("hidden");
    setHidden(captionsEl, !shown);
    ccToggle.textContent = shown ? "Hide captions" : "Show captions";
  }

  // Said once, plainly, and gone on its own. It does not accuse or interrupt; the candidate can ignore it.
  function sayAbsence(seconds) {
    const how = seconds >= 90 ? `${Math.round(seconds / 60)} minutes` : `${Math.max(1, Math.round(seconds))} seconds`;
    noteEl.textContent = `The interview page was out of view for ${how}. The person reviewing your interview will see that.`;
    noteEl.className = "noted";
    setHidden(noteEl, false);
    noteTimers.forEach((t) => window.clearTimeout(t));
    const fade = window.setTimeout(() => (noteEl.className = "noted fading"), 6000);
    const hide = window.setTimeout(() => setHidden(noteEl, true), 6400);
    noteTimers = [fade, hide];
  }

  // The focus record is sent as each absence ends, so a dropped connection cannot take the whole
  // record with it. keepalive lets a late one survive the page going away.
  function sendAbsence(absence) {
    try {
      void fetch("/interviews/focus", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        body: JSON.stringify({
          meeting_id: credentials.meetingId,
          password: credentials.password,
          absences: [absence],
        }),
      }).catch(() => undefined);
    } catch {
      // A lost absence is not worth a broken interview.
    }
  }

  // One message per finished sentence. The message type says when a new turn begins.
  function addCaption(data) {
    const text = (data.text ?? "").trim();
    if (!text) return;
    const reset = newTurn;
    newTurn = false;
    const base = reset ? [] : segments;
    const key = data.segment_id ?? `auto-${base.length}`;
    // A segment can be sent more than once as it is spoken, so each owns one line and is updated in place.
    segments = base.some((s) => s.key === key)
      ? base.map((s) => (s.key === key ? { ...s, text } : s))
      : [...base, { key, text }];
    renderCaptions();
  }

  // ---- lifecycle ----------------------------------------------------------------------

  function finish(message) {
    if (finished) return;
    finished = true;
    if (timer !== null) window.clearInterval(timer);
    focus?.stop();
    const current = call;
    call = null;
    current?.destroy();
    audioElements.forEach((a) => a.remove());
    onEnded(message);
  }

  function setConfirming(on) {
    setHidden(confirmEl, !on);
    setHidden(controls, on);
  }

  async function leave() {
    if (call) await call.leave();
    finish();
  }

  function toggleMic() {
    if (!call) return;
    const on = call.localAudio();
    call.setLocalAudio(!on);
    micWanted = !on;
    muteButton.textContent = on ? "Unmute" : "Mute";
    muteButton.className = on ? "muted" : "";
  }

  function toggleCamera() {
    if (!call) return;
    const on = call.localVideo();
    call.setLocalVideo(!on);
    cameraButton.textContent = on ? "Camera on" : "Camera off";
    cameraButton.className = on ? "muted" : "";
    selfEl.classList.toggle("camera-off", on);
  }

  // ---- the call -----------------------------------------------------------------------

  focus = createFocusWatch({ onAbsence: sendAbsence, onNote: sayAbsence });

  const daily = DailyIframe.createCallObject({
    audioSource: true,
    videoSource: true,
    dailyConfig: {
      // The call-object bundle loads by script tag rather than Function(), so the page's CSP needs
      // no 'unsafe-eval'. Requires the Daily fallback hosts in script-src (see app/main.py).
      avoidEval: true,
      userMediaAudioConstraints: MIC_CONSTRAINTS,
      userMediaVideoConstraints: {
        width: { ideal: 640, max: 640 },
        height: { ideal: 480, max: 480 },
        frameRate: { ideal: 15, max: 15 },
      },
    },
    sendSettings: {
      video: {
        encodings: { low: { maxBitrate: 200000, maxFramerate: 15, scaleResolutionDownBy: 1 } },
      },
    },
  });
  call = daily;

  // With a call object we own media playback, so remote audio is attached by hand. Without this the
  // bot is in the room and inaudible.
  daily.on("track-started", (event) => {
    // The candidate's own camera, shown back to them.
    if (event.track.kind === "video" && event.participant?.local) {
      selfView.srcObject = new MediaStream([event.track]);
      setHidden(selfEl, false);
      return;
    }
    if (event.track.kind !== "audio" || event.participant?.local) return;
    const audio = document.createElement("audio");
    audio.autoplay = true;
    audio.srcObject = new MediaStream([event.track]);
    document.body.appendChild(audio);
    audioElements.push(audio);
  });

  daily.on("joined-meeting", () => {
    setSpeaker(`Waiting for ${bot}…`);
    const began = Date.now();
    timer = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - began) / 1000);
      timerEl.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    }, 1000);
    focus.start();
    // After joining, so a filter that fails to load can never stop the candidate getting into the call.
    enableNoiseFilter(daily, () => micWanted);
  });

  daily.on("participant-joined", () => {
    setSpeaker("Listening");
    hintEl.textContent = HINT;
  });

  daily.on("active-speaker-change", (event) => {
    const local = daily.participants().local;
    const speakingNow = event.activeSpeaker?.peerId !== local?.session_id;
    setBotSpeaking(speakingNow);
    setSpeaker(speakingNow ? `${bot} is speaking` : "Listening");
  });

  // The bot leaving is how a completed interview ends.
  daily.on("participant-left", () => finish(ENDED));
  daily.on("left-meeting", () => finish(ENDED));

  daily.on("error", (event) => {
    setSpeaker("Connection problem");
    hintEl.textContent = event?.errorMsg || "Something went wrong with the connection.";
  });

  daily.on("app-message", (event) => {
    const message = event?.data;
    if (!message || message.label !== "rtvi-ai") return;
    if (message.type === "bot-started-speaking") newTurn = true;
    else if (message.type === "bot-output" && message.data) addCaption(message.data);
  });

  daily
    .join({ url: session.room_url, token: session.token })
    .catch((err) => {
      setSpeaker("Could not connect");
      hintEl.textContent = String(err?.message ?? err);
    });

  return element;
}
