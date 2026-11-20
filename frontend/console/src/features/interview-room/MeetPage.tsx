// The candidate's interview room: one page, five steps.
//
//   enter    meeting ID and password, checked by /api/room/lookup
//   consent  what will happen, each item agreed to separately
//   lobby    camera, microphone and (MITRA interview round) the entire screen, checked
//   room     the call: Mitra for the MITRA interview round, a person for the HR round
//   done     what happens next, and the candidate's rights over their data
//
// The Daily call object is created in the lobby and carried into the room, so
// the camera is opened once. Nothing on this page knows how the candidate is
// evaluated, and nothing the candidate does here reaches the interviewer.

import DailyIframe, { type DailyCall } from "@daily-co/daily-js";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ConsentStep } from "./ConsentStep";
import { DoneStep, type Ending } from "./DoneStep";
import { EnterStep } from "./EnterStep";
import { Lobby } from "./Lobby";
import { toPeer, type Peer } from "./media";
import { previewCall, previewFacts, previewPeers, previewStep } from "./preview";
import { lookup, recall, remember, type Credentials, type JoinResult, type RoomFacts } from "./room-api";
import type { ProctoringPass } from "./proctoring/useIntegrity";
import { Room } from "./Room";
import "./meet.css";

type Step = "enter" | "consent" | "lobby" | "room" | "done";

/**
 * Video is for a person to review afterwards and is never scored, so it is
 * capped at capture (640x480, 15 fps) and sent as one 200 kbps layer. Audio is
 * what the interview rests on and must never be starved to carry video.
 */
function createCall(voiceOnly: boolean): DailyCall {
  return DailyIframe.createCallObject({
    // The HR round is a voice call: no camera is opened at all.
    ...(voiceOnly ? { videoSource: false } : {}),
    subscribeToTracksAutomatically: true,
    inputSettings: {
      video: {
        settings: { width: { ideal: 640, max: 640 }, height: { ideal: 480, max: 480 }, frameRate: { ideal: 15, max: 15 } },
      },
    },
    sendSettings: {
      video: { encodings: { low: { maxBitrate: 200_000, maxFramerate: 15, scaleResolutionDownBy: 1 } } },
    },
  });
}

/**
 * Daily allows one call object at a time and destroys them asynchronously, so
 * every create waits for the previous destroy. Without this a remount (React's
 * development double-mount, or a rejoin) asks for a second call object while
 * the first is still going, and Daily refuses.
 */
let callQueue: Promise<void> = Promise.resolve();

/** The call object and its participants, alive while `active`. */
function useCall(active: boolean, generation: number, voiceOnly = false) {
  const [call, setCall] = useState<DailyCall | null>(null);
  const [peers, setPeers] = useState<Peer[]>([]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let c: DailyCall | null = null;
    const events = ["participant-joined", "participant-updated", "participant-left", "track-started", "track-stopped"] as const;
    const refresh = () => c && setPeers(Object.values(c.participants()).map(toPeer));

    callQueue = callQueue.then(async () => {
      const leftover = DailyIframe.getCallInstance();
      if (leftover) await leftover.destroy().catch(() => undefined);
      if (cancelled) return;
      c = createCall(voiceOnly);
      events.forEach((e) => c!.on(e, refresh));
      setCall(c);
    });

    return () => {
      cancelled = true;
      setCall(null);
      setPeers([]);
      callQueue = callQueue.then(async () => {
        if (!c) return;
        events.forEach((e) => c!.off(e, refresh));
        await c.leave().catch(() => undefined);
        await c.destroy().catch(() => undefined);
      });
    };
  }, [active, generation, voiceOnly]);

  const refresh = useCallback(() => {
    if (call) setPeers(Object.values(call.participants()).map(toPeer));
  }, [call]);

  return { call, peers, refresh };
}

export function MeetPage() {
  const [params] = useSearchParams();
  const preview = previewStep(params.get("preview"));
  if (preview) return <Preview step={preview} />;
  return <Meet initialId={params.get("id")} />;
}

/** Development only: one step with sample details (preview.ts). */
function Preview({ step }: { step: NonNullable<ReturnType<typeof previewStep>> }) {
  const facts = previewFacts(step);
  const creds = { meetingId: "428-193-756", password: "preview" };
  const [generation] = useState(0);
  const { call, peers, refresh } = useCall(step === "lobby", generation, facts.round === "hr");
  const [screen, setScreen] = useState<MediaStream | null>(null);
  const noop = () => undefined;
  if (step === "consent") return <ConsentStep facts={facts} onAgree={noop} onBack={noop} />;
  if (step === "lobby")
    return (
      <Lobby call={call} peers={peers} refresh={refresh} facts={facts} credentials={creds} screen={screen} onScreen={setScreen} onJoined={noop} />
    );
  if (step === "done") return <DoneStep facts={facts} ending="complete" joinedAt={Date.now() - 38 * 60000} onRejoin={noop} />;
  return (
    <Room
      call={previewCall()}
      peers={previewPeers(step)}
      refresh={noop}
      facts={facts}
      screen={null}
      onScreen={noop}
      joinedAt={Date.now() - 18 * 60000 - 24000}
      onFinished={noop}
    />
  );
}

