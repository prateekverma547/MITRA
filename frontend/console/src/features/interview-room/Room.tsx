// The call. MITRA interview round: Mitra's presence fills the stage, the candidate's
// own picture sits in the corner, the entire screen stays shared, and
// reminders are on-screen only. HR round: the HR person fills the stage, as in
// any video call. No captions of anyone, in either round.

import type { DailyCall } from "@daily-co/daily-js";
import {
  AppWindow,
  CircleHelp,
  Eye,
  Lock,
  Mic,
  MicOff,
  MonitorOff,
  MonitorUp,
  PhoneOff,
  ScanFace,
  Users,
  Video,
  VideoOff,
  WifiOff,
  X,
  CircleCheck,
  Maximize2,
  Minimize2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Brand } from "../../shell/Brand";
import { clock, isMitra, pickWholeScreen, shareProblemText, useSpeaking, type Peer } from "./media";
import { useFaceWatch, type FaceState } from "./proctor";
import { useRoomDiagnostics } from "./diagnostics";
import { timesLabel, useEpisodeWarning, useWarning, type Warning } from "./warnings";
import { useIntegrity, type ProctoringPass } from "./proctoring/useIntegrity";
import type { Ending } from "./DoneStep";
import type { RoomFacts } from "./room-api";
import { AudioEl, MitraPresence, SelfView, VideoEl, VoiceTile } from "./Tiles";

/** The End button's message to Mitra (Track I: bot/app_messages.py). */
const END_REQUEST = { label: "mitra-candidate", type: "end-interview", reason: "end_button" };
/** A moment for the message to reach Mitra before the call is left. */
const END_SETTLE_MS = 1500;

/** How long a face problem has to last before its warning shows (a detector blink is not one). */
const WARNING_AFTER_MS = 1500;
/** Once shown, a face warning stays this long after the problem clears, so it can be read. */
const WARNING_HOLD_MS = 3000;
/** Looking away: a glance at the keyboard is not one. */
const GAZE_WARNING_AFTER_MS = 2500;

type RuleKey = "face" | "people" | "eyes" | "screen" | "tab";

