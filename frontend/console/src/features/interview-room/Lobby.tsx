// The lobby: a camera preview and a checklist, as Meet's "Ready to join?"
// screen, plus what a proctored interview needs: the camera on and the entire
// screen shared. Every check is real; nothing is shown as ready until it is.

import type { DailyCall, DailyMediaDeviceInfo } from "@daily-co/daily-js";
import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  Circle,
  Loader2,
  Lock,
  Mic,
  MicOff,
  MonitorUp,
  Monitor,
  Video,
  VideoOff,
  Volume2,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { PreJoinFrame } from "./EnterStep";
import { canShareScreen, hasSecondDisplay, pickWholeScreen, shareProblemText, useLevel, type Peer, type ShareProblem } from "./media";
import { roomTimings } from "./diagnostics";
import { join, RoomError, type Credentials, type JoinResult, type RoomFacts } from "./room-api";
import { VideoEl, VoiceTile } from "./Tiles";

type Status = "pass" | "wait" | "fix" | "warn";

interface Check {
  id: string;
  icon: ReactNode;
  label: string;
  status: Status;
  detail: string;
  action?: ReactNode;
}

const STATUS_ICON: Record<Status, ReactNode> = {
  pass: <CheckCircle2 size={18} aria-label="Ready" />,
  wait: <Circle size={18} aria-label="Not yet" />,
  fix: <XCircle size={18} aria-label="Needs fixing" />,
  warn: <AlertTriangle size={18} aria-label="Worth fixing" />,
};

/** Two soft notes, so the candidate can hear that sound comes out. */
function chime() {
  try {
    const ctx = new AudioContext();
    [660, 880].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      const t = ctx.currentTime + i * 0.22;
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(0.18, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.5);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.55);
    });
    window.setTimeout(() => ctx.close().catch(() => undefined), 1200);
  } catch {
    // no sound available: nothing to test
  }
}

