import { h, setHidden } from "../shared/dom.js";
import { deviceNotice } from "./notice.js";

/**
 * Step 1b: the device check, before the room is booked.
 *
 * It sits before joining, deliberately. Joining starts the interviewer and takes one of a small
 * number of concurrent slots, so checking devices afterwards would leave it sitting in an empty room
 * while somebody hunts for a headset. Everything on this screen is a preview, and none of it may
 * outlive the screen.
 *
 * `join()` resolves to `{ session }` or `{ error }`. `onJoined(session)` is called once it succeeds.
 */
export function renderDeviceCheck(config, { join, onJoined, interviewRecorded }) {
  // Everything the preview holds lives here, so it can be released from any path.
  const preview = { stream: null, audioContext: null, meterFrame: null, recordedUrl: null, recorder: null };
  let heardSomething = false;
  let micId;
  let camId;

  // ---- elements --------------------------------------------------------------

  const grantButton = h("button", { id: "grant", class: "primary", type: "button", onClick: () => requestDevices() }, "Allow microphone and camera");
  const grantError = h("div", { id: "grant-error", class: "error" });
  setHidden(grantError, true);
  const ask = h("div", { id: "ask" }, deviceNotice(interviewRecorded), grantButton, grantError);

  const videoPreview = h("video", { id: "preview", autoplay: true, playsInline: true, muted: true });
  const noPreview = h(
    "div",
    { class: "placeholder", id: "no-preview" },
    "No camera preview. You can still take the interview, and your camera will simply be off.",
  );
  setHidden(noPreview, true);

  const meterFill = h("div", { class: "meter-fill", id: "meter" });
  const micStatus = statusLine("mic-status", "Say something and watch the bar move.", "");
  const micSelect = h("select", { id: "mic-pick", class: "hidden", onChange: (e) => requestDevices(e.target.value, camId) });

  const recordButton = h("button", { id: "record", class: "plain", type: "button", onClick: () => record() }, "Record four seconds");
  const replayButton = h("button", { id: "replay", class: "plain", type: "button", disabled: true, onClick: () => play() }, "Play it back");
  const loopStatus = statusLine("loop-status", "", "");

  const camStatus = statusLine("cam-status", "", "");
  const camSelect = h("select", { id: "cam-pick", class: "hidden", onChange: (e) => requestDevices(micId, e.target.value) });

  const joinButton = h("button", { id: "join", class: "primary", type: "button", onClick: () => joinNow() }, "Join the interview");
  const joinError = h("div", { id: "check-error", class: "error" });
  setHidden(joinError, true);

  const devices = h(
    "div",
    { id: "devices" },
    h(
      "div",
      { class: "preview", id: "preview-box" },
      videoPreview,
      noPreview,
    ),
    h(
      "div",
      { class: "device" },
      h("h2", {}, "Microphone"),
      micSelect,
      h("div", { class: "meter" }, meterFill),
      micStatus,
    ),
    h(
      "div",
      { class: "device" },
      h("h2", {}, "Hear yourself back"),
      h(
        "p",
        { class: "sub", style: { margin: "0 0 10px" } },
        "A bar that moves tells you the microphone is picking something up. Playing it back tells you how you actually sound.",
      ),
      h("div", { class: "row" }, recordButton, replayButton),
      loopStatus,
    ),
    h("div", { class: "device", id: "cam-block" }, h("h2", {}, "Camera"), camSelect, camStatus),
    joinButton,
    joinError,
  );
  setHidden(devices, true);

  const card = h(
    "section",
    { class: "card" },
    h("h1", {}, "Check your camera and microphone"),
    h("p", { class: "sub" }, "A quick check before you go in, so nothing surprises you once the interview has started."),
    ask,
    devices,
  );

  const root = h(
    "div",
    { id: "check-wrap" },
    h("div", { class: "brandline top" }, config.company_credit),
    card,
    h("div", { class: "brandline foot" }, config.company_name),
  );

  // ---- preview -----------------------------------------------------------------

  /** Release every preview resource. Safe to call more than once. */
  function stopPreview() {
    if (preview.meterFrame !== null) cancelAnimationFrame(preview.meterFrame);
    preview.meterFrame = null;
    if (preview.audioContext) void preview.audioContext.close().catch(() => undefined);
    preview.audioContext = null;
    preview.stream?.getTracks().forEach((track) => track.stop());
    preview.stream = null;
    videoPreview.srcObject = null;
    if (preview.recordedUrl) URL.revokeObjectURL(preview.recordedUrl);
    preview.recordedUrl = null;
  }

  /** A moving bar answers "is it hearing me" without anyone reading anything. */
  function startMeter(mic) {
    const Ctx = window.AudioContext ?? window.webkitAudioContext;
    if (!Ctx) return; // no meter, but the check still works
    const context = new Ctx();
    preview.audioContext = context;
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(mic).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);

    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) {
        const centred = (sample - 128) / 128;
        sum += centred * centred;
      }
      const level = Math.min(1, Math.sqrt(sum / samples.length) * 4);
      meterFill.style.width = `${(level * 100).toFixed(0)}%`;
      if (level > 0.06 && !heardSomething) {
        heardSomething = true;
        setStatus(micStatus, "That came through clearly.", "good");
      }
      preview.meterFrame = requestAnimationFrame(tick);
    };
    tick();
  }

  // Labels are empty until permission is granted, which is why this runs after.
  async function listDevices() {
    let list = [];
    try {
      list = await navigator.mediaDevices.enumerateDevices();
    } catch {
      return;
    }
    fillSelect(micSelect, list.filter((d) => d.kind === "audioinput" && d.deviceId), micId);
    fillSelect(camSelect, list.filter((d) => d.kind === "videoinput" && d.deviceId), camId);
  }

  /** Ask for both devices. The microphone decides whether they can go on. */
  async function requestDevices(wantedMic, wantedCam) {
    stopPreview();
    heardSomething = false;
    setHidden(grantError, true);

    let mic;
    try {
      mic = await navigator.mediaDevices.getUserMedia({
        audio: wantedMic ? { deviceId: { exact: wantedMic } } : true,
      });
    } catch (err) {
      grantError.textContent = micDenialMessage(err);
      setHidden(grantError, false);
      grantButton.textContent = "Try again";
      return;
    }

    preview.stream = mic;
    setHidden(ask, true);
    setHidden(devices, false);
    startMeter(mic);

    // The camera is checked, not required. Refusing somebody an interview over a broken webcam
    // would be a worse failure than the one this screen exists to prevent.
    setHidden(noPreview, true);
    setHidden(videoPreview, false);
    try {
      const cam = await navigator.mediaDevices.getUserMedia({
        video: wantedCam ? { deviceId: { exact: wantedCam } } : true,
      });
      cam.getVideoTracks().forEach((track) => mic.addTrack(track));
      videoPreview.srcObject = mic;
      setStatus(camStatus, "Your camera is working. It will be on during the interview.", "good");
    } catch {
      setHidden(noPreview, false);
      setHidden(videoPreview, true);
      setStatus(camStatus, "We could not use your camera. You can still take the interview, and your camera will be off.", "warn");
    }

    camId = wantedCam;
    micId = wantedMic;
    await listDevices();
  }

  function record() {
    const mic = preview.stream;
    const cannotPlay = () =>
      setStatus(loopStatus, "This browser cannot play your voice back. The bar above still tells you the microphone is working.", "warn");
    if (!mic || !window.MediaRecorder) return cannotPlay();

    const chunks = [];
    let started;
    try {
      started = new MediaRecorder(new MediaStream(mic.getAudioTracks()));
    } catch {
      return cannotPlay();
    }
    preview.recorder = started;
    started.ondataavailable = (event) => chunks.push(event.data);
    started.onstop = () => {
      if (preview.recordedUrl) URL.revokeObjectURL(preview.recordedUrl);
      preview.recordedUrl = URL.createObjectURL(new Blob(chunks));
      recordButton.disabled = false;
      replayButton.disabled = false;
      recordButton.textContent = "Record again";
      setStatus(loopStatus, "Ready. Play it back and listen.", "good");
      play();
    };
    started.start();
    recordButton.disabled = true;
    replayButton.disabled = true;
    setStatus(loopStatus, "Recording. Say a sentence or two.", "");
    window.setTimeout(() => {
      if (started.state === "recording") started.stop();
    }, 4000);
  }

  function play() {
    if (!preview.recordedUrl) return;
    new Audio(preview.recordedUrl).play().catch(() => {
      setStatus(loopStatus, "Your browser would not play that back. Tap the button again.", "warn");
    });
  }

  async function joinNow() {
    setHidden(joinError, true);
    joinButton.disabled = true;
    joinButton.textContent = "Connecting…";
    // Warned, never blocked. Somebody who simply did not speak has a working microphone and a flat
    // bar, and locking them out of their own interview over that cannot be undone from their side.
    if (!heardSomething) {
      setStatus(
        micStatus,
        "We did not hear anything during the check. You can still go ahead, but if the interviewer cannot hear you, come back and check your microphone.",
        "warn",
      );
    }
    const outcome = await join();
    if (outcome.error || !outcome.session) {
      joinError.textContent = outcome.error ?? "Could not join the interview.";
      setHidden(joinError, false);
      joinButton.disabled = false;
      joinButton.textContent = "Join the interview";
      return;
    }
    // The preview has done its job. The call object opens the camera itself, and two things holding
    // one device is how a candidate ends up with a black self-view.
    stopPreview();
    onJoined(outcome.session);
  }

  return { element: root, stopPreview };
}

function statusLine(id, text, kind) {
  return h("div", { class: `status ${kind}`.trim(), id }, text);
}

function setStatus(node, text, kind) {
  node.textContent = text;
  node.className = `status ${kind}`.trim();
}

function fillSelect(select, devices, selectedId) {
  select.replaceChildren(
    ...devices.map((d, i) => h("option", { value: d.deviceId }, d.label || `Option ${i + 1}`)),
  );
  if (selectedId) select.value = selectedId;
  setHidden(select, devices.length < 2);
}

function micDenialMessage(err) {
  const name = err?.name;
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "We could not find a microphone. Plug one in or connect a headset, then try again. The interview is a spoken conversation, so one is needed.";
  }
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Your browser is blocking the microphone. Look for the camera or lock icon in the address bar, allow access for this page, then try again. If you have already changed it, try again now.";
  }
  if (name === "NotReadableError") {
    return "Your microphone is in use by another app. Close anything else that might be using it, like a video call, then try again.";
  }
  return "We could not reach your microphone. Check that one is connected, then try again.";
}