export function Room({
  call,
  peers,
  refresh,
  facts,
  screen,
  onScreen,
  joinedAt,
  onFinished,
  gazeRemindersOff = false,
  proctoring = null,
}: {
  call: DailyCall;
  peers: Peer[];
  refresh: () => void;
  facts: RoomFacts;
  screen: MediaStream | null;
  onScreen: (s: MediaStream | null) => void;
  joinedAt: number;
  onFinished: (how: Ending) => void;
  /** Chosen at consent: no reminders about looking away (noted for the reviewer). */
  gazeRemindersOff?: boolean;
  /** From the join: lets this page post the round's integrity events. Memory only. */
  proctoring?: ProctoringPass | null;
}) {
  const technical = facts.round === "technical";
  const [now, setNow] = useState(Date.now());
  const [muted, setMuted] = useState(() => call.participants().local?.tracks.audio.state === "off");
  const [cam] = useState(() => call.participants().local?.tracks.video.state !== "off");
  const [weak, setWeak] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [tabAwayAt, setTabAwayAt] = useState<number | null>(null);
  const [shareError, setShareError] = useState<string | null>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [help, setHelp] = useState(false);
  // The shared screen, opened beside Mitra (the share card), or the usual view.
  const [split, setSplit] = useState(false);
  const [hostLeft, setHostLeft] = useState(false);
  const leaving = useRef(false);
  const mitraSeen = useRef(false);
  const hostSeen = useRef(false);
  /** Closes the round's open integrity events and sends them (set below, used by finish). */
  const closeIntegrity = useRef<() => Promise<void>>(async () => undefined);

  const me = peers.find((p) => p.local);
  const remote = peers.filter((p) => !p.local);
  const mitra = technical
    ? remote.find((p) => isMitra(p, facts.bot_name)) ?? (remote.length === 1 && !remote[0].video ? remote[0] : undefined)
    : undefined;
  const hosts = technical ? [] : remote;
  const mitraSpeaking = useSpeaking(mitra?.audio);
  const host = hosts[0];
  const shared = Boolean(screen && screen.getVideoTracks()[0]?.readyState === "live");
  // Split only while there is a screen to show; stopping the share goes back to the usual view.
  const showSplit = technical && split && shared;
  useEffect(() => {
    if (!showSplit) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSplit(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showSplit]);

  const finish = useCallback(
    async (how: Ending) => {
      if (leaving.current) return;
      leaving.current = true;
      if (technical) {
        // Every open integrity event goes with its end, before anything else.
        await closeIntegrity.current().catch(() => undefined);
      }
      if (technical && how === "ended") {
        // Tell Mitra first (Track I's guide 2.9): the interview is then
        // recorded as ended by the candidate, not as a dropped call that
        // Mitra waits 90 seconds for. Accepted only from the candidate.
        try {
          call.sendAppMessage(END_REQUEST, mitra?.id ?? "*");
          await new Promise((resolve) => window.setTimeout(resolve, END_SETTLE_MS));
        } catch {
          // Not sent: leaving still ends the call for Mitra.
        }
      }
      await call.leave().catch(() => undefined);
      onFinished(how);
    },
    [call, onFinished, technical, mitra?.id],
  );

  // The clock.
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  // The call's own events.
  useEffect(() => {
    const onLeft = () => {
      if (!leaving.current) {
        leaving.current = true;
        onFinished("dropped");
      }
    };
    const onQuality = (e?: { threshold?: string }) => setWeak(e?.threshold === "low" || e?.threshold === "very-low");
    const onConnection = (e?: { event?: string }) => {
      if (e?.event === "interrupted") setReconnecting(true);
      if (e?.event === "connected") setReconnecting(false);
    };
    const onError = () => onLeft();
    call.on("left-meeting", onLeft);
    call.on("network-quality-change", onQuality);
    call.on("network-connection", onConnection);
    call.on("error", onError);
    refresh();
    return () => {
      call.off("left-meeting", onLeft);
      call.off("network-quality-change", onQuality);
      call.off("network-connection", onConnection);
      call.off("error", onError);
    };
  }, [call, onFinished, refresh]);

  // Mitra leaving is how a MITRA interview ends. Anyone else leaving is not.
  useEffect(() => {
    if (!technical) return;
    if (mitra) mitraSeen.current = true;
    else if (mitraSeen.current) {
      const t = window.setTimeout(() => finish("complete"), 1200);
      return () => window.clearTimeout(t);
    }
  }, [technical, mitra, finish]);

  // In the HR round, the person leaving is shown, not acted on: they may be back.
  useEffect(() => {
    if (technical) return;
    if (hosts.length) {
      hostSeen.current = true;
      setHostLeft(false);
    } else if (hostSeen.current) setHostLeft(true);
  }, [technical, hosts.length]);

  // The entire screen, kept shared. Stopping it from the browser's bar ends the track.
  useEffect(() => {
    const track = screen?.getVideoTracks()[0];
    if (!track) return;
    const ended = () => {
      call.stopScreenShare();
      onScreen(null);
    };
    track.addEventListener("ended", ended);
    return () => track.removeEventListener("ended", ended);
  }, [screen, call, onScreen]);

  async function shareAgain() {
    setShareError(null);
    const result = await pickWholeScreen();
    if ("stream" in result) {
      onScreen(result.stream);
      call.startScreenShare({ mediaStream: result.stream });
    } else setShareError(shareProblemText[result.problem]);
  }

  // The page out of view, for the strip: "Left the tab" for a few seconds after.
  useEffect(() => {
    if (!technical) return;
    const onVisibility = () => {
      if (document.visibilityState === "visible") setTabAwayAt(Date.now());
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [technical]);
  useEffect(() => {
    if (!tabAwayAt) return;
    const t = window.setTimeout(() => setTabAwayAt(null), 10_000);
    return () => window.clearTimeout(t);
  }, [tabAwayAt]);

  // The camera, watched on this device: a face in view, one person, head towards the screen.
  const face = useFaceWatch(me?.video, technical);

  // Integrity events and reminders: Track I's engine decides both (at most 3
  // reminders of each kind, 2 minutes apart), and its sender stores the events
  // for the report, with the proctoring token from the join. Facts for a
  // person, never a score.
  const integrity = useIntegrity({ active: technical, pass: proctoring, face, screen, gazeRemindersOff });

  // The warnings (warnings.ts): one for every kind of problem the room can see, on
  // for as long as it lasts, every time, with how many times it has come up. They
  // replace the engine's capped reminders on screen; the engine still records
  // every event for the report.
  const faceReady = technical && face.status === "ready";
  const warn = {
    screen: useWarning(technical && !shared, 0),
    face: useWarning(faceReady && face.faces === 0, WARNING_AFTER_MS, WARNING_HOLD_MS),
    people: useWarning(faceReady && face.faces > 1, WARNING_AFTER_MS, WARNING_HOLD_MS),
    eyes: useWarning(faceReady && !gazeRemindersOff && face.faces === 1 && face.away, GAZE_WARNING_AFTER_MS, WARNING_HOLD_MS),
    tab: useEpisodeWarning(technical ? tabAwayAt : null),
  };
  closeIntegrity.current = integrity.finish;
  // Measurement only (diagnostics.ts): this browser's timings during the round.
  useRoomDiagnostics({ active: technical, pass: proctoring, mic: me?.audio });

  // Closing the tab mid-interview asks first.
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (leaving.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  const toggleMic = useCallback(() => {
    call.setLocalAudio(muted);
    setMuted(!muted);
  }, [call, muted]);
  // The camera is never toggled here: locked on in the MITRA interview round,
  // and not used at all in the HR round's voice call.
  const toggleCam = useCallback(() => undefined, []);

  // Meet's shortcuts: Ctrl/Cmd+D for the microphone, Ctrl/Cmd+E for the camera.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setHelp(false);
        setConfirmEnd(false);
      }
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === "d") {
        e.preventDefault();
        toggleMic();
      } else if (k === "e") {
        e.preventDefault();
        toggleCam();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleMic, toggleCam]);

  const elapsed = (now - joinedAt) / 1000;
  const progress = Math.min(1, elapsed / (facts.duration_minutes * 60));
  const remaining = facts.duration_minutes * 60 - elapsed;
  const timeLeft =
    remaining >= 60 ? `about ${Math.round(remaining / 60)} min left` : remaining >= 0 ? "under a minute left" : `${Math.max(1, Math.round(-remaining / 60))} min over`;
  const remoteScreen = remote.find((p) => p.screen);
  const mitraState = mitra ? (mitraSpeaking ? "speaking" : "listening") : mitraSeen.current ? "gone" : "joining";

  const status = [facts.recording ? "Recording" : "Not recorded", technical ? (shared ? "entire screen shared" : "screen not shared") : null]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="meet meet-call">
      {remote.map((p) => (p.audio ? <AudioEl key={p.id} track={p.audio} /> : null))}

      <header className="meet-bar">
        <Brand size={22} />
        <span className="meet-bar-role">{facts.role_title}</span>
        <span className="meet-bar-fill" />
        {weak && (
          <span className="meet-pill warn" title="Your connection is weak. Video may drop before sound does.">
            <WifiOff size={13} /> Weak connection
          </span>
        )}
        {facts.recording && (
          <span className="meet-pill rec">
            <span className="meet-rec-dot" /> Recording
          </span>
        )}
        <span className="meet-time" aria-label={`${clock(elapsed)} elapsed. ${timeLeft}.`}>
          <span className="meet-clock">{clock(elapsed)}</span>
          <span className={`meet-left${remaining < 0 ? " over" : remaining < 300 ? " soon" : ""}`}>{timeLeft}</span>
        </span>
        <span className="meet-progress" aria-hidden="true">
          <i style={{ transform: `scaleX(${progress})` }} />
        </span>
      </header>

      <div className="meet-notices" aria-live="polite">
        {reconnecting && (
          <div className="meet-notice" role="status">
            <span className="meet-spinner small" />
            <span>Reconnecting. Stay on this page; {technical ? facts.bot_name : "the call"} carries on where you left off.</span>
          </div>
        )}
        {hostLeft && (
          <div className="meet-notice" role="status">
            <span>The HR team member has left the room. If the conversation is over, you can leave too.</span>
            <button type="button" className="meet-notice-btn" onClick={() => finish("complete")}>
              Leave
            </button>
          </div>
        )}
      </div>

      <main className="meet-stage">
        <h1 className="sr-only">{technical ? `Interview with ${facts.bot_name}` : "Conversation with HR"}</h1>
        {technical ? (
          // One calm stage with MITRA at its centre, the shared screen as a small
          // status card (a large live preview of your own screen is a hall of
          // mirrors) and the candidate's camera bottom right; a rail on the right
          // with the rules, point by point, and below them any warning, large and
          // bold, with how many times it has come up. Calm until something needs
          // fixing: proctoring is evidence for a person, never a show.
          <section className={`meet-stage-main tech${showSplit ? " split" : ""}`}>
            {showSplit && screen && (
              // Split: their shared screen fills the left half; the right half holds
              // the instructions and any warning, then MITRA with their camera bottom
              // right. "Minimise" (or Esc) goes back.
              <section className="meet-screen-panel" aria-label="Your shared screen">
                <header className="meet-screen-head">
                  <span className="meet-share-text">
                    <b>Entire screen shared</b>
                    <span>Keep it shared until the end</span>
                  </span>
                  <button type="button" className="meet-ghost-btn" onClick={() => setSplit(false)} title="Back to the usual view (Esc)">
                    <Minimize2 size={16} /> Minimise
                  </button>
                </header>
                <figure className="meet-screen-full">
                  <VideoEl track={screen.getVideoTracks()[0]} contain />
                </figure>
              </section>
            )}
            <div className={`meet-tech-stage${warn.screen.on || warn.face.on || warn.people.on || warn.eyes.on || warn.tab.on ? " alert" : ""}`}>
              {!showSplit &&
                (shared && screen ? (
                  <button
                    type="button"
                    className="meet-share on"
                    onClick={() => setSplit(true)}
                    title="Show your shared screen beside Mitra"
                  >
                    <figure className="meet-share-thumb">
                      <VideoEl track={screen.getVideoTracks()[0]} contain />
                      <span className="meet-share-expand" aria-hidden="true">
                        <Maximize2 size={14} />
                      </span>
                    </figure>
                    <span className="meet-share-text">
                      <b>Entire screen shared</b>
                      <span>Click to view it beside Mitra</span>
                    </span>
                  </button>
                ) : (
                  <div className="meet-share off">
                    <span className="meet-share-icon" aria-hidden="true">
                      <MonitorOff size={22} />
                    </span>
                    <span className="meet-share-text">
                      <b>Screen not shared</b>
                      <span>{shareError ?? "Share your entire screen to carry on"}</span>
                    </span>
                  </div>
                ))}
              <MitraPresence name={facts.bot_name} track={mitra?.audio} state={mitraState} />
              <SelfView peer={me} muted={muted} />
            </div>
            <aside className="meet-rail" aria-labelledby="rules-title">
              <p className="meet-live">
                <span className="meet-live-dot" aria-hidden="true" />
                You are under live proctoring
              </p>
              <h2 className="meet-rail-title" id="rules-title">
                Instructions
              </h2>
              <ProctorRules face={face} gazeOff={gazeRemindersOff} warn={warn} />
              <h2 className="meet-rail-title">Warnings</h2>
              <div className="meet-warnings" aria-live="assertive">
                {!(warn.screen.on || warn.face.on || warn.people.on || warn.eyes.on || warn.tab.on) && (
                  <p className="meet-warnings-none">
                    <CircleCheck size={18} /> All clear. A warning will show here if something needs fixing.
                  </p>
                )}
                {warn.screen.on && (
                  <WarningCard tone="danger" icon={<MonitorOff size={26} />} count={warn.screen.count}>
                    Your screen is no longer shared.
                    <button type="button" className="meet-notice-btn" onClick={shareAgain}>
                      Share again
                    </button>
                  </WarningCard>
                )}
                {warn.face.on && (
                  <WarningCard icon={<ScanFace size={26} />} count={warn.face.count}>
                    We can't see you clearly. Please face your camera.
                  </WarningCard>
                )}
                {warn.people.on && (
                  <WarningCard icon={<Users size={26} />} count={warn.people.count}>
                    Please make sure only you are in view.
                  </WarningCard>
                )}
                {warn.eyes.on && (
                  <WarningCard icon={<Eye size={26} />} count={warn.eyes.count}>
                    Please keep your eyes on the screen.
                  </WarningCard>
                )}
                {warn.tab.on && (
                  <WarningCard icon={<AppWindow size={26} />} count={warn.tab.count}>
                    Please stay on the interview tab.
                  </WarningCard>
                )}
              </div>
            </aside>
          </section>
        ) : remoteScreen?.screen ? (
          <section className="meet-stage-main">
            <VideoEl track={remoteScreen.screen} contain />
            <span className="meet-name corner">{remoteScreen.name} is presenting</span>
          </section>
        ) : (
          <section className="meet-stage-main voice">
            <div className="meet-voices">
              {host ? (
                <VoiceTile name={host.name} track={host.audio} muted={!host.micOn} large />
              ) : (
                <VoiceTile name="HR" state={hostLeft ? "left" : "waiting"} large />
              )}
              <VoiceTile name={me?.name ?? "You"} track={me?.audio} muted={muted} you />
              {hosts.slice(1).map((p) => (
                <VoiceTile key={p.id} name={p.name} track={p.audio} muted={!p.micOn} />
              ))}
            </div>
            {!host && !hostLeft && <p className="meet-voice-hint">You are in the room. A member of the HR team will be with you shortly.</p>}
          </section>
        )}
      </main>

      <footer className="meet-controls">
        <span className="meet-status">
          {facts.recording && <span className="meet-rec-dot" aria-hidden="true" />}
          {status}
        </span>
        <div className="meet-buttons">
          <button
            type="button"
            className={`meet-round dark${muted ? " off" : ""}`}
            onClick={toggleMic}
            aria-pressed={muted}
            aria-label={muted ? "Unmute microphone (Ctrl or Cmd + D)" : "Mute microphone (Ctrl or Cmd + D)"}
            title={muted ? "Unmute (Ctrl or Cmd + D)" : "Mute (Ctrl or Cmd + D)"}
          >
            {muted ? <MicOff size={20} /> : <Mic size={20} />}
          </button>
{technical && (
          <button
            type="button"
            className={`meet-round dark${cam ? "" : " off"}${technical ? " locked" : ""}`}
            onClick={toggleCam}
            aria-disabled={technical}
            aria-label={technical ? "Your camera stays on during a proctored interview" : cam ? "Turn off camera (Ctrl or Cmd + E)" : "Turn on camera (Ctrl or Cmd + E)"}
            title={technical ? "Your camera stays on during a proctored interview" : cam ? "Turn off camera" : "Turn on camera"}
          >
            {cam ? <Video size={20} /> : <VideoOff size={20} />}
            {technical && <Lock size={11} className="meet-lock" aria-hidden="true" />}
          </button>
          )}
          {technical && (
            <button
              type="button"
              className={`meet-round dark${shared ? " active" : " off"}`}
              onClick={shared ? undefined : shareAgain}
              aria-disabled={shared}
              aria-label={shared ? "Your entire screen is shared" : "Share your entire screen again"}
              title={shared ? "Your entire screen is shared" : "Share again"}
            >
              {shared ? <MonitorUp size={20} /> : <MonitorOff size={20} />}
            </button>
          )}
          <div className="meet-help-wrap">
            <button
              type="button"
              className={`meet-round dark${help ? " active" : ""}`}
              onClick={() => setHelp((h) => !h)}
              aria-expanded={help}
              aria-label="Help with sound, camera or connection"
              title="Help"
            >
              <CircleHelp size={20} />
            </button>
            {help && (
              <div className="meet-help" role="dialog" aria-label="Help">
                <div className="meet-help-head">
                  <strong>Having trouble?</strong>
                  <button type="button" className="meet-icon-btn" onClick={() => setHelp(false)} aria-label="Close help">
                    <X size={16} />
                  </button>
                </div>
                <ul>
                  <li>
                    <b>{technical ? `${facts.bot_name} cannot hear you.` : "They cannot hear you."}</b> Check you are not muted (Ctrl or
                    Cmd + D) and that the right microphone is chosen in your browser.
                  </li>
                  <li>
                    <b>No sound.</b> Check your speaker or headphone volume.
                  </li>
                  {technical && (
                    <li>
                      <b>A question did not land.</b> Say "sorry, could you repeat that?" or "what do you mean?". Saying "I don't know" is
                      a fine answer.
                    </li>
                  )}
                  <li>
                    <b>The call dropped.</b> Reload this page and join again with the same meeting ID and password.
                    {technical ? ` ${facts.bot_name} carries on where you left off.` : ""}
                  </li>
                </ul>
              </div>
            )}
          </div>
        </div>
        <button type="button" className="meet-end" onClick={() => setConfirmEnd(true)}>
          <PhoneOff size={18} />
          {technical ? "End interview" : "Leave"}
        </button>
      </footer>

      {confirmEnd && (
        <div className="meet-scrim" role="presentation" onClick={() => setConfirmEnd(false)}>
          <section
            className="meet-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="end-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="end-title">{technical ? "Do you want to stop here?" : "Leave the conversation?"}</h2>
            <p>
              {technical
                ? "The interview ends for good and cannot be restarted. What you have said so far is kept, and the hiring team sees that you chose to stop, not that time ran out."
                : "If you leave by mistake, join again with the same meeting ID and password."}
            </p>
            <div className="meet-dialog-actions">
              <button type="button" className="meet-ghost" onClick={() => setConfirmEnd(false)} autoFocus>
                {technical ? "Keep going" : "Stay"}
              </button>
              <button type="button" className="meet-danger" onClick={() => finish(technical ? "ended" : hostLeft ? "complete" : "ended")}>
                {technical ? "Yes, end the interview" : "Leave"}
              </button>
            </div>
            {technical && <p className="meet-dialog-note">Saying "end the interview" to {facts.bot_name} asks this same question once.</p>}
          </section>
        </div>
      )}
    </div>
  );
}

