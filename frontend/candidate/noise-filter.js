import DailyIframe from "../vendor/daily-js-0.94.0.esm.js";

// Background-noise removal on the candidate's microphone, in their browser, before the audio leaves it.
//
// Why here and not on the server: a fan, a television or someone talking in the next room otherwise
// reaches the bot raw. Silero VAD opens a turn on it, and Deepgram transcribes it into the candidate's
// record. Cleaning at the source means VAD, STT and the recording all hear the same clean signal, and the
// bot gains no dependency and no per-session CPU. The Fatima kiosk gets the same effect from OpenAI's
// server-side `noise_reduction`, which MITRA's live Deepgram path has no equivalent of.
//
// Daily's processor is Krisp. It runs on the candidate's CPU, and when the machine cannot keep up Daily
// turns the processor off **and mutes the microphone**. A muted candidate in an interview is far worse
// than a noisy one, so every failure path below restores the microphone and carries on unfiltered. The
// interview must never depend on this working: it improves the audio when it can and steps aside when it
// cannot.

export const NOISE_CANCELLATION = { audio: { processor: { type: "noise-cancellation" } } };

/** Asked for explicitly rather than left to browser defaults: the browser's own echo cancellation and
 * suppression are the floor this rests on wherever Krisp is unsupported or has switched itself off. */
export const MIC_CONSTRAINTS = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};

/**
 * Turn the filter on once the call is joined, and fall back cleanly if it fails at any point.
 *
 * `micWanted()` reports whether the candidate wants their microphone on, so a failure never unmutes
 * somebody who muted themselves.
 */
export function enableNoiseFilter(daily, micWanted) {
  let failed = false;

  const fallBack = (reason) => {
    if (failed) return;
    failed = true;
    console.warn(`Noise filter off, continuing without it: ${reason}`);
    // Daily mutes the microphone when its audio processor fails. Put it back.
    if (micWanted() && !daily.localAudio()) daily.setLocalAudio(true);
  };

  daily.on("nonfatal-error", (event) => {
    if (!event) return;
    const processorSetup = event.type === "input-settings-error" && event.details?.category !== "settings";
    if (event.type === "audio-processor-error" || processorSetup) fallBack(`${event.type}: ${event.errorMsg}`);
  });

  if (!DailyIframe.supportedBrowser().supportsAudioProcessing) {
    console.info("Noise filter not supported in this browser; using the browser's own suppression.");
    return;
  }

  daily.updateInputSettings(NOISE_CANCELLATION).catch((err) => {
    fallBack(String(err?.message ?? err));
  });
}
