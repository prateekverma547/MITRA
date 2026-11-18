"""Captions on the candidate page, for the realtime engine.

The pipeline engine gets captions from Pipecat's `RTVIObserver` (`run_bot.py`).
That cannot be reused here, and the reason is specific: the OpenAI Realtime
service pushes every transcript *delta* as its own `TTSTextFrame` labelled
`AggregationType.SENTENCE` ("Hel", "lo there"), and RTVI sends each frame as a
separate caption segment with its own id. The candidate page gives every
segment id its own line, so the question would arrive on screen one fragment per
line.

So this observer builds the captions itself, in the exact message shape the
page already reads (`label: "rtvi-ai"`, `bot-started-speaking` then
`bot-output` with `text` and `segment_id`), which means no frontend change:

- **One segment per reply.** Every message for a reply carries the same
  `segment_id`, and the page updates that one line in place.
- **Whole sentences.** The text is sent when a sentence completes and again at
  the end of the reply, never per delta: captions that stutter a word at a
  time are what `run_bot.py` deliberately prevents, and a message per delta
  would also run into Daily's app-message rate limits.
- **Only the interviewer.** As on the pipeline engine, nothing the candidate
  says is ever sent back to them.

Known difference from the pipeline engine: the realtime transcript arrives as
the audio is generated, which is faster than it is played, so a sentence can
appear slightly before it is heard. The page keeps the question on screen until
the next one, so it is readable either way.

An observer, not a processor, and not a dataclass, for the reasons in
`bot/ending.py`.
"""

import re

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputTransportMessageUrgentFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import FramePushed

from bot.observers import _DedupObserver

#: The label the candidate page filters on (`message.label !== "rtvi-ai"`).
LABEL = "rtvi-ai"

#: The end of a sentence: terminal punctuation, optionally closed by a quote or
#: bracket, then whitespace or the end of the text so far.
_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*(?=\s|$)")


def started_message() -> dict:
    return {"label": LABEL, "type": "bot-started-speaking"}


def output_message(text: str, segment_id: str) -> dict:
    return {
        "label": LABEL,
        "type": "bot-output",
        "data": {"text": text, "segment_id": segment_id, "spoken": True},
    }


class RealtimeCaptions(_DedupObserver):
    """Sends the interviewer's words to the candidate page, a sentence at a time."""

    def __init__(self, *, session_id: str = ""):
        super().__init__()
        self._session_id = session_id
        self._worker = None
        self._reply = 0
        self._text = ""
        self._sent = ""
        self._open = False

    def attach(self, worker) -> None:
        self._worker = worker

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if not isinstance(
            frame, (LLMFullResponseStartFrame, TTSTextFrame, LLMFullResponseEndFrame)
        ):
            return
        if not self._is_new(frame):
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            await self._start_reply()
        elif isinstance(frame, TTSTextFrame):
            if not self._open:
                # Text with no start seen (should not happen): still caption it,
                # as a reply of its own, rather than gluing it onto the last one.
                await self._start_reply()
            spaced = getattr(frame, "includes_inter_frame_spaces", False)
            self._text += frame.text if spaced else f" {frame.text}"
            await self._send_complete_sentences()
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self._send(self._text)
            self._open = False

    async def _start_reply(self) -> None:
        self._reply += 1
        self._text = ""
        self._sent = ""
        self._open = True
        await self._push(started_message())

    async def _send_complete_sentences(self) -> None:
        ends = list(_SENTENCE_END.finditer(self._text))
        if ends:
            await self._send(self._text[: ends[-1].end()])

    async def _send(self, text: str) -> None:
        text = " ".join(text.split())
        if not text or text == self._sent:
            return
        self._sent = text
        await self._push(output_message(text, f"{self._session_id}-reply-{self._reply}"))

    async def _push(self, message: dict) -> None:
        if self._worker is None:
            return
        await self._worker.queue_frames([OutputTransportMessageUrgentFrame(message=message)])