export function Lobby({
  call,
  peers,
  refresh,
  facts,
  credentials,
  screen,
  onScreen,
  onJoined,
}: {
  call: DailyCall | null;
  peers: Peer[];
  refresh: () => void;
  facts: RoomFacts;
  credentials: Credentials;
  screen: MediaStream | null;
  onScreen: (s: MediaStream | null) => void;
  onJoined: (room: JoinResult) => void;
}) {
  const technical = facts.round === "technical";
  const [mic, setMic] = useState(true);
  const [cam, setCam] = useState(true);
  const [heard, setHeard] = useState(false);
  const [started, setStarted] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [devices, setDevices] = useState<DailyMediaDeviceInfo[]>([]);
  const [chosen, setChosen] = useState<{ cam?: string; mic?: string; out?: string }>({});
  const [shareProblem, setShareProblem] = useState<ShareProblem | null>(null);
  const [sharing, setSharing] = useState(false);
  const [joining, setJoining] = useState(false);
  const [joinError, setJoinError] = useState<string | null>(null);
  const meter = useRef<HTMLSpanElement>(null);

  const me = peers.find((p) => p.local);

  // Open the camera and microphone for the preview.
  useEffect(() => {
    if (!call) return;
    let live = true;
    call
      .startCamera(technical ? {} : { startVideoOff: true })
      .then(async () => {
        if (!live) return;
        setStarted(true);
        refresh();
        const { devices: list } = await call.enumerateDevices();
        if (live) setDevices(list);
      })
      .catch(() => live && setStartError(technical ? "Your camera or microphone could not be opened." : "Your microphone could not be opened."));
    return () => {
      live = false;
    };
  }, [call, refresh, technical]);

  // The level meter, and the first time we hear the candidate.
  useLevel(mic ? me?.audio : undefined, (level) => {
    meter.current?.style.setProperty("--lvl", level.toFixed(3));
    if (level > 0.12) setHeard(true);
  });

  // The candidate can stop sharing from the browser's own bar at any time.
  useEffect(() => {
    const track = screen?.getVideoTracks()[0];
    if (!track) return;
    const ended = () => onScreen(null);
    track.addEventListener("ended", ended);
    return () => track.removeEventListener("ended", ended);
  }, [screen, onScreen]);

  function toggleMic() {
    call?.setLocalAudio(!mic);
    setMic(!mic);
  }
  function toggleCam() {
    if (technical) return;
    call?.setLocalVideo(!cam);
    setCam(!cam);
  }

  async function choose(kind: "cam" | "mic" | "out", deviceId: string) {
    setChosen((c) => ({ ...c, [kind]: deviceId }));
    if (!call) return;
    if (kind === "out") await call.setOutputDeviceAsync({ outputDeviceId: deviceId });
    else await call.setInputDevicesAsync(kind === "cam" ? { videoDeviceId: deviceId } : { audioDeviceId: deviceId });
    if (kind === "mic") setHeard(false);
    refresh();
  }

  async function share() {
    setSharing(true);
    setShareProblem(null);
    const result = await pickWholeScreen();
    setSharing(false);
    if ("stream" in result) onScreen(result.stream);
    else setShareProblem(result.problem);
  }

  // ---- the checklist ----

  const checks: Check[] = [];
  const blocked = (what: string) =>
    `${what} access is blocked. Click the camera icon in the address bar, choose Allow, then reload this page.`;

  if (technical) {
  checks.push({
      id: "camera",
      icon: <Camera size={16} />,
      label: "Camera",
      status: me?.camBlocked ? "fix" : me?.video ? "pass" : !cam && !technical ? "warn" : "wait",
      detail: me?.camBlocked
        ? blocked("Camera")
        : me?.video
          ? technical
            ? "Working. It stays on for the whole interview."
            : "Working."
          : !cam && !technical
            ? "Your camera is off. HR will see your initials."
            : startError ?? "Starting your camera.",
    });
  }
  checks.push({
    id: "microphone",
    icon: <Mic size={16} />,
    label: "Microphone",
    status: me?.micBlocked ? "fix" : !mic ? "warn" : heard ? "pass" : "wait",
    detail: me?.micBlocked
      ? blocked("Microphone")
      : !mic
        ? "Muted. Unmute before you start so you can be heard."
        : heard
          ? "We can hear you."
          : "Say a few words to test it.",
  });
  if (technical) {
    const live = Boolean(screen?.getVideoTracks()[0]?.readyState === "live");
    checks.push({
      id: "screen",
      icon: <MonitorUp size={16} />,
      label: "Entire screen",
      status: !canShareScreen() ? "fix" : live ? "pass" : shareProblem ? "fix" : "wait",
      detail: !canShareScreen()
        ? shareProblemText.unsupported
        : live
          ? "Shared. Keep it shared until the interview ends."
          : shareProblem
            ? shareProblemText[shareProblem]
            : "Share your entire screen, not a window or a tab.",
      action:
        canShareScreen() && !live ? (
          <button type="button" className="meet-chip-btn" onClick={share} disabled={sharing}>
            {sharing ? <Loader2 size={14} className="meet-spin" /> : <MonitorUp size={14} />}
            {shareProblem ? "Try again" : "Share screen"}
          </button>
        ) : undefined,
    });
    if (hasSecondDisplay()) {
      checks.push({
        id: "display",
        icon: <Monitor size={16} />,
        label: "One screen",
        status: "warn",
        detail: "A second display is connected. Disconnect it, or keep everything for this interview on one screen.",
      });
    }
  }

  const passed = (id: string) => checks.find((c) => c.id === id)?.status === "pass";
  const canJoin =
    started &&
    !me?.micBlocked &&
    (!technical || !me?.camBlocked) &&
    (technical ? passed("camera") && passed("screen") : true) &&
    !joining;
  const ready = checks.filter((c) => c.status === "pass").length;

  async function enter() {
    if (!call || !canJoin) return;
    setJoining(true);
    setJoinError(null);
    try {
      const asked = performance.now();
      const room = await join(credentials);
      const joining = performance.now();
      await call.join({ url: room.room_url, token: room.token, startAudioOff: !mic, startVideoOff: !technical });
      roomTimings.joinRequestMs = Math.round(joining - asked); // measurement only (diagnostics.ts)
      roomTimings.dailyJoinMs = Math.round(performance.now() - joining);
      if (technical && screen) call.startScreenShare({ mediaStream: screen });
      onJoined(room);
    } catch (err) {
      const status = err instanceof RoomError ? err.status : -1;
      setJoinError(
        status === -1 ? "We could not connect you to the room. Check your connection and try again." : (err as Error).message,
      );
      setJoining(false);
    }
  }

  const pick = (kind: "videoinput" | "audioinput" | "audiooutput") => devices.filter((d) => d.kind === kind);

  return (
    <PreJoinFrame step="Step 3 of 3">
      <div className="meet-lobby">
        <section className="meet-preview" aria-label={technical ? "Your camera" : "Your microphone"}>
          {!technical ? (
            <div className="meet-preview-tile voice">
              <VoiceTile name={me?.name ?? "You"} track={me?.audio} muted={!mic} you large />
              <p className="meet-voice-hint">{mic ? "A voice call, no camera. Say a few words: the rings move when you are heard." : "Your microphone is off."}</p>
              <div className="meet-preview-ctl">
                <button
                  type="button"
                  className={`meet-round${mic ? "" : " off"}`}
                  onClick={toggleMic}
                  aria-pressed={!mic}
                  aria-label={mic ? "Mute microphone" : "Unmute microphone"}
                >
                  {mic ? <Mic size={20} /> : <MicOff size={20} />}
                </button>
              </div>
            </div>
          ) : (
          <div className="meet-preview-tile">
            {cam && me?.video ? (
              <VideoEl track={me.video} mirror />
            ) : (
              <span className="meet-preview-empty">
                {startError || me?.camBlocked ? (
                  <VideoOff size={28} />
                ) : !cam ? (
                  "Camera is off"
                ) : (
                  <Loader2 size={26} className="meet-spin" />
                )}
              </span>
            )}
            <span className="meet-meter" ref={meter} aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <div className="meet-preview-ctl">
              <button
                type="button"
                className={`meet-round${mic ? "" : " off"}`}
                onClick={toggleMic}
                aria-pressed={!mic}
                aria-label={mic ? "Mute microphone" : "Unmute microphone"}
                title={mic ? "Mute microphone" : "Unmute microphone"}
              >
                {mic ? <Mic size={20} /> : <MicOff size={20} />}
              </button>
              <button
                type="button"
                className={`meet-round${cam ? "" : " off"}${technical ? " locked" : ""}`}
                onClick={toggleCam}
                aria-disabled={technical}
                aria-label={technical ? "Your camera stays on during a proctored interview" : cam ? "Turn off camera" : "Turn on camera"}
                title={technical ? "Your camera stays on during a proctored interview" : cam ? "Turn off camera" : "Turn on camera"}
              >
                {cam ? <Video size={20} /> : <VideoOff size={20} />}
                {technical && <Lock size={11} className="meet-lock" aria-hidden="true" />}
              </button>
            </div>
          </div>
          )}

          {devices.length > 0 && (
            <div className="meet-devices">
              {(
                [
                  ["cam", "Camera", technical ? pick("videoinput") : []],
                  ["mic", "Microphone", pick("audioinput")],
                  ["out", "Speaker", pick("audiooutput")],
                ] as const
              ).map(([kind, label, list]) =>
                list.length ? (
                  <label key={kind}>
                    <span>{label}</span>
                    <select value={chosen[kind] ?? ""} onChange={(e) => choose(kind, e.target.value)}>
                      <option value="">Default</option>
                      {list.map((d) => (
                        <option key={d.deviceId} value={d.deviceId}>
                          {d.label || label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null,
              )}
              <button type="button" className="meet-chip-btn" onClick={chime}>
                <Volume2 size={14} /> Test speaker
              </button>
            </div>
          )}
        </section>

        <section className="meet-ready" aria-labelledby="ready-title">
          <h1 id="ready-title" className="meet-title">
            Ready to join?
          </h1>
          <p className="meet-lead">
            {technical
              ? `${facts.bot_name} joins as soon as you do and opens with a short hello. ${facts.role_title}, about ${facts.duration_minutes} minutes.`
              : `A member of the HR team will meet you in the room. If they are not there yet, wait for them. ${facts.role_title}, about ${facts.duration_minutes} minutes.`}
          </p>

          <div className="meet-checks" role="list" aria-label={`${ready} of ${checks.length} ready`}>
            {checks.map((c) => (
              <div key={c.id} role="listitem" className={`meet-check ${c.status}`}>
                <span className="meet-check-state">{STATUS_ICON[c.status]}</span>
                <div className="meet-check-body">
                  <strong>
                    {c.icon} {c.label}
                  </strong>
                  <span>{c.detail}</span>
                </div>
                {c.action}
              </div>
            ))}
          </div>

          {joinError && (
            <div className="meet-alert" role="alert">
              <strong>You are not in the room yet.</strong>
              <span>{joinError}</span>
            </div>
          )}

          <button type="button" className="meet-primary big" disabled={!canJoin} onClick={enter}>
            {joining && <Loader2 size={18} className="meet-spin" />}
            {joining ? "Joining" : technical ? "Start the interview" : "Join now"}
          </button>
          <p className="meet-hint">
            {canJoin
              ? technical
                ? "Starting records the time you agreed."
                : "Joining records the time you agreed."
              : technical
                ? "The camera and your entire screen must be ready first."
                : "Allow the microphone first."}
          </p>
        </section>
      </div>
    </PreJoinFrame>
  );
}
