// The HR round's room: a voice conversation between HR and the candidate. No
// AI takes part and no camera is used. Built on Daily's call object:
// - a lobby with a microphone check and device choice, then the room;
// - in the room, the two voices on the stage (each moves with its speaker's
//   actual loudness), the live transcript under them, and the questions and
//   notes beside it;
// - HR's owner token starts the recording, and HR's room starts Daily's live
//   transcription. Each finished line is tagged with the participant who spoke
//   it and saved as it arrives (POST /hr-round/transcript), so the evaluation
//   starts from it the moment the round ends (backend/hiring/hr_round.py).

import DailyIframe, { type DailyCall, type DailyMediaDeviceInfo, type DailyParticipant } from "@daily-co/daily-js";
import { ArrowLeftRight, ClipboardCopy, Ear, Loader2, LogOut, Mic, MicOff, PhoneCall, PhoneOff, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { initials, type HRRoundView } from "../../contracts";
import { copyText } from "../../shell/ui/CopyField";
import { useToast } from "../../shell/ui/Toast";
import { useLevel } from "../interview-room/media";
import { NotesPanel, QuestionsPanel, type Progress } from "./RoomPanel";

/**
 * Daily's live transcription (Deepgram): the newest model in mixed-language
 * mode, for Indian English and Hindi-English as spoken. With no model named,
 * Daily's default gave fragments on real rounds (measured 2026-11-19).
 */
const TRANSCRIPTION = {
  model: "nova-3",
  language: "multi",
  punctuate: true,
  includeRawResponse: true,
  extra: { interim_results: true, smart_format: true },
};
/** What the room used before, if Daily refuses the settings above. */
const PLAIN_TRANSCRIPTION = { language: "en", punctuate: true, includeRawResponse: true, extra: { interim_results: true } };

// ---- listen mode: HR's phone call, on speaker, heard through HR's microphone ----------------------

/** Both voices reach one microphone, so Deepgram tells them apart by voice (diarize). */
const LISTEN_TRANSCRIPTION = { ...TRANSCRIPTION, extra: { ...TRANSCRIPTION.extra, diarize: true } };
const LISTEN_PLAIN_TRANSCRIPTION = { ...PLAIN_TRANSCRIPTION, extra: { ...PLAIN_TRANSCRIPTION.extra, diarize: true } };
/** The phone's speaker is the candidate's voice: the browser's echo cancellation and noise
 * suppression would treat it as noise to remove. Gain control keeps a quiet speaker audible. */
const LISTEN_MIC: MediaTrackConstraints = { echoCancellation: false, noiseSuppression: false, autoGainControl: true };

type DeepgramWords = { channel?: { alternatives?: { words?: { speaker?: number }[] }[] } };

/** The voice that said most of a line, as Deepgram numbers voices (0, 1, ...). */
function voiceOf(raw: unknown): number | null {
  const words = (raw as DeepgramWords | undefined)?.channel?.alternatives?.[0]?.words ?? [];
  const counts = new Map<number, number>();
  for (const w of words) if (typeof w.speaker === "number") counts.set(w.speaker, (counts.get(w.speaker) ?? 0) + 1);
  let best: number | null = null;
  for (const [voice, n] of counts) if (best === null || n > (counts.get(best) ?? 0)) best = voice;
  return best;
}

const STOP = new Set("the and for with about your what have that this from they them their you are was were how why when where which would could".split(" "));
function words(text: string): Set<string> {
  return new Set(text.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter((w) => w.length > 3 && !STOP.has(w)));
}
/** Does this line ask one of the HR questions? Then its voice is HR's. */
function asksAPlannedQuestion(text: string, questions: { text: string }[]): boolean {
  const said = words(text);
  return questions.some((q) => {
    const want = words(q.text);
    if (want.size < 3) return false;
    let hit = 0;
    for (const w of want) if (said.has(w)) hit += 1;
    return hit / want.size >= 0.5;
  });
}

type Phase = "lobby" | "joining" | "in" | "left";
type Speaker = "hr" | "candidate" | "other";
type Transcribing = "off" | "starting" | "on" | "unavailable";

interface Peer {
  id: string;
  name: string;
  local: boolean;
  audio?: MediaStreamTrack;
  micOn: boolean;
}

interface Line {
  id: string;
  speaker: Speaker;
  who: string;
  text: string;
  start: number;
  end: number;
  final: boolean;
}

function toPeer(p: DailyParticipant): Peer {
  const a = p.tracks.audio as { state: string; persistentTrack?: MediaStreamTrack };
  return {
    id: p.session_id,
    name: p.user_name || (p.local ? "You" : "Candidate"),
    local: p.local,
    audio: a.state === "playable" ? a.persistentTrack : undefined,
    micOn: ["playable", "loading", "sendable"].includes(a.state),
  };
}

export function invitation(view: HRRoundView): string {
  const r = view.round!;
  return [
    `Hello ${view.candidate.name},`,
    "",
    `Your HR conversation for ${view.job.title} is ready. Join here: ${r.join_url}`,
    `Meeting ID: ${r.meeting_id}`,
    `Password: ${r.password}`,
    "",
    "It is a voice call with a member of our HR team, no camera needed. It is recorded and transcribed.",
  ].join("\n");
}

function clock(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function AudioEl({ track }: { track?: MediaStreamTrack }) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.srcObject = track ? new MediaStream([track]) : null;
  }, [track]);
  return <audio ref={ref} autoPlay />;
}

