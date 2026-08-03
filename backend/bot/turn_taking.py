"""Listening configuration: when do we decide the candidate has finished speaking?

This is the single most important file for interview quality. A bot that cuts a
nervous candidate off mid-thought destroys both the experience and the evidence
we are collecting. Per CLAUDE.md, tuning priority is:

    (1) doesn't interrupt      <- never trade this away
    (2) feels fast

There are three independent mechanisms stacked here. Understanding which one
ended a turn is the whole game when diagnosing a bad interruption:

1. **Silero VAD** (`VAD_STOP_SECS`) — pure acoustic silence detection. Fires
   fast (200ms of silence) and does NOT end the turn. It only tells the smart
   turn analyzer "there is a gap here, go take a look".

2. **Smart-turn v3 model** (`LocalSmartTurnAnalyzerV3`) — a bundled ONNX model
   that looks at the audio (prosody, intonation, trailing filler) and predicts
   whether the sentence sounded *finished*. This is what distinguishes
   "...and that's how we fixed it." (falling tone, done) from
   "...so the way we handled that was—" (hanging tone, still thinking).
   It runs on every VAD silence gap. INCOMPLETE keeps the turn open.

3. **`stop_secs` hard cap** — if the model keeps saying INCOMPLETE, this is the
   maximum silence we will tolerate before ending the turn anyway. It is a
   safety net against a candidate who goes quiet and never comes back.

Pipecat 1.6.0 ships smart-turn v3 as the *default* stop strategy, so we are
configuring the defaults rather than adding a mechanism.
"""

from typing import Any

import numpy as np
from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import (
    UserTurnStrategies,
    default_user_turn_start_strategies,
)

# How long a silence the smart-turn model is allowed to keep calling INCOMPLETE
# before we end the turn regardless.
#
# Pipecat's default is exactly 3.0s, which collides head-on with our Milestone 1
# DoD ("must not interrupt a 3-second mid-answer thinking pause") — at the
# default, a genuine 3s pause lands precisely on the cutoff. 4.0s puts clear
# daylight between the DoD case and the hard cap.
#
# This does NOT cost latency on normal turns. When the candidate has plainly
# finished, the model returns COMPLETE at the first VAD gap (~200ms) and we
# respond immediately; stop_secs only comes into play when the model is still
# unsure, which is exactly the thinking-pause case we want to protect.
#
# Ceiling: must stay below LLMUserAggregatorParams.user_turn_stop_timeout
# (default 5.0s) or that timeout fires first and silently pre-empts this value.
SMART_TURN_STOP_SECS = 4.0

# Acoustic silence before VAD reports a gap. Deliberately short — this is a
# trigger for the smart-turn model, not a turn-ending decision. Lengthening it
# to "fix" interruptions is the classic wrong knob: it delays every single
# response while barely helping the mid-thought pause case.
VAD_STOP_SECS = 0.2

# Speech must persist this long before we believe it started. Guards against
# coughs, chair creaks and keyboard noise opening a turn.
VAD_START_SECS = 0.2

# Audio window the smart-turn model considers, in seconds. Pipecat's default.
SMART_TURN_MAX_DURATION_SECS = 8.0

# How sure the model must be that the candidate has finished before we end the
# turn on its say-so.
#
# Pipecat hardcodes `probability > 0.5` inside LocalSmartTurnAnalyzerV3, which
# is a coin flip: a clause that sounds 51% finished ends the turn. Live session
# `int_0a7ca5d0aca5` started a new turn 74 times in 291 seconds against a
# candidate speaking in short, complete-sounding clauses. Each fragment went to
# the LLM on its own, so the interviewer answered "urgent request." with "I
# understand this is urgent", having never seen the sentence it came from.
#
# Raising this does NOT risk a hung turn. `stop_secs` is still the backstop: if
# the model stays unsure, the turn ends there anyway. So the worst case is
# waiting the same 4s we already accept for a thinking pause, which is rule (1)
# of the tuning order. It also costs nothing on a clear ending, where the model
# returns a high probability and we release immediately.
#
# 0.8 is a starting point, not a measurement. `turn_end_probabilities` in the
# session summary records what the model actually said, so set this from a real
# session rather than from taste.
TURN_END_CONFIDENCE = 0.8


class ConfidentSmartTurnAnalyzer(LocalSmartTurnAnalyzerV3):
    """Smart-turn v3, but it has to be sure before it cuts somebody off.

    Overrides only the threshold applied to the model's own probability. The
    model, the features and the inference are untouched, so this stays a
    configuration of Pipecat's analyser rather than a second implementation of
    turn detection, which CLAUDE.md forbids.
    """

    def __init__(self, *, confidence: float = TURN_END_CONFIDENCE, **kwargs):
        super().__init__(**kwargs)
        self._confidence = confidence

    def _predict_endpoint(self, audio_array: np.ndarray) -> dict[str, Any]:
        result = super()._predict_endpoint(audio_array)
        probability = result["probability"]
        prediction = 1 if probability >= self._confidence else 0
        if result["prediction"] == 1 and prediction == 0:
            logger.debug(
                f"smart-turn said complete at {probability:.2f}, below the "
                f"{self._confidence:.2f} bar; letting the candidate carry on"
            )
        result["prediction"] = prediction
        return result


def build_vad_analyzer() -> SileroVADAnalyzer:
    """Acoustic voice-activity detection (layer 1)."""
    return SileroVADAnalyzer(
        params=VADParams(
            start_secs=VAD_START_SECS,
            stop_secs=VAD_STOP_SECS,
        )
    )


def build_turn_strategies() -> UserTurnStrategies:
    """Semantic endpointing (layers 2 and 3) on top of the default start strategies.

    Start strategies are left at Pipecat's defaults (VAD + transcription).
    Only the stop side needs our tuning.
    """
    return UserTurnStrategies(
        start=default_user_turn_start_strategies(),
        stop=[
            TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=ConfidentSmartTurnAnalyzer(
                    confidence=TURN_END_CONFIDENCE,
                    params=SmartTurnParams(
                        stop_secs=SMART_TURN_STOP_SECS,
                        max_duration_secs=SMART_TURN_MAX_DURATION_SECS,
                    )
                )
            )
        ],
    )
