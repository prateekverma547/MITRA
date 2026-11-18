"""After the HR round: who said what, what counts as evidence, and the pipeline
from a stored recording to `hr_done`. Models and Daily are stubbed."""

import json
from pathlib import Path

import pytest

from hiring import hr_evaluation as pipeline
from hiring.hr_evaluation import HREvaluationError, attribute, build_evaluation, prepare_audio
from shared.screening import HRRecommendation, HRRoundEvaluation
from tests.console_support import org_client, sign_in  # noqa: F401

QUESTIONS = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "hr_questions.json").read_text()
)["questions"]


def seg(speaker, text, start):
    return {"speaker": speaker, "text": text, "start": start, "end": start + 5}


CALL = [
    seg("A", "Hello, thanks for joining. Walk me through what you own end to end in your current role.", 0),
    seg("B", "Sure. I own the voice agent platform end to end, from data collection to the deployed service.", 6),
    seg("A", "Tell me about a time a client wanted a number your results did not support.", 20),
    seg("B", "We told them the number was chance and showed them why the labels were the problem.", 26),
    seg("B", "They were unhappy for a week, then fixed their labelling process.", 31),
    seg("A", "What is your notice period, and what are your expectations?", 40),
    seg("B", "Sixty days, and I am looking for a role with more ownership of evaluation.", 46),
]


# ---- who said what ----------------------------------------------------------------------------


def test_the_voice_that_asked_the_hr_questions_is_hr():
    turns, attribution = attribute(CALL, QUESTIONS)
    assert [t["speaker"] for t in turns] == ["hr", "candidate", "hr", "candidate", "hr", "candidate"]
    assert attribution["confidence"] == "clear"
    assert attribution["method"] == "matched the HR questions"
    # Consecutive segments from one voice become one turn.
    assert turns[3]["text"].endswith("fixed their labelling process.")


def test_when_the_questions_do_not_tell_voices_apart_it_says_so():
    vague = [seg("X", "Hello there, nice to meet you today.", 0), seg("Y", "Hello, likewise, thank you.", 4)]
    _, attribution = attribute(vague, QUESTIONS)
    assert attribution["confidence"] == "uncertain"
    assert "spoke first" in attribution["method"]


def test_a_third_voice_is_marked_as_another_voice():
    call = CALL + [seg("C", "Say the bit about Kafka.", 60)]
    turns, attribution = attribute(call, QUESTIONS)
    assert turns[-1]["speaker"] == "other"
    assert attribution["other_voices"] == 1


# ---- what counts as evidence -------------------------------------------------------------------


def evaluate(payload, attribution=None):
    turns, auto = attribute(CALL, QUESTIONS)
    return build_evaluation(payload, candidate_id="c", interview_id="i", turns=turns, asked=["hr1"],
                            attribution=attribution or auto, model="m")


def test_a_quote_must_be_the_candidates_own_words():
    result = evaluate({
        "ratings": [
            {"key": "candour", "rating": 4, "quotes": [{"line": 3, "text": "we told them the number was chance and showed them why"}]},
            {"key": "ownership", "rating": 3, "quotes": [{"line": 1, "text": "I personally rebuilt the whole platform in a weekend"}]},
            # HR's words are never evidence about the candidate.
            {"key": "communication", "rating": 3, "quotes": [{"line": 0, "text": "Walk me through what you own end to end in your current role"}]},
            # The right words on the wrong line are moved to where they were said.
            {"key": "motivation", "rating": 3, "quotes": [{"line": 1, "text": "looking for a role with more ownership of evaluation"}]},
        ],
        "recommendation": "proceed_to_technical", "summary": "Candid.",
    })
    by = {r.name: r for r in result.ratings}
    assert by["Candour about results"].rating == 4
    assert by["Ownership"].rating is None and "could not be found" in by["Ownership"].reasoning
    assert by["Communication"].rating is None
    assert by["Motivation and practical fit"].evidence[0].turn_index == 5
    assert result.recommendation is HRRecommendation.PROCEED_TO_TECHNICAL


def test_an_unknown_recommendation_becomes_discuss_and_uncertainty_is_said():
    result = evaluate({"ratings": [], "recommendation": "hire them now"}, attribution={"confidence": "uncertain"})
    assert result.recommendation is HRRecommendation.DISCUSS
    assert "could not reliably tell" in result.summary


def test_a_big_recording_without_ffmpeg_fails_with_how_to_fix_it(tmp_path, monkeypatch):
    big = tmp_path / "round.mp4"
    big.write_bytes(b"0" * (pipeline.MAX_UPLOAD_BYTES + 1))
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)
    with pytest.raises(HREvaluationError, match="brew install ffmpeg"):
        prepare_audio(big, tmp_path)


# ---- the pipeline, through the API ---------------------------------------------------------------


class StubTranscriber:
    async def segments(self, recording):
        return CALL


class StubEvaluator:
    async def evaluate(self, *, candidate_id, interview_id, job_title, turns, notes, asked, attribution):
        return build_evaluation(
            {"ratings": [{"key": "candour", "rating": 4, "quotes": [{"line": 3, "text": "we told them the number was chance and showed them why the labels were the problem"}]}],
             "recommendation": "proceed_to_technical", "summary": "Candid about results.",
             "findings_for_technical": ["How the 97 percent figure was measured"]},
            candidate_id=candidate_id, interview_id=interview_id, turns=turns, asked=asked,
            attribution=attribution, model="stub",
        )


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    from bot.services import daily

    tokens = []

    async def room(**kwargs):
        class R:
            name, url = "hr-room", "https://example.daily.co/hr-room"
        return R()

    async def token(**kwargs):
        tokens.append(kwargs)
        return "tok"

    monkeypatch.setattr(daily, "create_room", room)
    monkeypatch.setattr(daily, "create_meeting_token", token)
    monkeypatch.setattr(pipeline, "_transcriber", lambda: StubTranscriber())
    monkeypatch.setattr(pipeline, "_evaluator", lambda: StubEvaluator())
    return tokens