// ---- the invitation card -------------------------------------------------------------------------

export function InviteCard({ view, onCancel, cancelling }: { view: HRRoundView; onCancel?: () => void; cancelling?: boolean }) {
  const toast = useToast();
  const r = view.round!;
  const rows: [string, string][] = [
    ["Meeting ID", r.meeting_id ?? ""],
    ["Password", r.password ?? ""],
    ["Join page", r.join_url ?? ""],
  ];
  return (
    <section className="invite-card" aria-label="Invite the candidate">
      <h2>Invite {view.candidate.name}</h2>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              <span>{value}</span>
              <button
                type="button"
                className="invite-copy"
                aria-label={`Copy the ${label.toLowerCase()}`}
                onClick={async () => {
                  if (await copyText(value)) toast(`${label} copied`);
                }}
              >
                <ClipboardCopy size={14} />
              </button>
            </dd>
          </div>
        ))}
      </dl>
      <div className="invite-actions">
        <button
          type="button"
          className="room-btn-text primary"
          onClick={async () => {
            if (await copyText(invitation(view))) toast("Invitation copied. Paste it into an email or a message.");
          }}
        >
          <ClipboardCopy size={15} /> Copy invitation
        </button>
        {onCancel && r.can_cancel && (
          <button type="button" className="room-btn-text quiet" onClick={onCancel} disabled={cancelling}>
            {cancelling ? "Cancelling" : "Cancel invitation"}
          </button>
        )}
      </div>
    </section>
  );
}

// ---- the live transcript -------------------------------------------------------------------------

