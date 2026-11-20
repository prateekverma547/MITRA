/**
 * What the detection worker sends back for one analysed frame. Numbers only:
 * no image, no landmarks. This is the boundary that keeps video on the device.
 */
export interface FrameReading {
  /** performance.now() when the frame was captured, milliseconds. */
  capturedAt: number;
  /** Faces found (the landmarker looks for up to two). */
  faces: number;
  /** Mean iris diameter of the first face, pixels. */
  irisPx: number | null;
  /** Distance estimate of the first face, centimetres. */
  distanceCm: number | null;
  /** Head pose of the first face, degrees. */
  yaw: number | null;
  pitch: number | null;
  /** Iris position in the eye of the first face (fraction of eye width). */
  eyeH: number | null;
  eyeV: number | null;
  /** Mean luma 0-255 of the first face's box, and of the whole frame. */
  faceLuma: number | null;
  frameLuma: number | null;
  /** Hands found, or null when the hand landmarker is not running. */
  hands: number | null;
  /** Frame size the numbers were computed on. */
  width: number;
  height: number;
  /** Time spent analysing this frame in the worker, milliseconds. */
  detectMs: number;
}

/** A reading placed on the interview clock, as the engine consumes it. */
export interface TimedReading extends FrameReading {
  /** Interview clock, seconds. */
  t: number;
}
