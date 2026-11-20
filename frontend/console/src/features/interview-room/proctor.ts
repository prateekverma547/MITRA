// On-device face watching for the MITRA interview round's candidate room.
//
// MediaPipe's face detector runs in the candidate's own browser on their own
// camera, about four times a second. Nothing is uploaded: what leaves this
// file is three facts for the screen (how many faces, whether the head is
// turned away, whether the detector is running). It drives on-screen
// reminders only, at most three of each kind, and never a score: Track I's
// proctoring rules (docs/DEV_PLAN.md, CLAUDE.md "Proctoring is evidence for a
// person ... never part of a competency score").
//
// It is an approximation by design: head direction from six face keypoints,
// not eye tracking. Track I's proctoring service replaces it at the merge.

import { FaceDetector, FilesetResolver } from "@mediapipe/tasks-vision";
import { roomTimings } from "./diagnostics";
import { useEffect, useRef, useState } from "react";

const VERSION = "1.2.0"; // keep in step with package.json
const WASM = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VERSION}/wasm`;
const MODEL =
  "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite";

/** Samples per second. Enough to notice a turn of the head; cheap on a laptop. */
const RATE_HZ = 4;
/** Consecutive samples a change must hold before it counts, so a blink or a nod does not. */
const HOLD = 3;
/** Head turned: nose offset from between the eyes, as a share of the eye distance. */
const YAW_LIMIT = 0.42;
/** Head tipped well down or up: nose below or above the eyes, as a share of the eye distance. */
const PITCH_LOW = 0.18;
const PITCH_HIGH = 1.05;

let loading: Promise<FaceDetector> | null = null;
function detector(): Promise<FaceDetector> {
  loading ??= FilesetResolver.forVisionTasks(WASM)
    .then((files) =>
      FaceDetector.createFromOptions(files, {
        baseOptions: { modelAssetPath: MODEL, delegate: "GPU" },
        runningMode: "VIDEO",
        minDetectionConfidence: 0.55,
      }),
    )
    .catch((err) => {
      loading = null;
      throw err;
    });
  return loading;
}

export interface FaceState {
  status: "loading" | "ready" | "unavailable";
  faces: number;
  away: boolean;
}

/** Is the head turned away from the screen? Keypoints: right eye, left eye, nose tip, mouth, right ear, left ear. */
export function headAway(k: { x: number; y: number }[]): boolean {
  if (k.length < 4) return false;
  const [re, le, nose] = k;
  const eyeDist = Math.hypot(le.x - re.x, le.y - re.y);
  if (eyeDist < 0.01) return false;
  const midX = (re.x + le.x) / 2;
  const midY = (re.y + le.y) / 2;
  const yaw = (nose.x - midX) / eyeDist;
  const pitch = (nose.y - midY) / eyeDist;
  return Math.abs(yaw) > YAW_LIMIT || pitch < PITCH_LOW || pitch > PITCH_HIGH;
}

export function useFaceWatch(track: MediaStreamTrack | undefined, active: boolean): FaceState {
  const [state, setState] = useState<FaceState>({ status: "loading", faces: 1, away: false });
  const held = useRef({ faces: 1, away: false, facesRun: 0, awayRun: 0 });

  useEffect(() => {
    if (!active || !track) return;
    let stopped = false;
    let timer = 0;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = new MediaStream([track]);
    video.play().catch(() => undefined);

    const loadStarted = performance.now();
    detector()
      .then((det) => {
        roomTimings.faceCheckLoadMs = Math.round(performance.now() - loadStarted); // measurement only
        roomTimings.faceCheckStatus = "ready";
        if (stopped) return;
        setState((s) => ({ ...s, status: "ready" }));
        timer = window.setInterval(() => {
          if (video.readyState < 2) return;
          let faces = 0;
          let away = false;
          try {
            const result = det.detectForVideo(video, performance.now());
            faces = result.detections.length;
            if (faces === 1) away = headAway(result.detections[0].keypoints);
          } catch {
            return;
          }
          // A change counts only once it has held for a moment.
          const h = held.current;
          h.facesRun = faces === h.faces ? 0 : h.facesRun + 1;
          h.awayRun = away === h.away ? 0 : h.awayRun + 1;
          let changed = false;
          if (h.facesRun >= HOLD) {
            h.faces = faces;
            h.facesRun = 0;
            changed = true;
          }
          if (h.awayRun >= HOLD) {
            h.away = away;
            h.awayRun = 0;
            changed = true;
          }
          if (changed) setState({ status: "ready", faces: h.faces, away: h.faces === 1 && h.away });
        }, 1000 / RATE_HZ);
      })
      .catch(() => {
        roomTimings.faceCheckStatus = "unavailable";
        if (!stopped) setState((s) => ({ ...s, status: "unavailable" }));
      });

    return () => {
      stopped = true;
      window.clearInterval(timer);
      video.pause();
      video.srcObject = null;
    };
  }, [track, active]);

  return state;
}
