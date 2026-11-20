/**
 * Pure geometry on MediaPipe Face Landmarker output: iris size, distance, head
 * pose, eye offsets. Runs inside the detection worker; only the numbers it
 * returns leave the worker, never the landmarks.
 *
 * Landmark indices (478-point mesh with irises), as in MediaPipe's
 * face_mesh_connections: the candidate's right eye has corners 33 (outer) and
 * 133 (inner), iris centre 468 and ring 469-472; the left eye has corners 362
 * (inner) and 263 (outer), centre 473 and ring 474-477. Landmarks are
 * normalised: x by the frame width, y by its height.
 */
import { ASSUMED_HFOV_DEG, IRIS_DIAMETER_MM } from "./thresholds";

export interface Landmark {
  x: number;
  y: number;
  z?: number;
}

export const RIGHT_EYE = { outer: 33, inner: 133, iris: 468, ring: [469, 470, 471, 472] } as const;
export const LEFT_EYE = { outer: 263, inner: 362, iris: 473, ring: [474, 475, 476, 477] } as const;

const px = (l: Landmark, w: number, h: number) => ({ x: l.x * w, y: l.y * h });
const dist = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

/**
 * Diameter of one iris in pixels: the larger of its two ring diameters
 * (469-471 and 470-472, or 474-476 and 475-477). A turned head shortens one
 * axis but not the other, so the larger one is the less foreshortened.
 */
export function irisDiameterPx(lm: Landmark[], ring: readonly number[], w: number, h: number): number {
  const [a, b, c, d] = ring.map((i) => px(lm[i], w, h));
  return Math.max(dist(a, c), dist(b, d));
}

/** Mean iris diameter of both eyes in pixels, or null without iris landmarks. */
export function meanIrisPx(lm: Landmark[], w: number, h: number): number | null {
  if (lm.length < 478) return null;
  return (irisDiameterPx(lm, RIGHT_EYE.ring, w, h) + irisDiameterPx(lm, LEFT_EYE.ring, w, h)) / 2;
}

/** Focal length in pixels from the frame width and an assumed horizontal field of view. */
export function focalPx(frameWidth: number, hfovDeg = ASSUMED_HFOV_DEG): number {
  return frameWidth / 2 / Math.tan((hfovDeg * Math.PI) / 360);
}

/** Pinhole camera: distance = focal * real size / image size. Centimetres. */
export function distanceCm(irisPx: number, frameWidth: number, hfovDeg = ASSUMED_HFOV_DEG): number {
  return (focalPx(frameWidth, hfovDeg) * IRIS_DIAMETER_MM) / irisPx / 10;
}

/**
 * The field of view that would make a reading match a measured distance. For
 * the tape-measure check: it turns "the tape says 60 cm" into this camera's
 * real field of view, which is the one unknown in the estimate.
 */
export function impliedHfovDeg(irisPx: number, frameWidth: number, trueCm: number): number {
  const f = (trueCm * 10 * irisPx) / IRIS_DIAMETER_MM;
  return (2 * Math.atan(frameWidth / 2 / f) * 180) / Math.PI;
}

export interface EyeOffset {
  /** Iris centre along the eye's corner-to-corner axis, as a fraction of the eye width. Positive = image right. */
  h: number;
  /** Iris centre across that axis, same unit. Positive = image down. */
  v: number;
}

/**
 * Where the iris sits in the eye, for one eye. The axis runs from the eye's
 * image-left corner to its image-right corner, so both eyes use the same
 * direction and can be averaged. Corners are stable; eyelids are not, so the
 * vertical offset is also measured from the corner line, not from the lids.
 */
export function eyeOffset(lm: Landmark[], left: number, right: number, iris: number, w: number, h: number): EyeOffset {
  const a = px(lm[left], w, h);
  const b = px(lm[right], w, h);
  const c = px(lm[iris], w, h);
  const ax = b.x - a.x;
  const ay = b.y - a.y;
  const width = Math.hypot(ax, ay) || 1;
  const ux = ax / width;
  const uy = ay / width;
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = c.x - mx;
  const dy = c.y - my;
  return { h: (dx * ux + dy * uy) / width, v: (dx * -uy + dy * ux) / width };
}

/** Both eyes averaged. The right eye's image-left corner is 33, the left eye's is 362. */
export function meanEyeOffset(lm: Landmark[], w: number, h: number): EyeOffset | null {
  if (lm.length < 478) return null;
  const r = eyeOffset(lm, RIGHT_EYE.outer, RIGHT_EYE.inner, RIGHT_EYE.iris, w, h);
  const l = eyeOffset(lm, LEFT_EYE.inner, LEFT_EYE.outer, LEFT_EYE.iris, w, h);
  return { h: (r.h + l.h) / 2, v: (r.v + l.v) / 2 };
}

export interface HeadPose {
  yaw: number;
  pitch: number;
  roll: number;
}

/**
 * Head pose in degrees from MediaPipe's facial transformation matrix (4x4,
 * column-major). Only differences from the warm-up baseline are used, so the
 * sign conventions do not matter, only that they are consistent.
 */
export function headPoseFromMatrix(m: ArrayLike<number>): HeadPose {
  // Row r, column c of a column-major matrix is m[c * 4 + r].
  const r = (row: number, col: number) => m[col * 4 + row];
  const deg = 180 / Math.PI;
  const pitch = Math.atan2(r(2, 1), r(2, 2)) * deg;
  const yaw = Math.asin(Math.max(-1, Math.min(1, -r(2, 0)))) * deg;
  const roll = Math.atan2(r(1, 0), r(0, 0)) * deg;
  return { yaw, pitch, roll };
}

/** Median of a list (NaN when empty). */
export function median(values: number[]): number {
  if (values.length === 0) return Number.NaN;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** Percentile (0-100) by nearest rank. */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return Number.NaN;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.ceil((p / 100) * s.length) - 1))];
}

/** Mean luma (0-255, Rec. 601) of RGBA pixels inside a box, sampling every `step`th pixel. */
export function meanLuma(
  rgba: ArrayLike<number>,
  width: number,
  box: { x0: number; y0: number; x1: number; y1: number },
  step = 1,
): number {
  let sum = 0;
  let n = 0;
  for (let y = Math.max(0, box.y0); y < box.y1; y += step) {
    for (let x = Math.max(0, box.x0); x < Math.min(width, box.x1); x += step) {
      const i = (y * width + x) * 4;
      sum += 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2];
      n++;
    }
  }
  return n ? sum / n : Number.NaN;
}
