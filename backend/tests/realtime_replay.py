"""Replay a recorded realtime session through the real service and observers.

Offline and free. Uses tests/fixtures/realtime_capture/ (task 19: one real
gpt-realtime-2.1 session, synthetic voice, events only, no audio):

- every recorded server event is fed, in time order, into the real
  `MitraRealtimeService._receive_task_handler` (Pipecat's parser and handlers,
  plus ours);
- the audio we appended is replayed as `_send_user_audio` calls of the same
  length at the same times, and the greeting pause through
  `set_audio_input_paused`, so the audio clock code runs as it does live;
- frames the service emits go to the real `TranscriptObserver` and
  `TurnLatencyObserver`; BotStarted/StoppedSpeaking are synthesised at each
  reply's recorded playback; VAD frames are Silero's recorded edges;
- `time.time()` inside Pipecat's service, our service and our observers
  returns the replayed clock.

`transcript_delay` delays every input transcription (a slower transcriber),
the perturbation that flipped the order before task 19 (F-067).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time as real_time
import types
from contextlib import contextmanager
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "realtime_capture"
BYTES_PER_MS = 48
T0 = 1_795_028_444.988936  # the capture's wall-clock start; all fixture times are from here


def load() -> tuple[list[dict], list, list, dict]:
    events = [json.loads(line) for line in open(FIXTURE / "events.jsonl")]
    appends = json.loads((FIXTURE / "appends.json").read_text())
    vad = json.loads((FIXTURE / "vad_edges.json").read_text())
    meta = json.loads((FIXTURE / "meta.json").read_text())
    return events, appends, vad, meta


class _Clock:
    wall = T0


@contextmanager
def replayed_clock(clock: _Clock):
    """Point every time.time() the replay touches at the replayed clock."""
    import bot.observers as observers
    import bot.services.realtime as ours
    import pipecat.services.openai.realtime.llm as pc_llm

    fake = types.SimpleNamespace(**{k: getattr(real_time, k) for k in dir(real_time) if not k.startswith("__")})
    fake.time = lambda: clock.wall
    saved = (pc_llm.time, observers.time, ours.time)
    pc_llm.time = observers.time = ours.time = fake
    try:
        yield
    finally:
        pc_llm.time, observers.time, ours.time = saved


async def replay(transcript_delay: float = 0.0, *, hold_readback: bool = False) -> dict:
    """Run the capture; return the observers, the service and what was sent.

    `hold_readback=True` drops the recorded read-back events, so the read-back
    never answers (for the non-blocking test and the proportional fallback).
    """
    from bot.observers import TranscriptObserver, TurnLatencyObserver
    from bot.services.realtime import build_realtime_llm

    events, appends, vad, meta = load()
    clock = _Clock()
    with replayed_clock(clock):
        llm = build_realtime_llm(api_key="offline", instructions="x", tools=None)
        transcript = TranscriptObserver(timeline=llm.timeline)
        transcript._started_at = T0  # interview second 0 = the capture's start
        latency = TurnLatencyObserver(timeline=llm.timeline)
        llm.timeline.new_connection(1)  # what _connect does on a real connection
        outgoing: list[dict] = []
        frames: list[tuple[float, str]] = []

        async def deliver(frame):
            frames.append((round(clock.wall - T0, 3), type(frame).__name__))
            pushed = FramePushed(source=None, destination=None, frame=frame,
                                 direction=FrameDirection.DOWNSTREAM, timestamp=0)
            await transcript.on_push_frame(pushed)
            await latency.on_push_frame(pushed)

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            await deliver(frame)

        async def broadcast_frame(frame_cls, **kwargs):
            await deliver(frame_cls(**kwargs))

        async def nothing(*args, **kwargs):
            return None

        async def ws_send(message):
            if message.get("type") != "input_audio_buffer.append":
                outgoing.append({"t": round(clock.wall - T0, 3), **message})

        llm.push_frame, llm.broadcast_frame = push_frame, broadcast_frame
        # No pipeline here, so no TaskManager: give retrieve_conversation_item the loop.
        llm.get_event_loop = asyncio.get_running_loop
        llm.broadcast_interruption, llm._ws_send = nothing, ws_send
        for name in ("start_ttfb_metrics", "stop_ttfb_metrics", "start_processing_metrics",
                     "stop_processing_metrics", "stop_all_metrics", "start_llm_usage_metrics",
                     "_call_event_handler"):
            setattr(llm, name, nothing)

        # Things that happen on our side, in time order, between server events.
        local: list[tuple[float, str, object]] = [(0.5, "pause", True), (meta["input_unpaused_at"], "pause", False)]
        local += [(t, "append", ms) for t, ms in appends]
        for t, kind, secs in vad:
            cls = VADUserStartedSpeakingFrame if kind == "start" else VADUserStoppedSpeakingFrame
            arg = {"start_secs": secs} if kind == "start" else {"stop_secs": secs}
            local.append((t, "frame", cls(timestamp=T0 + t, **arg)))
        for item in meta["assistant_items"]:
            first = next(e for e in events if e["type"] == "response.output_audio.delta" and e["item_id"] == item)
            played = meta["played_ms"].get(item)
            full = meta["audio_bytes"][item] / BYTES_PER_MS
            # Playback starts once the first audio has arrived, never before it.
            local.append((first["t"] + 0.001, "frame", BotStartedSpeakingFrame()))
            # An interrupted reply stops just after the interruption reaches the service.
            local.append((first["t"] + (played / 1000 + 0.05 if played else full / 1000), "stop", None))
        local.sort(key=lambda x: x[0])

        timeline = []
        for e in events:
            if hold_readback and e["type"] == "conversation.item.retrieved":
                continue
            at = e["t"] + (transcript_delay if e["type"] == "conversation.item.input_audio_transcription.completed" else 0)
            timeline.append((at, e))
        timeline.sort(key=lambda x: x[0])

        async def run_local(t, kind, value):
            clock.wall = T0 + t
            if kind == "pause":
                llm.set_audio_input_paused(value)
            elif kind == "append":
                await llm._send_user_audio(InputAudioRawFrame(
                    audio=bytes(int(round(value * BYTES_PER_MS))), sample_rate=24000, num_channels=1))
            elif kind == "stop":
                await llm._handle_bot_stopped_speaking()
                await deliver(BotStoppedSpeakingFrame())
            else:
                await deliver(value)

        class FakeWS:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                pending = list(local)
                for at, e in timeline:
                    while pending and pending[0][0] <= at:
                        await run_local(*pending.pop(0))
                    clock.wall = T0 + at
                    raw = {k: v for k, v in e.items() if k not in ("t", "delta_bytes")}
                    if e["type"] == "response.output_audio.delta":
                        raw["delta"] = base64.b64encode(bytes(e["delta_bytes"])).decode()
                    yield json.dumps(raw)
                    await asyncio.sleep(0)  # let scheduled tasks (the read-back) run
                for item in pending:
                    await run_local(*item)

        llm._websocket = FakeWS()
        await llm._receive_task_handler()
        await asyncio.sleep(0)
        return {"llm": llm, "transcript": transcript, "latency": latency,
                "outgoing": outgoing, "frames": frames, "meta": meta}


def build(result: dict):
    """The stored transcript and linked waits, exactly as run_realtime saves them."""
    from bot.persistence import build_transcript, link_waits_to_turns

    tr = result["transcript"]
    transcript = build_transcript(
        interview_id="replay", turns=tr.turns, duration_seconds=50.0,
        timeline=result["llm"].timeline, clock_zero=tr.clock_zero,
        silero_starts=tr.silero_starts, silero_ends=tr.silero_ends,
    )
    waits = link_waits_to_turns([t.to_dict() for t in result["latency"].turns], transcript)
    return transcript, waits
