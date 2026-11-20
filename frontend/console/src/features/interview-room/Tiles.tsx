import { AudioLines, Loader2, Mic, MicOff, MonitorUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { initials, useLevel, type Peer } from "./media";

export function VideoEl({ track, mirror, contain }: { track?: MediaStreamTrack; mirror?: boolean; contain?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.srcObject = track ? new MediaStream([track]) : null;
  }, [track]);
  return <video ref={ref} autoPlay playsInline muted className={`meet-video${mirror ? " mirror" : ""}${contain ? " contain" : ""}`} />;
}

/** A call object plays nothing by itself: each remote voice gets an element. */
export function AudioEl({ track }: { track?: MediaStreamTrack }) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.srcObject = track ? new MediaStream([track]) : null;
  }, [track]);
  return <audio ref={ref} autoPlay />;
}

/**
 * Mitra has no camera, and inventing a face for it would be worse than an
 * honest presence. The orb moves with Mitra's actual voice: its loudness is
 * written to a CSS variable thirty times a second.
 */
export function MitraPresence({
  name,
  track,
  state,
}: {
  name: string;
  track?: MediaStreamTrack;
  state: "joining" | "speaking" | "listening" | "gone";
}) {
  const orb = useRef<HTMLSpanElement>(null);
  useLevel(track, (level) => orb.current?.style.setProperty("--lvl", level.toFixed(3)));
  const label = {
    joining: `${name} is joining`,
    speaking: `${name} is speaking`,
    listening: "Listening",
    gone: `${name} has left`,
  }[state];
  return (
    <div className={`meet-mitra ${state}`}>
      <span className="meet-orb-wrap" aria-hidden="true">
        <span className="meet-orb-ring r1" />
        <span className="meet-orb-ring r2" />
        <span className="meet-orb" ref={orb} />
      </span>
      <span className="meet-mitra-name">{name}</span>
      <span className="meet-mitra-state" role="status" aria-live="polite">
        {state === "joining" && <Loader2 size={14} className="meet-spin" />}
        {label}
      </span>
    </div>
  );
}

/** The candidate's own picture, small in the corner, with a live mic level. */
export function SelfView({ peer, muted, sharing }: { peer?: Peer; muted: boolean; sharing?: boolean }) {
  const bars = useRef<HTMLSpanElement>(null);
  useLevel(muted ? undefined : peer?.audio, (level) => bars.current?.style.setProperty("--lvl", level.toFixed(3)));
  return (
    <figure className="meet-self">
      {peer?.camOn && peer.video ? (
        <VideoEl track={peer.video} mirror />
      ) : (
        <span className="meet-avatar small" aria-hidden="true">
          {initials(peer?.name ?? "You")}
        </span>
      )}
      <figcaption>
        {muted && <MicOff size={12} aria-label="Muted" />}
        You
      </figcaption>
      {!muted && (
        <span className="meet-meter mini" ref={bars} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      )}
      {sharing && (
        <span className="meet-self-share">
          <MonitorUp size={12} /> Screen shared
        </span>
      )}
    </figure>
  );
}

/**
 * One voice: initials in a circle whose rings swell with the speaker's actual
 * loudness (a CSS variable, written thirty times a second), and a word for
 * what they are doing.
 */
export function VoiceTile({
  name,
  track,
  muted,
  state,
  you = false,
  large = false,
}: {
  name: string;
  track?: MediaStreamTrack;
  muted?: boolean;
  state?: "waiting" | "left";
  you?: boolean;
  large?: boolean;
}) {
  const ring = useRef<HTMLDivElement>(null);
  const [speaking, setSpeaking] = useState(false);
  const since = useRef(0);
  useLevel(muted || state ? undefined : track, (level) => {
    ring.current?.style.setProperty("--lvl", level.toFixed(3));
    const now = performance.now();
    if (level > 0.06) {
      since.current = now;
      setSpeaking(true);
    } else if (now - since.current > 500) setSpeaking(false);
  });
  const label = state === "waiting" ? "Waiting to join" : state === "left" ? "Left the call" : muted ? "Muted" : speaking ? "Speaking" : "Listening";
  return (
    <div className={`voice-tile${large ? " large" : ""}${speaking ? " speaking" : ""}${state ? ` ${state}` : ""}`} ref={ring}>
      <span className="voice-rings" aria-hidden="true">
        <i />
        <i />
      </span>
      <span className="voice-avatar" aria-hidden="true">
        {initials(name)}
      </span>
      <span className="voice-name">{you ? "You" : name}</span>
      <span className="voice-state" aria-live="polite">
        {muted ? <MicOff size={14} /> : speaking ? <AudioLines size={14} /> : <Mic size={14} />}
        {label}
      </span>
    </div>
  );
}

