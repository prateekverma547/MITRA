"""Task 19: the raw transcript in spoken order, with cut replies cut to what was heard.

Most of these replay a real recorded realtime session (tests/realtime_replay.py,
tests/fixtures/realtime_capture/: synthetic voice, events only) through the
real Pipecat service, our service, our observers and our persistence. Ground
truth for the candidate's speech comes from the investigation (F-066/F-067).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import realtime_replay as rr  # noqa: E402

from shared.contracts import EvidenceQuote, Speaker, Transcript, TranscriptTurn  # noqa: E402

CUT_FULL = "What did you believe about the market that others on your team did not?"
CUT_HEARD_READBACK = "What did you believe about the market that"  # F-068, the server's read-back


def run(delay=0.0, **kw):
    result = asyncio.run(rr.replay(delay, **kw))
    transcript, waits = rr.build(result)
    return result, transcript, waits


@pytest.fixture(scope="module")
def replayed():
    return run(0.0)


# -- spoken order and spoken times ---------------------------------------------------------


def test_candidate_turns_carry_their_spoken_times(replayed):
    _, transcript, _ = replayed
    truth = rr.load()[3]["spoken_truth_s"]
    candidate = [t for t in transcript.turns if t.speaker is Speaker.CANDIDATE]
    assert len(candidate) == 3
    for turn, label in zip(candidate, ("A", "B", "C")):
        start, end = truth[label]
        assert turn.timing_source == "silero"
        assert abs(turn.audio_start_seconds - start) < 0.2, (label, turn.audio_start_seconds, start)
        assert abs(turn.audio_end_seconds - end) < 0.2, (label, turn.audio_end_seconds, end)
        # at_seconds keeps its meaning: when the text arrived, later than the speech.
        assert turn.at_seconds > turn.audio_end_seconds


def test_turns_are_stored_in_spoken_order(replayed):
    _, transcript, _ = replayed
    starts = [t.spoken_start for t in transcript.turns]
    assert starts == sorted(starts)
    assert [t.speaker.value for t in transcript.turns] == [
        "interviewer", "candidate", "interviewer", "candidate", "interviewer", "candidate", "interviewer"]
    assert [t.index for t in transcript.turns] == list(range(7))


def test_a_three_second_slower_transcriber_keeps_the_order():
    """F-067: before the fix, this delay stored Mitra's reply to B before B."""
    _, slow, _ = run(3.0)
    assert [t.speaker.value for t in slow.turns] == [
        "interviewer", "candidate", "interviewer", "candidate", "interviewer", "candidate", "interviewer"]
    assert [t.text for t in slow.turns] == [t.text for t in run(0.0)[1].turns]


def test_mitra_turns_are_timed_by_playback(replayed):
    _, transcript, _ = replayed
    for turn in transcript.turns:
        if turn.speaker is Speaker.INTERVIEWER:
            assert turn.timing_source == "playback" and turn.item_id
            assert turn.audio_start_seconds < turn.audio_end_seconds


# -- the cut reply -----------------------------------------------------------------------------


def test_a_cut_reply_is_stored_as_heard_with_the_full_text_kept(replayed):
    _, transcript, _ = replayed
    cut = [t for t in transcript.turns if t.cut]
    assert len(cut) == 1
    turn = cut[0]
    assert turn.text == CUT_HEARD_READBACK
    assert turn.generated_text == CUT_FULL
    # It ends where the candidate cut in, well before the full 3.6 s reply would have.
    assert turn.audio_end_seconds - turn.audio_start_seconds < 3.0


def test_the_cut_time_and_source_are_recorded(replayed):
    result, _, _ = replayed
    cuts = result["llm"].timeline.summary(rr.T0)["cuts"]
    assert len(cuts) == 1
    assert cuts[0]["truncated_ms"] == 1886 and cuts[0]["heard_source"] == "readback"
    truncates = [m for m in result["outgoing"] if m["type"] == "conversation.item.truncate"]
    retrieves = [m for m in result["outgoing"] if m["type"] == "conversation.item.retrieve"]
    assert [m["audio_end_ms"] for m in truncates] == [1886]
    assert [m["item_id"] for m in retrieves] == [truncates[0]["item_id"]]


# -- the read-back never blocks the reply path ---------------------------------------------