/**
 * The rules, point by point, each with what is seen now and how many warnings it
 * has had. A rule turns amber only while its warning is showing, so the two never
 * disagree (the detector's raw state flickers; the warning waits a moment first).
 * Nothing is hidden from the person it is about. Facts, never a verdict.
 */
function ProctorRules({ face, gazeOff, warn }: { face: FaceState; gazeOff: boolean; warn: Record<RuleKey, Warning> }) {
  const checking = face.status === "loading" ? "Starting the camera check" : face.status === "unavailable" ? "Camera check unavailable" : null;
  const rules: { key: RuleKey; rule: string; ok: boolean | null; now: string; icon: ReactNode }[] = [
    { key: "face", rule: "Keep your face in view", ok: checking ? null : !warn.face.on, now: checking ?? (warn.face.on ? "Face not visible" : "Face visible"), icon: <ScanFace size={18} /> },
    {
      key: "people",
      rule: "Be alone in the room",
      ok: checking ? null : !warn.people.on,
      now: checking ?? (warn.people.on ? "More than one person in view" : "Only you in view"),
      icon: <Users size={18} />,
    },
    {
      key: "eyes",
      rule: "Keep your eyes on the screen",
      ok: gazeOff || checking ? null : !warn.eyes.on,
      now: gazeOff ? "Reminders off, as you chose" : (checking ?? (warn.eyes.on ? "Looking away" : "Eyes on the screen")),
      icon: <Eye size={18} />,
    },
    { key: "screen", rule: "Keep your entire screen shared", ok: !warn.screen.on, now: warn.screen.on ? "Screen not shared" : "Screen shared", icon: <MonitorUp size={18} /> },
    { key: "tab", rule: "Stay on this tab", ok: !warn.tab.on, now: warn.tab.on ? "You left the tab" : "On this tab", icon: <AppWindow size={18} /> },
  ];
  return (
    <ol className="meet-rules">
      {rules.map((r) => {
        const count = warn[r.key].count;
        return (
          <li key={r.key} className={r.ok === null ? "na" : r.ok ? "ok" : "warn"}>
            <span className="meet-rule-icon" aria-hidden="true">
              {r.icon}
            </span>
            <span className="meet-rule-text">
              <b>{r.rule}</b>
              <span>{r.now}</span>
            </span>
            <span className={`meet-rule-count${count ? "" : " zero"}`} title={`Warned ${count} ${count === 1 ? "time" : "times"}`}>
              {count}
              <span className="sr-only"> {count === 1 ? "warning" : "warnings"}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** One warning: large and bold so it reads from a distance, with how many times it has come up. */
function WarningCard({ tone = "amber", icon, count, children }: { tone?: "amber" | "danger"; icon: ReactNode; count: number; children: ReactNode }) {
  return (
    <div className={`meet-warning ${tone}`} role="alert">
      {icon}
      <span className="meet-warning-text">{children}</span>
      <span className="meet-warning-count">{timesLabel(count)}</span>
    </div>
  );
}
