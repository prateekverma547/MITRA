"""The cleaned transcript (task 25): guards, alignment, never touching the raw. No keys."""

import copy
from types import SimpleNamespace

import pytest

from feedback.clean import align, check_turn, clean_transcript

RAW = "um so we we cut the payout failures by 38 percent on the uh routing layer at Meridian Pay"


def turn(text, corrections=(), uncertain=()):
    return {"index": 1, "text": text, "corrections": [dict(c) for c in corrections], "uncertain": list(uncertain)}


def test_a_clean_tidy_passes():
    assert check_turn(RAW, turn("So we cut the payout failures by 38 percent on the routing layer at Meridian Pay.")) == []


def test_an_invented_number_is_caught():
    problems = check_turn(RAW, turn("So we cut the payout failures by 38 percent in 2 quarters on the routing layer at Meridian Pay."))
    assert any(p.startswith("number added") for p in problems)


def test_a_dropped_number_is_caught():
    problems = check_turn(RAW, turn("So we cut the payout failures on the routing layer at Meridian Pay."))
    assert any(p.startswith("number dropped") for p in problems)


def test_a_new_word_is_caught():
    problems = check_turn(RAW, turn("So we heroically cut the payout failures by 38 percent on the routing layer at Meridian Pay."))
    assert any(p.startswith("new words") for p in problems)


def test_a_lost_name_is_caught_unless_declared_or_uncertain():
    raw = "we moved the cluster to Kubernetes with gRPC between the services"
    assert any(p.startswith("term dropped") for p in check_turn(raw, turn("We moved the cluster with gRPC between the services.")))
    fixed = turn("We moved the cluster to Kubernetes with gRPC between the services.")
    assert check_turn(raw, fixed) == []


def test_a_declared_correction_is_allowed_and_an_undeclared_one_is_not():
    raw = "I led payouts at Maridevan Pay for three years"
    good = turn("I led payouts at Meridian Pay for three years.", [{"from_text": "Maridevan", "to_text": "Meridian"}])
    assert check_turn(raw, good) == []
    assert check_turn(raw, turn("I led payouts at Meridian Pay for three years.")) != []


def test_too_much_removed_is_caught():
    raw = "we shipped the routing layer and then we measured the failure rate every week for a full quarter"
    assert any(p.startswith("too much removed") for p in check_turn(raw, turn("We shipped the routing layer.")))


def test_a_dropped_or_misaligned_turn_is_caught():
    raw_turns = [{"index": 1}, {"index": 3}, {"index": 5}]
    assert align(raw_turns, [{"index": 1}, {"index": 3}, {"index": 5}]) == []
    assert align(raw_turns, [{"index": 1}, {"index": 5}]) != []                  # dropped
    assert align(raw_turns, [{"index": 1}, {"index": 3}, {"index": 6}]) != []    # misaligned
    assert align(raw_turns, [{"index": 1}, {"index": 1}, {"index": 3}, {"index": 5}]) != []  # duplicated


TRANSCRIPT = {"interview_id": "int_1", "duration_seconds": 30.0, "turns": [
    {"index": 0, "speaker": "interviewer", "text": "What was the bet?", "at_seconds": 1.0, "item_id": "m0",
     "audio_start_seconds": 0.5, "audio_end_seconds": 2.0},
    {"index": 1, "speaker": "candidate", "text": RAW, "at_seconds": 9.0, "item_id": "c1",
     "audio_start_seconds": 3.0, "audio_end_seconds": 8.5},
    {"index": 2, "speaker": "interviewer", "text": "Why that bet?", "at_seconds": 11.0, "item_id": "m2",
     "audio_start_seconds": 10.0, "audio_end_seconds": 11.0},
    {"index": 3, "speaker": "candidate", "text": "uh failures cost us two million a year", "at_seconds": 16.0,
     "item_id": "c3", "audio_start_seconds": 12.0, "audio_end_seconds": 15.5},
]}


def model(turns):
    async def request(**kwargs):
        return SimpleNamespace(payload={"turns": turns}, record=SimpleNamespace(cost_usd=0.002))
    return request


async def test_one_cleaned_turn_per_raw_turn_with_the_same_ids_and_timings():
    raw = copy.deepcopy(TRANSCRIPT)
    result = await clean_transcript(raw, request=model([
        {"index": 1, "text": "So we cut the payout failures by 38 percent on the routing layer at Meridian Pay.",
         "corrections": [], "uncertain": []},
        {"index": 3, "text": "Failures cost us two million a year.", "corrections": [], "uncertain": []}]))
    assert raw == TRANSCRIPT, "the raw transcript is never changed"
    assert result["status"] == "ok" and result["guard_failures"] == 0
    assert [(t["index"], t["item_id"], t["audio_start_seconds"]) for t in result["turns"]] == [
        (0, "m0", 0.5), (1, "c1", 3.0), (2, "m2", 10.0), (3, "c3", 12.0)]
    assert result["turns"][0]["text"] == "What was the bet?" and result["turns"][0]["guard"] == "copied"