def test_the_read_back_never_blocks_a_frame():
    """Decision 18. The truncate is sent and the handler returns at once, even if
    the read-back never answers; frames keep flowing; the cut falls back to the
    proportional text. Before it was scheduled as a task, an awaited read-back
    would have held the receive loop (and every frame) for the whole timeout."""
    from pipecat.services.openai.realtime import events

    from bot.services.realtime import build_realtime_llm

    async def scenario():
        llm = build_realtime_llm(api_key="offline", instructions="x", tools=None)
        sent = []

        async def ws_send(message):
            sent.append(message)

        hang = asyncio.get_running_loop().create_future()

        async def never_answers(item_id):
            return await hang

        llm._ws_send = ws_send
        llm.retrieve_conversation_item = never_answers
        start = time.monotonic()
        await asyncio.wait_for(
            llm.send_client_event(events.ConversationItemTruncateEvent(
                item_id="item_x", content_index=0, audio_end_ms=900)),
            timeout=0.2,
        )
        elapsed = time.monotonic() - start
        pending = len(llm._readbacks)
        for task in list(llm._readbacks):
            task.cancel()
        return elapsed, sent, pending

    elapsed, sent, pending = asyncio.run(scenario())
    assert elapsed < 0.05
    assert [m["type"] for m in sent] == ["conversation.item.truncate"]
    assert pending == 1  # the read-back runs on its own


def test_an_unanswered_read_back_does_not_hold_up_the_session():
    started = time.monotonic()
    result, transcript, _ = run(0.0, hold_readback=True)
    assert time.monotonic() - started < 2.0  # well under READBACK_TIMEOUT_SECONDS
    # Everything after the cut was still processed, in order.
    assert transcript.turns[-1].text.startswith("Go ahead")
    cut = next(t for t in transcript.turns if t.cut)
    assert cut.text == "What did you believe about the market"  # proportional, F-068
    assert cut.generated_text == CUT_FULL
    for task in list(result["llm"]._readbacks):
        task.cancel()


def test_a_failed_read_back_leaves_no_pending_retrieval():
    from bot.services.realtime import build_realtime_llm

    async def scenario():
        llm = build_realtime_llm(api_key="offline", instructions="x", tools=None)

        async def fails(item_id):
            llm._retrieve_conversation_item_futures[item_id] = [object()]
            raise RuntimeError("socket closed")

        llm.retrieve_conversation_item = fails
        await llm._read_back("item_y")
        return llm

    llm = asyncio.run(scenario())
    assert "item_y" not in llm._retrieve_conversation_item_futures
    assert llm.timeline.assistants.get("item_y") is None or llm.timeline.assistants["item_y"].heard_text is None


# -- reply waits keyed by item id (F-060) ---------------------------------------------------


def test_each_wait_is_linked_to_its_turns(replayed):
    _, transcript, waits = replayed
    assert [w["turn_index"] for w in waits] == [1, 3, 5]
    assert [w["reply_turn_index"] for w in waits] == [2, 4, 6]
    by_index = {t.index: t for t in transcript.turns}
    for w in waits:
        assert by_index[w["turn_index"]].speaker is Speaker.CANDIDATE
        assert by_index[w["reply_turn_index"]].speaker is Speaker.INTERVIEWER


# -- pre-interview turns: kept, flagged, left out of every reading ------------------------


def _observer_turns():
    return [
        {"speaker": "pre_interview_audio", "text": "Enjoyment", "at_seconds": 1.0},
        {"speaker": "interviewer", "text": "Hello, I'm Mitra.", "at_seconds": 3.0},
        {"speaker": "candidate", "text": "Hi, I lead payments.", "at_seconds": 6.0},
    ]


def test_pre_interview_turns_are_kept_and_flagged():
    from bot.persistence import build_transcript

    transcript = build_transcript(interview_id="i", turns=_observer_turns(), duration_seconds=10)
    assert [(t.text, t.pre_interview, t.speaker.value) for t in transcript.turns] == [
        ("Enjoyment", True, "candidate"),
        ("Hello, I'm Mitra.", False, "interviewer"),
        ("Hi, I lead payments.", False, "candidate"),
    ]
    assert [t.text for t in transcript.interview_turns()] == ["Hello, I'm Mitra.", "Hi, I lead payments."]


def test_no_reader_of_the_record_sees_pre_interview_audio():
    from app.interviews import _without_pre_interview
    from bot.persistence import build_transcript
    from feedback.health import assess, assess_questioning
    from feedback.score import _render_transcript, verify_quote

    transcript = build_transcript(interview_id="i", turns=_observer_turns(), duration_seconds=10)
    assert "Enjoyment" not in _render_transcript(transcript)
    assert verify_quote("Enjoyment", 0, transcript) is None
    assert assess(transcript, {}).candidate_turns == 1
    assess_questioning(transcript)  # reads only interview turns; must not fail
    view = _without_pre_interview(transcript.model_dump(mode="json"))
    assert [t["text"] for t in view["turns"]] == ["Hello, I'm Mitra.", "Hi, I lead payments."]


