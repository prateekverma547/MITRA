/**
 * Constructed inputs for the proctoring tests: a 478-point landmark set with
 * known iris size and eye position, rotation matrices with known angles, and
 * timed reading streams at the live detection rate. Nothing here comes from
 * the code under test.
 */
import type { Landmark } from "../../src/features/interview-room/proctoring/geometry";
import type { TimedReading } from "../../src/features/interview-room/proctoring/types";

export const FPS = 8;

/**
 * Landmarks for one face in a w x h frame: both eyes 30 px wide at the given
 * centres, each iris a circle of `irisPx` diameter (optionally squashed
 * horizontally, as a turned head does), offset in the eye by (eyeH, eyeV)
 * fractions of the eye width.
 */
export function faceLandmarks(opts: {
  w?: number;
  h?: number;
  irisPx: number;
  eyeH?: number;
  eyeV?: number;
  squashX?: number;
}): Landmark[] {
  const { w = 1280, h = 720, irisPx, eyeH = 0, eyeV = 0, squashX = 1 } = opts;
  const lm: Landmark[] = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 }));
  const eyeW = 30;
  const put = (i: number, xPx: number, yPx: number) => (lm[i] = { x: xPx / w, y: yPx / h });
  const eye = (cx: number, cy: number, leftCorner: number, rightCorner: number, iris: number, ring: number[]) => {
    put(leftCorner, cx - eyeW / 2, cy);
    put(rightCorner, cx + eyeW / 2, cy);
    const ix = cx + eyeH * eyeW;
    const iy = cy + eyeV * eyeW;
    put(iris, ix, iy);
    const r = irisPx / 2;
    // ring order: right, top, left, bottom (469-472 / 474-477)
    put(ring[0], ix + r * squashX, iy);
    put(ring[1], ix, iy - r);
    put(ring[2], ix - r * squashX, iy);
    put(ring[3], ix, iy + r);
  };
  eye(600, 360, 33, 133, 468, [469, 470, 471, 472]); // right eye: 33 image-left
  eye(680, 360, 362, 263, 473, [474, 475, 476, 477]); // left eye: 362 image-left
  return lm;
}

/**
 * A column-major 4x4 matrix R = Ry(yaw) * Rx(pitch), degrees: the Z-Y-X
 * (yaw-pitch-roll) convention, with no roll. Written out by hand from the two
 * rotation matrices, not from the code under test.
 */
export function poseMatrix(yawDeg: number, pitchDeg: number): number[] {
  const y = (yawDeg * Math.PI) / 180;
  const p = (pitchDeg * Math.PI) / 180;
  const R = [
    [Math.cos(y), Math.sin(y) * Math.sin(p), Math.sin(y) * Math.cos(p)],
    [0, Math.cos(p), -Math.sin(p)],
    [-Math.sin(y), Math.cos(y) * Math.sin(p), Math.cos(y) * Math.cos(p)],
  ];
  const m = new Array(16).fill(0);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) m[c * 4 + r] = R[r][c];
  m[15] = 1;
  return m;
}

/** A reading of one face looking at the screen at 60 cm. Override what a test changes. */
export function reading(t: number, over: Partial<TimedReading> = {}): TimedReading {
  return {
    t,
    capturedAt: t * 1000,
    faces: 1,
    irisPx: 19.6,
    distanceCm: 60,
    yaw: 0,
    pitch: 0,
    eyeH: 0,
    eyeV: 0,
    faceLuma: 120,
    frameLuma: 110,
    hands: null,
    width: 1280,
    height: 720,
    detectMs: 12,
    ...over,
  };
}

/** Readings at FPS from `from` to `to` seconds (exclusive), each shaped by `at(t)`. */
export function stream(from: number, to: number, at: (t: number) => Partial<TimedReading> = () => ({})): TimedReading[] {
  const out: TimedReading[] = [];
  for (let i = Math.round(from * FPS); i < Math.round(to * FPS); i++) {
    const t = i / FPS;
    out.push(reading(t, at(t)));
  }
  return out;
}

/** Deterministic pseudo-random numbers (mulberry32), so "random" sessions repeat exactly. */
export function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Looking down 30 degrees with the eyes lowered: a phone on the desk. */
export const LOOK_DOWN = { pitch: 30, eyeV: 0.2 } as const;
/** Looking up and to one side, as people do when thinking. */
export const LOOK_UP_SIDE = { pitch: -22, yaw: 15, eyeV: -0.15 } as const;