async def test_a_turn_that_fails_a_guard_keeps_its_raw_text_and_is_marked():
    result = await clean_transcript(copy.deepcopy(TRANSCRIPT), request=model([
        {"index": 1, "text": "So we cut the payout failures by 45 percent on the routing layer at Meridian Pay.",
         "corrections": [], "uncertain": []},
        {"index": 3, "text": "Failures cost us two million a year.", "corrections": [], "uncertain": []}]))
    assert result["turns"][1]["text"] == RAW and result["turns"][1]["guard"] != "ok"
    assert result["guard_failures"] == 1


async def test_a_dropped_turn_makes_the_whole_cleaned_transcript_missing():
    result = await clean_transcript(copy.deepcopy(TRANSCRIPT), request=model([
        {"index": 1, "text": "So we cut the payout failures by 38 percent.", "corrections": [], "uncertain": []}]))
    assert result["status"] == "missing"


async def test_a_model_failure_leaves_it_missing_never_raised():
    async def broken(**kwargs):
        raise RuntimeError("text model down")

    result = await clean_transcript(copy.deepcopy(TRANSCRIPT), request=broken)
    assert result["status"] == "missing"


async def test_it_is_written_beside_the_raw_transcript(tmp_path, monkeypatch):
    from app import db
    from feedback import clean as clean_module
    from live.dev import ensure_demo_interview

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/clean.db")
    await db.reset_engine()
    await ensure_demo_interview("int_cleanedcheck0")
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, "int_cleanedcheck0")
        interview.transcript = copy.deepcopy(TRANSCRIPT)
        interview.session_metrics = {"ending": {"ending": "model_spoke"}}
        await session.commit()
    original = clean_module.clean_transcript

    async def fake(raw, **kwargs):
        return await original(raw, request=model([
            {"index": 1, "text": "So we cut the payout failures by 38 percent on the routing layer at Meridian Pay.",
             "corrections": [], "uncertain": []},
            {"index": 3, "text": "Failures cost us two million a year.", "corrections": [], "uncertain": []}]))

    monkeypatch.setattr(clean_module, "clean_transcript", fake)
    await clean_module.generate_cleaned_transcript("int_cleanedcheck0")
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, "int_cleanedcheck0")
        assert interview.transcript == TRANSCRIPT, "raw untouched"
        # C-A6 (round 6): the column, as a valid CleanedTranscript; not session_metrics.
        from shared.interview_contracts import CleanedTranscript

        stored = CleanedTranscript.model_validate(interview.cleaned_transcript)
        assert stored.status == "ok" and [t.index for t in stored.turns] == [0, 1, 2, 3]
        assert "cleaned_transcript" not in interview.session_metrics
        assert interview.session_metrics["ending"] == {"ending": "model_spoke"}, "other metrics untouched"
    await db.reset_engine()


def test_an_inserted_word_already_used_elsewhere_in_the_turn_is_caught():
    raw = "most of my time went into routing layer that decides the route"
    assert "words added or reordered" in check_turn(raw, turn("Most of my time went into the routing layer that decides the route."))


def test_a_capitalised_filler_at_a_sentence_start_is_not_a_term():
    raw = "So that was most of the work on that project. Um"
    assert check_turn(raw, turn("So that was most of the work on that project.")) == []


# -- number-plus-letter tokens (B-70, round 9) -------------------------------------------------

FORMS = ["We fine-tuned the 7B and the 70B model.", "Then we tried a 1.5B model.", "We served 8x7B on two nodes.",
         "Context was 128K tokens.", "We cached 4k entries.", "It had 2M users.", "We compared GPT-4o and o3.",
         "We moved to Llama-3.1 in March.", "Latency was 200ms.", "Each node had 16GB.", "It was 3x faster.",
         "Uptime was 99.9%.", "We used the 14B model."]


@pytest.mark.parametrize("text", FORMS)
def test_an_unchanged_turn_always_passes(text):
    assert check_turn(text, turn(text)) == []


@pytest.mark.parametrize("raw, cleaned, dropped, added", [
    ("We used the 14B model.", "We used the 13B model.", "14", "13"),
    ("Context was 128K tokens.", "Context was 12K tokens.", "128", "12"),
    ("Uptime was 99.9%.", "Uptime was 99%.", "99.9", "99"),
    ("We served 8x7B on two nodes.", "We served 8x22B on two nodes.", "7", "22"),
])
def test_a_changed_number_in_these_forms_is_caught_by_the_number_guard(raw, cleaned, dropped, added):
    problems = check_turn(raw, turn(cleaned))
    assert f"number dropped: ['{dropped}']" in problems and f"number added: ['{added}']" in problems


def test_an_invented_model_size_is_caught():
    problems = check_turn("We compared the 14B model with another one.",
                          turn("We compared the 14B model with another 70B one."))
    assert "number added: ['70']" in problems and "new words: ['70b']" in problems