function Meet({ initialId }: { initialId: string | null }) {
  const remembered = useRef(recall()).current;
  const [step, setStep] = useState<Step>("enter");
  const [credentials, setCredentials] = useState<Credentials>({
    meetingId: remembered?.meetingId ?? initialId ?? "",
    password: remembered?.password ?? "",
  });
  const [facts, setFacts] = useState<RoomFacts | null>(null);
  const [screen, setScreen] = useState<MediaStream | null>(null);
  const [ending, setEnding] = useState<Ending | null>(null);
  const [joinedAt, setJoinedAt] = useState<number | null>(null);
  const [generation, setGeneration] = useState(0);
  const [resuming, setResuming] = useState(Boolean(remembered));

  const { call, peers, refresh } = useCall(step === "lobby" || step === "room", generation, facts?.round === "hr");

  useEffect(() => {
    document.title = "Your interview · MITRA";
  }, []);

  // A reload mid-interview: check the remembered credentials and go straight
  // back to the lobby if consent was already given.
  useEffect(() => {
    if (!remembered) return;
    lookup(remembered)
      .then((f) => {
        setFacts(f);
        if (f.status === "open") setStep(remembered.agreed ? "lobby" : "consent");
      })
      .catch(() => remember(null))
      .finally(() => setResuming(false));
  }, [remembered]);

  // The shared screen belongs to the page, not to a step: stop it when we leave.
  useEffect(() => () => screen?.getTracks().forEach((t) => t.stop()), [screen]);

  const found = useCallback((c: Credentials, f: RoomFacts) => {
    setCredentials(c);
    setFacts(f);
    remember({ ...c, agreed: false });
    setStep("consent");
  }, []);

  const [gazeOff, setGazeOff] = useState(false);
  const agreed = useCallback((gazeRemindersOff: boolean) => {
    setGazeOff(gazeRemindersOff);
    remember({ ...credentials, agreed: true });
    setStep("lobby");
  }, [credentials]);

  // The proctoring token from the join: in memory only, gone with the page.
  const [proctoring, setProctoring] = useState<ProctoringPass | null>(null);
  const entered = useCallback((room: JoinResult) => {
    setProctoring(room.proctoring_token ? { interviewId: room.interview_id, token: room.proctoring_token } : null);
    setJoinedAt(Date.now());
    setStep("room");
  }, []);

  const finished = useCallback((how: Ending) => {
    setEnding(how);
    setStep("done");
    setScreen((s) => {
      s?.getTracks().forEach((t) => t.stop());
      return null;
    });
    // Rejoining after a dropped connection needs the credentials; a finished
    // interview does not.
    if (how !== "dropped") remember(null);
  }, []);

  const rejoin = useCallback(() => {
    setGeneration((g) => g + 1);
    setEnding(null);
    setStep("lobby");
  }, []);

  if (resuming) {
    return (
      <div className="meet meet-light meet-center" role="status" aria-label="Loading your interview">
        <span className="meet-spinner" />
      </div>
    );
  }

  if (step === "enter" || !facts) {
    return <EnterStep initial={credentials} onFound={found} />;
  }
  if (step === "consent") {
    return <ConsentStep facts={facts} onAgree={agreed} onBack={() => setStep("enter")} />;
  }
  if (step === "lobby") {
    return (
      <Lobby
        call={call}
        peers={peers}
        refresh={refresh}
        facts={facts}
        credentials={credentials}
        screen={screen}
        onScreen={setScreen}
        onJoined={entered}
      />
    );
  }
  if (step === "room" && call) {
    return (
      <Room
        call={call}
        peers={peers}
        refresh={refresh}
        facts={facts}
        screen={screen}
        onScreen={setScreen}
        joinedAt={joinedAt ?? Date.now()}
        onFinished={finished}
        gazeRemindersOff={gazeOff}
        proctoring={proctoring}
      />
    );
  }
  return <DoneStep facts={facts} ending={ending ?? "complete"} joinedAt={joinedAt} onRejoin={rejoin} />;
}