function LiveTranscript({ lines, status, candidateName }: { lines: Line[]; status: Transcribing; candidateName: string }) {
  const box = useRef<HTMLOListElement>(null);
  const pinned = useRef(true);
  // Follow the newest message unless HR has scrolled up to read something.
  useEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [lines]);
  return (
    <section className="chat" aria-label="Live transcript">
      <header className="chat-head">
        <div>
          <h2>Live transcript</h2>
          <p>
            <span className="chat-key hr">You</span>
            <span className="chat-key cand">{candidateName}</span>
          </p>
        </div>
        <span className={`live-status ${status}`}>
          <span className="live-status-dot" aria-hidden="true" />
          {status === "on" ? "Transcribing" : status === "starting" ? "Starting" : status === "unavailable" ? "Not available" : "Off"}
        </span>
      </header>
      <ol
        ref={box}
        className="chat-list"
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
      >
        {lines.length === 0 && (
          <li className="chat-empty">
            {status === "unavailable"
              ? "Live transcription could not start. The call is still recorded, and MITRA transcribes it after the round."
              : `What you and ${candidateName} say appears here as you speak.`}
          </li>
        )}
        {lines.map((l) => (
          <li key={l.id} className={`msg ${l.speaker === "hr" ? "mine" : l.speaker}${l.final ? "" : " interim"}`}>
            <span className="msg-meta">
              {l.speaker === "hr" ? "You" : l.who}
              <span>{clock(l.start * 1000)}</span>
            </span>
            <p className="msg-bubble">{l.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** One person in the voice module: who, a level that moves with their voice, and what they are doing. */
function VoiceRow({ name, track, muted, you, state }: { name: string; track?: MediaStreamTrack; muted?: boolean; you?: boolean; state?: "waiting" | "left" }) {
  const bars = useRef<HTMLSpanElement>(null);
  const [speaking, setSpeaking] = useState(false);
  const since = useRef(0);
  useLevel(muted || state ? undefined : track, (level) => {
    bars.current?.style.setProperty("--lvl", level.toFixed(3));
    const now = performance.now();
    if (level > 0.06) {
      since.current = now;
      setSpeaking(true);
    } else if (now - since.current > 500) setSpeaking(false);
  });
  const label = state === "waiting" ? "Not joined yet" : state === "left" ? "Left the call" : muted ? "Muted" : speaking ? "Speaking" : "Listening";
  return (
    <div className={`vrow${speaking ? " speaking" : ""}${state ? ` ${state}` : ""}`}>
      <span className="vrow-avatar" aria-hidden="true">
        {initials(name)}
      </span>
      <span className="vrow-who">
        <strong>{you ? "You" : name}</strong>
        <span>
          {muted ? <MicOff size={13} /> : <Mic size={13} />} {label}
        </span>
      </span>
      <span className="vrow-wave" ref={bars} aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}

// ---- the room ------------------------------------------------------------------------------------

/** Creating and destroying call objects one after another (see the effect below). */
let callQueue: Promise<void> = Promise.resolve();

const PREVIEW_PEERS: Peer[] = [
  { id: "c", name: "Candidate", local: false, micOn: true },
  { id: "me", name: "You", local: true, micOn: true },
];
const PREVIEW_LINES: Line[] = [
  { id: "p1", speaker: "hr", who: "You", text: "Thanks for joining. To start, what made you apply for this role?", start: 12, end: 16, final: true },
  { id: "p2", speaker: "candidate", who: "Candidate", text: "I have been building retrieval systems for two years and I want to own one end to end.", start: 18, end: 25, final: true },
  { id: "p3", speaker: "candidate", who: "Candidate", text: "The team here ships to real clients, which", start: 26, end: 28, final: false },
];

function savedLines(view: HRRoundView): Line[] {
  const r = view.round;
  if (!r?.transcript || r.attribution?.method !== "live") return [];
  return r.transcript.map((t) => ({
    id: (t as { id?: string }).id ?? `saved-${t.index}`,
    speaker: t.speaker,
    who: t.speaker === "hr" ? "You" : t.speaker === "candidate" ? view.candidate.name : "Other voice",
    text: t.text,
    start: t.start,
    end: t.end,
    final: true,
  }));
}

export function MeetingRoom({
  view,
  progress,
  onEnded,
  onCancel,
  preview = false,
}: {
  view: HRRoundView;
  progress: Progress;
  onEnded: (v: HRRoundView) => void;
  onCancel: () => Promise<void>;
  /** Development only: renders the room with stand-in participants and no call. */
  preview?: boolean;
}) {
  const toast = useToast();
  const round = view.round!;
  const listen = round.mode === "listen";
  /** Listen mode: which Deepgram voice is HR's (first heard, or the one asking an HR question). */
  const hrVoice = useRef<number | null>(null);
  // Read through a ref, never as a dependency: the page refreshes the round every few
  // seconds, which makes a new `questions` array, and a new `onLine` would restart the
  // call effect below, leaving and destroying the live call while the screen still
  // says "On call" (the bug that left a phone-call round with no transcript).
  const questionsRef = useRef(view.questions);
  questionsRef.current = view.questions;
  const voices = useRef(new Set<number>());
  const [consented, setConsented] = useState(Boolean(round.phone_consent_at));
  const [onSpeaker, setOnSpeaker] = useState(Boolean(round.phone_consent_at));
  const call = useRef<DailyCall | null>(null);
  const [phase, setPhase] = useState<Phase>(preview ? "in" : "lobby");
  const [peers, setPeers] = useState<Peer[]>(preview ? PREVIEW_PEERS : []);
  const [mic, setMic] = useState(true);
  const [weak, setWeak] = useState(false);
  const [recording, setRecording] = useState(round.recording_status === "recording");
  const [joinedAt, setJoinedAt] = useState<number | null>(preview ? Date.now() - 754_000 : null);
  const [now, setNow] = useState(Date.now());
  const [devices, setDevices] = useState<DailyMediaDeviceInfo[]>([]);
  const [chosen, setChosen] = useState<{ mic?: string; out?: string }>({});
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [ending, setEnding] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>(() => (preview ? PREVIEW_LINES : savedLines(view)));
  const [transcribing, setTranscribing] = useState<Transcribing>(preview ? "on" : "off");
  const unsent = useRef<Line[]>([]);
  const peersRef = useRef<Peer[]>([]);
  peersRef.current = peers;
  const joinedRef = useRef<number | null>(null);
  joinedRef.current = joinedAt;

  const refresh = useCallback(() => {
    const c = call.current;
    if (!c) return;
    setPeers(Object.values(c.participants()).map(toPeer));
    const me = c.participants().local;
    if (me) setMic(me.tracks.audio.state !== "off" && me.tracks.audio.state !== "blocked");
  }, []);

  // A line from Daily's transcription: who said it, and whether it is final.
  const onLine = useCallback(
    (e?: { participantId?: string; text?: string; timestamp?: Date | string; rawResponse?: { is_final?: boolean } }) => {
      if (!e?.text?.trim() || !e.participantId) return;
      if (listen) {
        const voice = voiceOf(e.rawResponse) ?? -1;
        const final = (e.rawResponse as { is_final?: boolean } | undefined)?.is_final !== false;
        if (voice >= 0) voices.current.add(voice);
        if (hrVoice.current === null && voice >= 0) hrVoice.current = voice;
        // A voice asking one of the HR questions is HR's, whoever spoke first.
        if (final && voice >= 0 && voice !== hrVoice.current && asksAPlannedQuestion(e.text, questionsRef.current)) {
          hrVoice.current = voice;
          setLines((all) => all.map((l) => (l.speaker === "hr" ? { ...l, speaker: "candidate", who: view.candidate.name } : l.speaker === "candidate" ? { ...l, speaker: "hr", who: "You" } : l)));
        }
        const speaker: Speaker = voice === hrVoice.current ? "hr" : "candidate";
        const at = e.timestamp ? new Date(e.timestamp).getTime() : Date.now();
        const start = Math.max(0, (at - (joinedRef.current ?? at)) / 1000);
        const interimId = `interim-voice-${voice}`;
        const line: Line = {
          id: final ? `voice${voice}-${at}` : interimId,
          speaker,
          who: speaker === "hr" ? "You" : view.candidate.name,
          text: e.text.trim(),
          start,
          end: start,
          final,
        };
        setLines((all) => [...all.filter((l) => l.id !== interimId), line]);
        if (final) unsent.current.push(line);
        return;
      }
      const people = peersRef.current;
      const speakerPeer = people.find((p) => p.id === e.participantId);
      const candidateId = people.find((p) => !p.local)?.id;
      const speaker: Speaker = speakerPeer?.local ? "hr" : e.participantId === candidateId ? "candidate" : "other";
      const at = e.timestamp ? new Date(e.timestamp).getTime() : Date.now();
      const start = Math.max(0, (at - (joinedRef.current ?? at)) / 1000);
      const final = e.rawResponse?.is_final !== false;
      const interimId = `interim-${e.participantId}`;
      const line: Line = {
        id: final ? `${e.participantId}-${at}` : interimId,
        speaker,
        who: speaker === "hr" ? "You" : speaker === "candidate" ? view.candidate.name : speakerPeer?.name ?? "Other voice",
        text: e.text.trim(),
        start,
        end: start,
        final,
      };
      setLines((all) => {
        const rest = all.filter((l) => l.id !== interimId);
        return [...rest, line];
      });
      if (final) unsent.current.push(line);
    },
    [view.candidate.name, listen],
  );

  // Create the call object (voice only), check the microphone, and listen.
  // Daily allows one call object at a time and destroys them asynchronously,
  // so creating waits for any earlier one to be gone.
  useEffect(() => {
    if (preview) return;
    let cancelled = false;
    let c: DailyCall | null = null;
    const events = ["participant-joined", "participant-updated", "participant-left", "track-started", "track-stopped"] as const;
    const onQuality = (e?: { threshold?: string }) => setWeak(e?.threshold === "low" || e?.threshold === "very-low");
    const onRecStart = () => setRecording(true);
    const onRecStop = () => setRecording(false);
    const onLeft = () => setPhase("left");
    const onError = (e?: { errorMsg?: string }) => setError(e?.errorMsg ?? "The call ran into a problem.");
    const onTxStart = () => setTranscribing("on");
    const onTxStop = () => setTranscribing((t) => (t === "unavailable" ? t : "off"));
    let triedPlain = false;
    const onTxError = (e?: { errorMsg?: string }) => {
      // Already running (HR rejoined) is not a failure.
      if (/already/i.test(e?.errorMsg ?? "")) {
        setTranscribing("on");
        return;
      }
      // If Daily refuses the chosen model or language, fall back once to its
      // own default rather than leave HR with no live transcript.
      if (!triedPlain && c) {
        triedPlain = true;
        try {
          c.startTranscription(listen ? LISTEN_PLAIN_TRANSCRIPTION : PLAIN_TRANSCRIPTION);
          return;
        } catch {
          // fall through
        }
      }
      setTranscribing("unavailable");
    };

    callQueue = callQueue.then(async () => {
      const leftover = DailyIframe.getCallInstance();
      if (leftover) await leftover.destroy().catch(() => undefined);
      if (cancelled) return;
      let audioSource: MediaStreamTrack | boolean = true;
      if (listen) {
        // The phone's speaker must not be filtered out as echo or noise.
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: LISTEN_MIC });
          audioSource = stream.getAudioTracks()[0] ?? true;
        } catch {
          audioSource = true;
        }
        if (cancelled) return;
      }
      const created = DailyIframe.createCallObject({ subscribeToTracksAutomatically: true, videoSource: false, audioSource });
      c = created;
      call.current = created;
      events.forEach((e) => created.on(e, refresh));
      created.on("network-quality-change", onQuality);
      created.on("recording-started", onRecStart);
      created.on("recording-stopped", onRecStop);
      created.on("left-meeting", onLeft);
      created.on("error", onError);
      created.on("transcription-started", onTxStart);
      created.on("transcription-stopped", onTxStop);
      created.on("transcription-error", onTxError);
      created.on("transcription-message", onLine);
      try {
        await created.startCamera({ startVideoOff: true });
        if (cancelled) return;
        refresh();
        const { devices: list } = await created.enumerateDevices();
        if (!cancelled) setDevices(list);
      } catch {
        if (!cancelled) setError("Your microphone is not available. Check the browser's permission, then reload.");
      }
    });

    return () => {
      cancelled = true;
      call.current = null;
      callQueue = callQueue.then(async () => {
        if (!c) return;
        events.forEach((e) => c!.off(e, refresh));
        c.off("network-quality-change", onQuality);
        c.off("recording-started", onRecStart);
        c.off("recording-stopped", onRecStop);
        c.off("left-meeting", onLeft);
        c.off("error", onError);
        c.off("transcription-started", onTxStart);
        c.off("transcription-stopped", onTxStop);
        c.off("transcription-error", onTxError);
        c.off("transcription-message", onLine);
        await c.leave().catch(() => undefined);
        await c.destroy().catch(() => undefined);
      });
    };
  }, [preview, refresh, onLine, listen]);

  // Save finished lines every couple of seconds. A failed save keeps them for the next try.
  const flush = useCallback(async () => {
    if (preview || unsent.current.length === 0) return;
    const batch = unsent.current.slice(0, 200);
    try {
      await api.post(`/candidates/${view.candidate.id}/hr-round/transcript`, {
        lines: batch.map((l) => ({ id: l.id, speaker: l.speaker, text: l.text, start: l.start, end: l.end })),
      });
      unsent.current = unsent.current.slice(batch.length);
    } catch {
      // kept for the next attempt
    }
  }, [preview, view.candidate.id]);
  useEffect(() => {
    if (phase !== "in") return;
    const t = window.setInterval(flush, 2000);
    return () => window.clearInterval(t);
  }, [phase, flush]);

  // The clock.
  useEffect(() => {
    if (phase !== "in") return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [phase]);

  const toggleMic = useCallback(() => {
    if (preview) return setMic((v) => !v);
    call.current?.setLocalAudio(!mic);
    setMic(!mic);
  }, [mic, preview]);

  // Meet's shortcut: Ctrl/Cmd+D for the microphone.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
        e.preventDefault();
        toggleMic();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleMic]);

  async function join() {
    const c = call.current;
    if (!c) return;
    setPhase("joining");
    setError(null);
    try {
      const { room_url, token, transcribe } = await api.post<{ room_url: string; token: string; transcribe?: boolean }>(
        `/candidates/${view.candidate.id}/hr-round/join`,
        listen ? { phone_consent: consented } : undefined,
      );
      await c.join({ url: room_url, token, startAudioOff: !mic, startVideoOff: true });
      const at = Date.now();
      setJoinedAt((prev) => prev ?? at);
      setPhase("in");
      refresh();
      if (transcribe) {
        setTranscribing("starting");
        try {
          c.startTranscription(listen ? LISTEN_TRANSCRIPTION : TRANSCRIPTION);
        } catch {
          setTranscribing("unavailable");
        }
      }
    } catch (e) {
      setError((e as Error).message || "Could not join the room.");
      setPhase("lobby");
    }
  }

  async function choose(kind: "mic" | "out", deviceId: string) {
    setChosen((c) => ({ ...c, [kind]: deviceId }));
    const c = call.current;
    if (!c) return;
    if (kind === "out") await c.setOutputDeviceAsync({ outputDeviceId: deviceId });
    else await c.setInputDevicesAsync({ audioDeviceId: deviceId });
  }

  /** Listen mode: the voices were labelled the wrong way round. Fixes the lines on screen,
   * the ones not yet saved, the ones saved, and the lines still to come. */
  async function swapSpeakers() {
    const other = [...voices.current].find((v) => v !== hrVoice.current);
    hrVoice.current = other ?? null;
    const flip = (l: Line): Line =>
      l.speaker === "hr" ? { ...l, speaker: "candidate", who: view.candidate.name } : l.speaker === "candidate" ? { ...l, speaker: "hr", who: "You" } : l;
    setLines((all) => all.map(flip));
    unsent.current = unsent.current.map(flip);
    try {
      await api.post(`/candidates/${view.candidate.id}/hr-round/transcript/swap`);
      toast("Speakers swapped");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function end() {
    setEnding(true);
    setError(null);
    try {
      await flush();
      try {
        call.current?.stopTranscription();
      } catch {
        // not running
      }
      const v = await api.post<HRRoundView>(`/candidates/${view.candidate.id}/hr-round/end`);
      await call.current?.leave().catch(() => undefined);
      toast("HR round ended. MITRA is evaluating the conversation.");
      onEnded(v);
    } catch (e) {
      setError((e as Error).message);
      setEnding(false);
      setConfirmEnd(false);
    }
  }

  async function cancel() {
    setCancelling(true);
    try {
      await onCancel();
    } finally {
      setCancelling(false);
    }
  }

  const me = peers.find((p) => p.local);
  const others = peers.filter((p) => !p.local);
  const candidate = others[0];
  // Once the candidate has been in the room, an empty room means they left.
  const candidateSeen = useRef(false);
  if (candidate) candidateSeen.current = true;
  const candidateLeft = phase === "in" && candidateSeen.current && !candidate;
  const startedAt = joinedAt ?? (round.started_at ? Date.parse(round.started_at) : null);

  // ---- before joining: a microphone check and the joining details ----
  if (phase === "lobby" || phase === "joining") {
    const pick = (kind: "audioinput" | "audiooutput") => devices.filter((d) => d.kind === kind);
    return (
      <div className="vjoin">
        <section className="vjoin-card" aria-labelledby="vjoin-title">
          <div>
            <h2 id="vjoin-title" className="lobby-title">
              {listen ? "Before you call" : "Get ready to talk"}
            </h2>
            <p className="room-muted">
              {listen
                ? `You call ${view.candidate.name} from your own phone. MITRA listens through this laptop's microphone, transcribes the call and shows you the questions. It is recorded, and MITRA evaluates it when you end the round.`
                : `A voice call with ${view.candidate.name}. No camera. It is recorded and transcribed live, and MITRA evaluates it when you end the round.`}
            </p>
          </div>
          {listen && (
            <ol className="listen-steps">
              <li>Call {view.candidate.name} from your phone and put it on speaker.</li>
              <li>Keep the phone close to this laptop, with its speaker facing the microphone.</li>
              <li>Ask them to say a few words: the level below should move for their voice as well as yours.</li>
            </ol>
          )}
          <VoiceRow name={me?.name ?? "You"} track={me?.audio} muted={!mic} you />
          <p className="vjoin-hint">{mic ? "Say a few words. The level moves when your microphone hears you." : "Your microphone is muted."}</p>
          {listen && (
            <div className="listen-ticks">
              <label>
                <input type="checkbox" checked={onSpeaker} onChange={(e) => setOnSpeaker(e.target.checked)} />
                <span>My phone is on speaker, near this laptop.</span>
              </label>
              <label>
                <input type="checkbox" checked={consented} onChange={(e) => setConsented(e.target.checked)} />
                <span>{view.candidate.name} knows this call is recorded and transcribed by MITRA, and agreed.</span>
              </label>
            </div>
          )}
          {!listen && devices.length > 0 && (
            <div className="lobby-devices two">
              {(
                [
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
            </div>
          )}
          {error && <p className="room-error">{error}</p>}
          <div className="vjoin-actions">
            <button type="button" className="room-join" onClick={join} disabled={phase === "joining" || (listen && !(consented && onSpeaker))}>
              {phase === "joining" ? <Loader2 size={18} className="spin" /> : listen ? <Ear size={18} /> : <PhoneCall size={18} />}
              {phase === "joining" ? "Joining" : listen ? "Start listening" : "Join the call"}
            </button>
            <button type="button" className={mic ? "vbtn" : "vbtn off"} onClick={toggleMic} aria-pressed={!mic}>
              {mic ? <Mic size={17} /> : <MicOff size={17} />} {mic ? "Mute" : "Unmute"}
            </button>
          </div>
        </section>
        {!listen && <InviteCard view={view} onCancel={cancel} cancelling={cancelling} />}
      </div>
    );
  }

  // ---- after leaving ----
  if (phase === "left") {
    return (
      <section className="vjoin-card vleft">
        <h2 className="lobby-title">You left the call</h2>
        <p className="room-muted">The round is still open for {view.candidate.name}. Rejoin, or end the round to start MITRA's evaluation.</p>
        {error && <p className="room-error">{error}</p>}
        <div className="vjoin-actions">
          <button type="button" className="room-join" onClick={join}>
            <PhoneCall size={18} /> Rejoin
          </button>
          <button type="button" className="vbtn end" onClick={end} disabled={ending}>
            <PhoneOff size={17} /> {ending ? "Ending" : "End round"}
          </button>
        </div>
      </section>
    );
  }

  // ---- in the call: the voice module, the live transcript, the questions and notes ----
  return (
    <div className="vroom">
      {others.map((p) => (
        <AudioEl key={p.id} track={p.audio} />
      ))}

      <section className="vmod" aria-label="The call">
        <div className="vmod-status">
          <span className="vmod-live">
            <span className="vmod-dot" aria-hidden="true" /> On call
          </span>
          <span className="vmod-clock" aria-label="Time on the call">
            {startedAt ? clock(now - startedAt) : "0:00"}
          </span>
        </div>
        <div className="vmod-pills">
          {recording && (
            <span className="room-pill rec">
              <span className="rec-dot" aria-hidden="true" /> Recording
            </span>
          )}
          {weak && (
            <span className="room-pill warn" title="Your connection is weak">
              <WifiOff size={13} /> Weak connection
            </span>
          )}
        </div>

        {listen ? (
          <div className="vmod-people">
            <VoiceRow name="Your microphone, with the phone on speaker" track={me?.audio} muted={!mic} you />
            <p className="vmod-note">
              Both voices come through this microphone. If the transcript has you and {view.candidate.name} the wrong way round, swap them.
            </p>
            <button type="button" className="room-btn-text primary" onClick={swapSpeakers}>
              <ArrowLeftRight size={15} /> Swap speakers
            </button>
          </div>
        ) : (
        <div className="vmod-people">
          {candidate ? (
            <VoiceRow name={candidate.name} track={candidate.audio} muted={!candidate.micOn} />
          ) : (
            <VoiceRow name={view.candidate.name} state={candidateLeft ? "left" : "waiting"} />
          )}
          <VoiceRow name={me?.name ?? "You"} track={me?.audio} muted={!mic} you />
          {others.slice(1).map((p) => (
            <VoiceRow key={p.id} name={p.name} track={p.audio} muted={!p.micOn} />
          ))}
        </div>
        )}

        {!listen && candidateLeft && (
          <p className="vmod-note">{view.candidate.name} left the call. If the conversation is over, end the round and MITRA starts evaluating it.</p>
        )}
        {!listen && !candidate && !candidateLeft && (
          <button
            type="button"
            className="room-btn-text primary"
            onClick={async () => {
              if (await copyText(invitation(view))) toast("Invitation copied");
            }}
          >
            <ClipboardCopy size={15} /> Copy the invitation again
          </button>
        )}

        <div className="vmod-controls">
          <button type="button" className={mic ? "vbtn" : "vbtn off"} onClick={toggleMic} aria-pressed={!mic} title="Ctrl or Cmd + D">
            {mic ? <Mic size={17} /> : <MicOff size={17} />} {mic ? "Mute" : "Unmute"}
          </button>
          <button
            type="button"
            className="vbtn"
            onClick={() => (preview ? setPhase("left") : call.current?.leave())}
            title="Leave the call; the round stays open"
          >
            <LogOut size={17} /> Leave call
          </button>
          {confirmEnd ? (
            <div className="vmod-confirm" role="dialog" aria-label="End the round">
              <span>End the round for everyone? The call and recording stop, and MITRA evaluates the conversation.</span>
              <span className="vmod-confirm-actions">
                <button type="button" className="vbtn" onClick={() => setConfirmEnd(false)}>
                  Not yet
                </button>
                <button type="button" className="vbtn end" onClick={end} disabled={ending}>
                  <PhoneOff size={17} /> {ending ? "Ending" : "End round"}
                </button>
              </span>
            </div>
          ) : (
            <button type="button" className="vbtn end wide" onClick={() => setConfirmEnd(true)}>
              <PhoneOff size={17} /> End round
            </button>
          )}
        </div>
        <p className="vmod-asked">
          {progress.asked.length} of {view.questions.length} questions asked
        </p>
        {error && <p className="room-error">{error}</p>}
      </section>

      <LiveTranscript lines={lines} status={transcribing} candidateName={view.candidate.name} />

      <aside className="vroom-side">
        <div className="room-panel compact">
          <QuestionsPanel questions={view.questions} progress={progress} editable={round.can_edit_notes} live />
        </div>
        <div className="room-panel notes">
          <NotesPanel progress={progress} editable={round.can_edit_notes} />
        </div>
      </aside>
    </div>
  );
}