def test_the_pipeline_engine_keeps_arrival_order_and_times():
    from bot.persistence import build_transcript

    transcript = build_transcript(interview_id="i", turns=_observer_turns(), duration_seconds=10)
    assert {t.timing_source for t in transcript.turns} == {"arrival"}
    assert all(t.audio_start_seconds is None and t.item_id is None for t in transcript.turns)
    assert [t.at_seconds for t in transcript.turns] == [1.0, 3.0, 6.0]


# -- the audio clock ------------------------------------------------------------------------


def test_the_audio_clock_survives_a_pause_and_a_reconnect():
    from bot.realtime_timeline import RealtimeTimeline

    tl = RealtimeTimeline()
    tl.new_connection(1)
    for i in range(50):  # 1 s of audio from wall 100.0
        tl.on_append(960, 100.0 + i * 0.02)
    for i in range(50):  # paused 5 s, then 1 s more
        tl.on_append(960, 106.0 + i * 0.02)
    assert tl.audio_ms_to_wall(1, 500) == pytest.approx(100.5)
    assert tl.audio_ms_to_wall(1, 1500) == pytest.approx(106.5)
    tl.new_connection(2)  # OpenAI's clock restarts at 0
    tl.on_append(960, 200.0)
    assert tl.audio_ms_to_wall(2, 10) == pytest.approx(200.01)
    assert tl.audio_ms_to_wall(1, 10) == pytest.approx(100.01)


def _window_turn(start_ms, end_ms):
    from bot.persistence import build_transcript
    from bot.realtime_timeline import RealtimeTimeline

    tl = RealtimeTimeline()
    tl.new_connection(1)
    tl.on_append(48 * 5000, 1000.0)  # 5 s of audio; the clock origin is wall 1000
    tl.speech_started("u1", start_ms)
    tl.speech_stopped("u1", end_ms)
    turns = [{"speaker": "candidate", "text": "hi", "at_seconds": 6.0, "item_id": "u1"}]
    return build_transcript(interview_id="i", turns=turns, duration_seconds=6, timeline=tl,
                            clock_zero=1000.0).turns[0]


def test_without_silero_edges_the_padded_window_is_corrected():
    from bot.realtime_timeline import OPENAI_END_PAD_SECONDS, OPENAI_START_PAD_SECONDS

    t = _window_turn(100, 3000)
    assert t.timing_source == "openai_window"
    assert t.audio_start_seconds == pytest.approx(0.1 + OPENAI_START_PAD_SECONDS, abs=0.01)
    assert t.audio_end_seconds == pytest.approx(3.0 - OPENAI_END_PAD_SECONDS, abs=0.01)


def test_a_window_shorter_than_its_padding_is_kept_as_measured():
    t = _window_turn(100, 900)
    assert (t.audio_start_seconds, t.audio_end_seconds) == (0.1, 0.9)


# -- the contract ---------------------------------------------------------------------------


def test_an_old_transcript_without_the_new_fields_still_loads():
    old = {"interview_id": "x", "turns": [
        {"index": 0, "speaker": "candidate", "text": "hi", "at_seconds": 1.5, "section_id": None}]}
    turn = Transcript.model_validate(old).turns[0]
    assert (turn.timing_source, turn.cut, turn.pre_interview, turn.spoken_start) == ("arrival", False, False, 1.5)


def test_the_new_fields_round_trip(replayed):
    _, transcript, _ = replayed
    dumped = transcript.model_dump(mode="json")
    assert Transcript.model_validate(dumped).model_dump(mode="json") == dumped


@pytest.mark.parametrize("bad, message", [
    ({"audio_start_seconds": 5, "audio_end_seconds": 4}, "ends before it starts"),
    ({"generated_text": "full"}, "only for a cut reply"),
    ({"cut": True, "speaker": "candidate"}, "only the interviewer"),
    ({"timing_source": "guess"}, "timing_source"),
])
def test_invalid_timing_is_rejected(bad, message):
    turn = {"index": 0, "speaker": "interviewer", "text": "x", "at_seconds": 1, **bad}
    with pytest.raises(ValidationError, match=message):
        TranscriptTurn.model_validate(turn)


def test_a_verified_quote_carries_its_spoken_window(replayed):
    from feedback.score import verify_quote

    _, transcript, _ = replayed
    a = next(t for t in transcript.turns if t.speaker is Speaker.CANDIDATE)
    quote = verify_quote("I lead the payments product", a.index, transcript)
    assert (quote.audio_start_seconds, quote.audio_end_seconds) == (a.audio_start_seconds, a.audio_end_seconds)
    assert quote.at_seconds == a.at_seconds  # arrival kept as before
    EvidenceQuote.model_validate(json.loads(quote.model_dump_json()))