async def held_round(client, tmp_path, monkeypatch, *, recorded=True) -> tuple[str, str]:
    """Book, join, take notes and end an HR round; store a recording if `recorded`."""
    from sqlalchemy import select

    from app import db, recordings
    from app.db import Candidate, Interview, RecordingStatus
    from hiring.models import Screening

    cid = client.demo.candidate_ids["ai"]
    async with db.get_sessionmaker()() as session:
        (await session.get(Candidate, cid)).stage = "ready_for_hr"
        session.add(Screening(candidate_id=cid, status="ready", hr_questions={"questions": QUESTIONS}))
        await session.commit()
    sign_in(client, "hr@mitra.local")
    client.post(f"/api/candidates/{cid}/hr-round")
    if recorded:
        client.post(f"/api/candidates/{cid}/hr-round/join")
    else:
        # The candidate joined but HR never did, so nothing was recorded.
        async with db.get_sessionmaker()() as session:
            interview = (await session.execute(select(Interview).where(Interview.candidate_id == cid))).scalars().first()
            interview.status = "in_progress"
            await session.commit()
    client.post(f"/api/candidates/{cid}/hr-round/progress", json={"asked": ["hr1", "hr2"], "notes": "Clear and candid."})
    client.post(f"/api/candidates/{cid}/hr-round/end")
    view = client.get(f"/api/candidates/{cid}/hr-round").json()
    interview_id = view["round"]["interview_id"]
    if recorded:
        monkeypatch.setattr(recordings, "SESSIONS_DIR", tmp_path)
        (tmp_path / "rec").mkdir(exist_ok=True)
        (tmp_path / "rec" / "round.mp4").write_bytes(b"audio")
        async with db.get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            interview.recording_status = RecordingStatus.STORED
            interview.recording_path = "rec/round.mp4"
            await session.commit()
    return cid, interview_id


async def test_hrs_token_starts_the_recording(org_client, stubs, tmp_path, monkeypatch):
    cid, _ = await held_round(org_client, tmp_path, monkeypatch)
    hr_token = [t for t in stubs if t.get("is_owner")][-1]
    assert hr_token["start_cloud_recording"] is True


async def test_a_stored_recording_becomes_an_evaluation_and_hr_done(org_client, tmp_path, monkeypatch):
    cid, interview_id = await held_round(org_client, tmp_path, monkeypatch)
    assert org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]["eval_status"] == "waiting_recording"

    assert await pipeline.advance(interview_id) == "ready"
    view = org_client.get(f"/api/candidates/{cid}/hr-round").json()
    r = view["round"]
    assert view["candidate"]["stage"] == "hr_done"
    assert r["attribution"]["confidence"] == "clear"
    assert [t["speaker"] for t in r["transcript"]][:2] == ["hr", "candidate"]
    evaluation = HRRoundEvaluation.model_validate(r["evaluation"])
    assert evaluation.questions_asked == ["hr1", "hr2"]
    assert evaluation.findings_for_technical == ["How the 97 percent figure was measured"]

    history = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"]
    assert history[-1]["actor"] == "system" and history[-1]["to"] == "hr_done"
    assert "proceed to the technical round" in history[-1]["model_recommendation"]

    moved = org_client.post(f"/api/candidates/{cid}/move", json={"to": "ready_for_technical"})
    assert moved.status_code == 200
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["actor"] == "hr" and "proceed to the technical round" in last["model_recommendation"]


async def test_a_round_without_a_recording_is_completed_on_notes(org_client, tmp_path, monkeypatch):
    cid, interview_id = await held_round(org_client, tmp_path, monkeypatch, recorded=False)
    r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert r["eval_status"] == "failed" and "did not join" in r["eval_error"]
    assert r["can_complete_on_notes"]
    # There is no recording to retry from, and the answer says what to do instead.
    refused = org_client.post(f"/api/candidates/{cid}/hr-round/retry")
    assert refused.status_code == 409 and "notes" in refused.json()["detail"]

    assert org_client.post(f"/api/candidates/{cid}/hr-round/complete", json={"reason": " "}).status_code == 422
    done = org_client.post(f"/api/candidates/{cid}/hr-round/complete", json={"reason": "Held it; notes are clear."})
    assert done.status_code == 200 and done.json()["candidate"]["stage"] == "hr_done"
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["actor"] == "hr" and last["reason"] == "Held it; notes are clear."


async def test_a_failed_step_can_be_retried_from_where_it_stopped(org_client, tmp_path, monkeypatch):
    cid, interview_id = await held_round(org_client, tmp_path, monkeypatch)

    class Broken:
        async def evaluate(self, **kwargs):
            raise HREvaluationError("The evaluation model call failed: timeout")

    monkeypatch.setattr(pipeline, "_evaluator", lambda: Broken())
    assert await pipeline.advance(interview_id) == "failed"
    r = org_client.get(f"/api/candidates/{cid}/hr-round").json()["round"]
    assert r["transcript"] and "timeout" in r["eval_error"] and r["can_retry"]

    monkeypatch.setattr(pipeline, "_evaluator", lambda: StubEvaluator())
    retried = org_client.post(f"/api/candidates/{cid}/hr-round/retry")
    assert retried.status_code == 200
    # The background task resumes at evaluation, using the transcript it already has.
    assert org_client.get(f"/api/candidates/{cid}/hr-round").json()["candidate"]["stage"] == "hr_done"
