"""After the HR round: transcript, who said what, and the evaluation. Track H.

Decision 27: the HR round is one combined recording (no S3 bucket for Daily's
per-person tracks). So after the call:

1. The recording sweep (app/recordings.py) collects the file from Daily.
2. `advance()` notices, shrinks the audio with ffmpeg, and transcribes it with
   a speaker-separating model.
3. `attribute()` works out which voice is HR. HR asked the questions on the
   checklist, so the speaker whose words match those questions is HR; the
   other main voice is the candidate; anyone else is an "other voice". How sure
   that is gets recorded, and an uncertain attribution is shown as such.
4. The evaluator rates the candidate's answers against a fixed soft-skill
   rubric. Every quote must be found, word for word, in what the candidate
   said; one that is not is dropped, and a rating left with no quote becomes
   "no evidence". HR's notes are context, never evidence.
5. The system records the fact: `hr_booked` -> `hr_done`, with MITRA's
   recommendation beside it. HR decides from there.

Each step is idempotent and resumes where it left off, so a restart mid-way
loses nothing. A background loop (`hr_pipeline_forever`) drives it.
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from openai import AsyncOpenAI
from sqlalchemy import select

from app.db import Candidate, Interview, Job, RecordingStatus, get_sessionmaker
from bot.config import _TRUTHY, Settings
from hiring import usage
from hiring.models import HRRound, Screening
from hiring.stages import StageError, move
from screening.fit import _normalise, _sampling
from shared.branding import PROSE_STYLE
from shared.contracts import EvidenceQuote, Speaker
from shared.screening import HRRecommendation, HRRoundEvaluation, SoftSkillRating
from shared.stages import CandidateStage

PROMPT_VERSION = "hr-eval-2026-11-19-probe-only"
TRANSCRIBE_MODEL = "gpt-4o-transcribe-diarize"
#: OpenAI's upload limit for one transcription request.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
LOOP_SECONDS = 30
DISABLED_ENV = "HR_PIPELINE_DISABLED"

WAITING, TRANSCRIBING, EVALUATING, READY, FAILED = "waiting_recording", "transcribing", "evaluating", "ready", "failed"

#: The HR round's rubric. Fixed, so HR rounds compare across candidates; the
#: job's own competencies are the technical round's business.
SOFT_SKILLS = [
    ("communication", "Communication", "Explains clearly and plainly, answers the question asked."),
    ("collaboration", "Working with others", "Describes how they work with colleagues, clients and disagreement."),
    ("ownership", "Ownership", "Owns outcomes, including mistakes, with specific examples."),
    ("pressure", "Handling pressure and feedback", "Describes real pressure or criticism and what they did about it."),
    ("candour", "Candour about results", "Reports what happened, including what did not work, without spin."),
    ("motivation", "Motivation and practical fit", "Why this role, and the practical facts: notice, location, hours."),
]


class HREvaluationError(RuntimeError):
    pass


# ---- audio and transcription --------------------------------------------------------------


def prepare_audio(source: Path, workdir: Path) -> Path:
    """A small mono speech file the transcription model accepts.

    A 30-minute call recorded as video is far over the 25 MB upload limit; as
    16 kHz mono speech at 32 kbit/s it is about 7 MB. ffmpeg is in the image
    (Dockerfile). Without it, only a file already under the limit can go.
    """
    if shutil.which("ffmpeg"):
        target = workdir / "hr_round.mp3"
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "32k", str(target)],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0 or not target.exists():
            raise HREvaluationError(f"Could not extract the audio from the recording: {result.stderr.strip()[:300]}")
        return target
    if source.stat().st_size <= MAX_UPLOAD_BYTES:
        return source
    raise HREvaluationError(
        "The recording is too large to transcribe without ffmpeg, which is not installed here. "
        "Install ffmpeg (brew install ffmpeg) and retry."
    )


@dataclass
class Transcriber:
    api_key: str
    model: str = TRANSCRIBE_MODEL

    async def segments(self, recording: Path) -> list[dict]:
        """Speaker-labelled segments: speaker, text, start, end (seconds)."""
        client = AsyncOpenAI(api_key=self.api_key)
        with tempfile.TemporaryDirectory() as tmp:
            audio = prepare_audio(recording, Path(tmp))
            if audio.stat().st_size > MAX_UPLOAD_BYTES:
                raise HREvaluationError("Even as compressed speech, the recording is over the 25 MB upload limit.")
            t0 = usage.started()
            try:
                with audio.open("rb") as handle:
                    response = await client.audio.transcriptions.create(
                        model=self.model, file=handle, response_format="diarized_json", chunking_strategy="auto",
                    )
            except Exception as exc:  # noqa: BLE001
                await usage.record(stage="hr_transcription", purpose="Transcribing the HR round", model=self.model,
                                   started_at=t0, error=exc)
                raise HREvaluationError(f"Transcription failed: {exc}") from exc
        raw = getattr(response, "segments", None) or (response.get("segments") if isinstance(response, dict) else None) or []
        out = []
        for seg in raw:
            get = seg.get if isinstance(seg, dict) else (lambda k, s=seg: getattr(s, k, None))
            text = (get("text") or "").strip()
            if text:
                out.append({"speaker": str(get("speaker") or "?"), "text": text,
                            "start": float(get("start") or 0), "end": float(get("end") or 0)})
        duration = getattr(response, "duration", None) or (response.get("duration") if isinstance(response, dict) else None)
        seconds = float(duration) if duration else (max((o["end"] for o in out), default=0.0) or None)
        await usage.record(
            stage="hr_transcription", purpose="Transcribing the HR round", model=self.model, started_at=t0,
            response=response, audio_seconds=seconds,
            status="ok" if out else "schema_error", error=None if out else "Empty transcription",
            summary={"segments": len(out), "speakers": len({o["speaker"] for o in out})},
        )
        if not out:
            raise HREvaluationError("The transcription came back empty. Was anyone speaking?")
        return out


#: Deepgram's newest model, in its mixed-language mode, so Indian English and
#: Hindi-English code-switching are heard as spoken. Measured on two real HR
#: rounds (2026-11-19): far closer to what was said than the live feed, and,
#: unlike OpenAI's file models, it invented no sentences on quiet stretches.
DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_LANGUAGE = "multi"
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


def deepgram_key() -> str:
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


@dataclass
class DeepgramTranscriber:
    """The recording, transcribed after the call, one segment per speaker turn."""

    api_key: str
    model: str = DEEPGRAM_MODEL
    language: str = DEEPGRAM_LANGUAGE

    async def segments(self, recording: Path) -> list[dict]:
        """Speaker-labelled segments: speaker, text, start, end (seconds)."""
        import httpx

        label = f"deepgram/{self.model}"
        params = {"model": self.model, "language": self.language, "diarize": "true", "utterances": "true",
                  "punctuate": "true", "smart_format": "true"}
        with tempfile.TemporaryDirectory() as tmp:
            audio = prepare_audio(recording, Path(tmp))
            t0 = usage.started()
            try:
                async with httpx.AsyncClient(timeout=600) as client:
                    response = await client.post(
                        DEEPGRAM_URL, params=params, content=audio.read_bytes(),
                        headers={"Authorization": f"Token {self.api_key}", "Content-Type": "application/octet-stream"},
                    )
                if response.status_code != 200:
                    raise HREvaluationError(f"Deepgram answered {response.status_code}: {response.text[:200]}")
                body = response.json()
            except Exception as exc:  # noqa: BLE001
                await usage.record(stage="hr_transcription", purpose="Transcribing the HR round", model=label,
                                   started_at=t0, error=exc)
                if isinstance(exc, HREvaluationError):
                    raise
                raise HREvaluationError(f"Transcription failed: {exc}") from exc
        out = [
            {"speaker": str(u.get("speaker", "?")), "text": (u.get("transcript") or "").strip(),
             "start": float(u.get("start") or 0), "end": float(u.get("end") or 0)}
            for u in (body.get("results") or {}).get("utterances") or []
            if (u.get("transcript") or "").strip()
        ]
        seconds = (body.get("metadata") or {}).get("duration")
        await usage.record(
            stage="hr_transcription", purpose="Transcribing the HR round", model=label, started_at=t0,
            audio_seconds=float(seconds) if seconds else None,
            status="ok" if out else "schema_error", error=None if out else "Empty transcription",
            summary={"segments": len(out), "speakers": len({o["speaker"] for o in out}), "language": self.language},
        )
        if not out:
            raise HREvaluationError("The transcription came back empty. Was anyone speaking?")
        return out


def prefers_recording() -> bool:
    """Whether the HR round is evaluated from the recording's transcript rather
    than the live lines. On when Deepgram is configured: it was measured far
    more accurate than the live feed and invents nothing. OpenAI's file models
    stay the fallback transcriber but are not preferred over the live lines,
    because they can invent a sentence on a quiet stretch."""
    return bool(deepgram_key())


# ---- who said what ----------------------------------------------------------------------------

_STOP = set("a an the and or of to in on for with about you your what how why when do did can could would "
            "tell me us we i is are was were be have has it this that there their they at as by from".split())


def _keywords(text: str) -> set[str]:
    return {w for w in _normalise(text).split() if len(w) > 2 and w not in _STOP}


def attribute(segments: list[dict], questions: list[dict]) -> tuple[list[dict], dict]:
    """Turns with speakers named hr / candidate / other, and how sure that is."""
    # Merge consecutive segments from the same voice into turns.
    turns: list[dict] = []
    for seg in segments:
        if turns and turns[-1]["voice"] == seg["speaker"]:
            turns[-1]["text"] += " " + seg["text"]
            turns[-1]["end"] = seg["end"]
        else:
            turns.append({"voice": seg["speaker"], "text": seg["text"], "start": seg["start"], "end": seg["end"]})

    voices: dict[str, dict] = {}
    for t in turns:
        v = voices.setdefault(t["voice"], {"words": 0, "first": t["start"], "turns": []})
        v["words"] += len(t["text"].split())
        v["turns"].append(_keywords(t["text"]))

    def question_hits(voice: str) -> int:
        hits = 0
        for q in questions:
            want = _keywords(q.get("text", ""))
            if want and any(len(want & said) / len(want) >= 0.6 for said in voices[voice]["turns"]):
                hits += 1
        return hits

    scores = {v: question_hits(v) for v in voices}
    by_talk = sorted(voices, key=lambda v: -voices[v]["words"])
    main = by_talk[:2]
    if len(main) == 1:
        hr, candidate = None, main[0]
        method, confidence = "one voice only", "uncertain"
    else:
        a, b = main
        if scores[a] != scores[b]:
            hr = a if scores[a] > scores[b] else b
            method = "matched the HR questions"
            gap = abs(scores[a] - scores[b])
            confidence = "clear" if max(scores[a], scores[b]) >= 2 and gap >= 2 else "likely"
        else:
            hr = min(main, key=lambda v: voices[v]["first"])
            method = "spoke first (the questions did not tell the voices apart)"
            confidence = "uncertain"
        candidate = b if hr == a else a

    named = []
    for i, t in enumerate(turns):
        who = "hr" if t["voice"] == hr else "candidate" if t["voice"] == candidate else "other"
        named.append({"index": i, "speaker": who, "text": t["text"].strip(), "start": round(t["start"], 2), "end": round(t["end"], 2)})
    others = [v for v in voices if v not in (hr, candidate)]
    return named, {
        "method": method, "confidence": confidence, "voices": len(voices), "other_voices": len(others),
        "question_matches": {"hr": scores.get(hr, 0) if hr else 0, "candidate": scores.get(candidate, 0)},
    }


# ---- the evaluation ----------------------------------------------------------------------------

SYSTEM = f"""\
You evaluate a human HR round for an HR partner, who decides. You do not.

You will see the conversation, with HR's lines and the candidate's lines
labelled, and the candidate's lines numbered. Rate the candidate on each of
these, 0 to 4, or null when the conversation gave nothing to go on:
{chr(10).join(f"- {key}: {name}. {desc}" for key, name, desc in SOFT_SKILLS)}

Scale: 0 clearly lacking, 1 weak, 2 adequate, 3 good with specifics, 4 strong,
specific and owned. null is not a low score.

For every rating above 0, quote the candidate WORD FOR WORD, one to two short
passages of at least six words, with the number of the line they come from.
Never quote HR. Never paraphrase. A passage you cannot copy exactly is not
evidence; use null.

HR's notes are given for context. They are not evidence: never quote them and
never let them stand in for what the candidate said.

Then recommend one of: proceed_to_technical, reject, discuss (the evidence
points neither way). And list up to four things the MITRA interview round
should probe: something about the candidate's work or skills that this
conversation left thin, unclear or unchecked. Each becomes one neutral
question in MITRA's plan, so write each as a gap in the evidence, never as a
verdict about the person ("how they sized and ended an A/B test was not
covered", not "weak at experimentation"). Leave out anything that is not about
the work itself, such as notice period, salary or location, and anything that
was already answered well. An empty list is a fine answer.

Never infer emotion, confidence or personality from tone; judge what was said.

Return JSON only:
{{
  "ratings": [{{"key": "communication", "rating": 3, "quotes": [{{"line": 4, "text": "..."}}], "reasoning": "one sentence"}}],
  "recommendation": "proceed_to_technical",
  "summary": "two or three sentences for HR",
  "findings_for_technical": ["..."]
}}

{PROSE_STYLE}
"""


@dataclass
class Evaluator:
    api_key: str
    model: str

    async def evaluate(self, *, candidate_id: str, interview_id: str, job_title: str, turns: list[dict],
                       notes: str, asked: list[str], attribution: dict) -> HRRoundEvaluation:
        lines = []
        for t in turns:
            who = {"hr": "HR", "candidate": f"CANDIDATE [{t['index']}]", "other": "OTHER VOICE"}[t["speaker"]]
            lines.append(f"{who}: {t['text']}")
        user = (
            f"THE ROLE: {job_title}\n\nTHE CONVERSATION:\n" + "\n".join(lines)
            + f"\n\nHR'S NOTES (context only, not evidence):\n{notes.strip() or '(none)'}"
        )
        client = AsyncOpenAI(api_key=self.api_key)
        t0 = usage.started()
        response = None
        try:
            response = await client.chat.completions.create(
                model=self.model, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                **_sampling(self.model),
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            await usage.record(stage="hr_evaluation", purpose="Evaluating the HR round", model=self.model,
                               started_at=t0, response=response, error=exc, prompt_version=PROMPT_VERSION,
                               status="schema_error" if response is not None else "error",
                               candidate_id=candidate_id, interview_id=interview_id)
            raise HREvaluationError(f"The evaluation model call failed: {exc}") from exc
        evaluation = build_evaluation(payload, candidate_id=candidate_id, interview_id=interview_id, turns=turns,
                                      asked=asked, attribution=attribution, model=self.model)
        await usage.record(
            stage="hr_evaluation", purpose="Evaluating the HR round", model=self.model, started_at=t0,
            response=response, prompt_version=PROMPT_VERSION, candidate_id=candidate_id, interview_id=interview_id,
            summary={
                "turns": len(turns), "skills_rated": sum(1 for r in evaluation.ratings if r.rating is not None),
                "quotes_kept": sum(len(r.evidence) for r in evaluation.ratings),
                "recommendation": evaluation.recommendation.value,
            },
            output_preview=evaluation.summary,
        )
        return evaluation


def build_evaluation(payload: dict, *, candidate_id: str, interview_id: str, turns: list[dict], asked: list[str],
                     attribution: dict, model: str) -> HRRoundEvaluation:
    """Keep only what the candidate actually said."""
    said = {t["index"]: t for t in turns if t["speaker"] == "candidate"}
    norm = {i: _normalise(t["text"]) for i, t in said.items()}
    by_key = {str(r.get("key")): r for r in (payload.get("ratings") or []) if isinstance(r, dict)}
    ratings = []
    for key, name, _ in SOFT_SKILLS:
        raw = by_key.get(key, {})
        value = raw.get("rating")
        rating = value if isinstance(value, int) and 0 <= value <= 4 else None
        evidence: list[EvidenceQuote] = []
        for q in raw.get("quotes") or []:
            text = str(q.get("text", "")).strip() if isinstance(q, dict) else ""
            n = _normalise(text)
            if len(n) < 16:
                continue
            claimed = q.get("line") if isinstance(q, dict) else None
            # The claimed line first; anywhere the candidate said it otherwise.
            found = claimed if claimed in norm and n in norm[claimed] else next((i for i, s in norm.items() if n in s), None)
            if found is not None:
                evidence.append(EvidenceQuote(text=text, turn_index=found, at_seconds=said[found]["start"], speaker=Speaker.CANDIDATE))
        reasoning = str(raw.get("reasoning") or "").strip()
        if rating is not None and rating > 0 and not evidence:
            rating = None
            reasoning = (reasoning + " " if reasoning else "") + "(The quoted words could not be found in what the candidate said.)"
        ratings.append(SoftSkillRating(name=name, rating=rating, evidence=evidence[:2] if rating else [], reasoning=reasoning))

    try:
        recommendation = HRRecommendation(str(payload.get("recommendation")))
    except ValueError:
        recommendation = HRRecommendation.DISCUSS
    summary = str(payload.get("summary") or "").strip()
    if attribution.get("confidence") == "uncertain":
        summary += " Note: MITRA could not reliably tell HR's voice from the candidate's in this recording, so check the transcript before relying on any quote."
    return HRRoundEvaluation(
        candidate_id=candidate_id, interview_id=interview_id, ratings=ratings, questions_asked=asked,
        recommendation=recommendation, summary=summary.strip(),
        findings_for_technical=[str(f).strip() for f in (payload.get("findings_for_technical") or []) if str(f).strip()][:4],
        model=model, prompt_version=PROMPT_VERSION,
    )


async def findings_for_technical(candidate_id: str) -> list[str]:
    """What MITRA's plan should probe, from the candidate's latest evaluated HR round.

    Read by Track I's plan preparation (live/hr_hook.py) when MITRA's plan is
    prepared. Only the model's findings: HR's own notes are never findings. An
    HR round completed on notes alone, or not evaluated, gives none.
    """
    async with get_sessionmaker()() as session:
        hr = (
            await session.scalars(
                select(HRRound)
                .where(HRRound.candidate_id == candidate_id, HRRound.evaluation.isnot(None))
                .order_by(HRRound.id.desc())
            )
        ).first()
    if hr is None:
        return []
    evaluation = HRRoundEvaluation.model_validate(hr.evaluation)
    return [f.strip() for f in evaluation.findings_for_technical if f and f.strip()]


def recommendation_text(evaluation: HRRoundEvaluation) -> str:
    word = {"proceed_to_technical": "proceed to the MITRA interview round", "reject": "reject", "discuss": "discuss: the evidence points neither way"}
    return f"HR round evaluation recommends: {word[evaluation.recommendation.value]}."


# ---- the pipeline -------------------------------------------------------------------------------


def _collector():
    """Downloads one finished HR round recording from Daily."""
    return collect_recording


async def collect_recording(interview_id: str) -> str:
    """Fetch this round's finished recording from Daily.

    The app-wide collector does it (app/recordings.collect_one): it claims the
    recording first, so this loop and the recording sweep never both download
    it or both delete Daily's copy, checks the file plays, and stores its path
    where the rest of the app looks for it. Answers `stored`, `waiting`,
    `unavailable`, `busy` (the sweep has it right now) or `error`.
    """
    from app.recordings import collect_one

    return await collect_one(interview_id, api_key=Settings.load().daily_api_key)


def _transcriber() -> Transcriber | DeepgramTranscriber:
    """Deepgram when DEEPGRAM_API_KEY is set (the same switch as the bot's STT),
    else OpenAI's diarizing transcription."""
    if deepgram_key():
        return DeepgramTranscriber(api_key=deepgram_key())
    settings = Settings.load()
    return Transcriber(api_key=settings.openai_api_key, model=os.environ.get("OPENAI_TRANSCRIBE_MODEL") or TRANSCRIBE_MODEL)


def _evaluator() -> Evaluator:
    settings = Settings.load()
    return Evaluator(api_key=settings.openai_api_key, model=os.environ.get("OPENAI_HR_EVAL_MODEL") or settings.legacy_blueprint_model)


async def _fail(interview_id: str, message: str) -> None:
    async with get_sessionmaker()() as session:
        hr = (await session.execute(select(HRRound).where(HRRound.interview_id == interview_id))).scalar_one()
        hr.eval_status, hr.eval_error = FAILED, message
        await session.commit()
    logger.warning(f"[{interview_id}] HR round evaluation failed: {message}")


def has_live_lines(hr: HRRound) -> bool:
    """The round was transcribed live (Daily, one transcription per person) and the candidate spoke."""
    return (hr.attribution or {}).get("method") in ("live", "live_one_mic") and any(
        t.get("speaker") == "candidate" for t in (hr.transcript or [])
    )


async def advance(interview_id: str) -> str:
    """Move one ended HR round one or more steps on. Returns its status."""
    from app.recordings import stored_path

    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(Interview, HRRound).join(HRRound, HRRound.interview_id == Interview.id)
                .where(Interview.id == interview_id)
            )
        ).one_or_none()
        if row is None:
            return "missing"
        interview, hr = row
        status = hr.eval_status
        if status == WAITING:
            if interview.recording_status == RecordingStatus.STORED:
                hr.eval_status = status = TRANSCRIBING
                await session.commit()
            elif interview.recording_status in (RecordingStatus.UNAVAILABLE, RecordingStatus.NOT_RECORDED, RecordingStatus.DELETED, RecordingStatus.EXPIRED):
                if has_live_lines(hr):
                    # No recording after all: the live lines are the transcript.
                    hr.attribution = {**(hr.attribution or {}), "recording_check": "no recording; the live lines were used"}
                    hr.eval_status = status = EVALUATING
                    await session.commit()
                else:
                    await _fail(interview_id, interview.recording_error or "No recording of this HR round is available.")
                    return FAILED
            else:
                status = "collect"
        if status != "collect":
            path = stored_path(interview)
            candidate = await session.get(Candidate, interview.candidate_id)
            job = await session.get(Job, candidate.job_id) if candidate else None
            screening = (await session.execute(select(Screening).where(Screening.candidate_id == interview.candidate_id))).scalar_one_or_none()
            questions = (screening.hr_questions or {}).get("questions", []) if screening else []
            asked, notes = list(hr.questions_asked or []), hr.notes or ""

    if status == "collect":
        # Fetch the recording now rather than waiting for the app-wide sweep,
        # which runs every five minutes: HR is watching this screen.
        outcome = await _collector()(interview_id)
        if outcome in ("stored", "unavailable"):
            return await advance(interview_id)
        return WAITING

    if status == TRANSCRIBING:
        if path is None or not path.exists():
            await _fail(interview_id, "The recording file is missing from storage.")
            return FAILED
        async with get_sessionmaker()() as session:
            hr = (await session.execute(select(HRRound).where(HRRound.interview_id == interview_id))).scalar_one()
            live = has_live_lines(hr)
        try:
            with usage.context(interview_id=interview_id):
                segments = await _transcriber().segments(path)
            turns, attribution = attribute(segments, questions)
        except HREvaluationError as exc:
            if not live:
                await _fail(interview_id, str(exc))
                return FAILED
            turns, attribution = None, {"recording_check": f"the recording could not be transcribed ({exc}); the live lines were used"}
        if live and turns is not None and attribution.get("confidence") == "uncertain":
            # The recording is one mixed track, so its voices are told apart by
            # guesswork; the live lines name each speaker for certain. When the
            # guess is unsure, the certain speakers win over the better words.
            turns, attribution = None, {"recording_check": f"the recording's voices could not be told apart ({attribution.get('method')}); the live lines were used"}
        async with get_sessionmaker()() as session:
            hr = (await session.execute(select(HRRound).where(HRRound.interview_id == interview_id))).scalar_one()
            if turns is None:
                hr.attribution = {**(hr.attribution or {}), **attribution}
            else:
                if live:
                    # Kept, not thrown away: what HR watched during the call.
                    attribution = {**attribution, "live_transcript": hr.transcript}
                hr.transcript, hr.attribution = turns, attribution
            hr.eval_status = EVALUATING
            await session.commit()
        status = EVALUATING

    if status == EVALUATING:
        async with get_sessionmaker()() as session:
            hr = (await session.execute(select(HRRound).where(HRRound.interview_id == interview_id))).scalar_one()
            turns, attribution = hr.transcript or [], hr.attribution or {}
        if not any(t["speaker"] == "candidate" for t in turns):
            await _fail(interview_id, "The transcript has nothing from the candidate to evaluate.")
            return FAILED
        try:
            evaluation = await _evaluator().evaluate(
                candidate_id=candidate.id, interview_id=interview_id, job_title=job.title if job else "",
                turns=turns, notes=notes, asked=asked, attribution=attribution,
            )
        except HREvaluationError as exc:
            await _fail(interview_id, str(exc))
            return FAILED
        async with get_sessionmaker()() as session:
            hr = (await session.execute(select(HRRound).where(HRRound.interview_id == interview_id))).scalar_one()
            hr.evaluation = evaluation.model_dump(mode="json")
            hr.eval_status, hr.eval_error, hr.evaluated_at = READY, None, datetime.now(UTC)
            fresh = await session.get(Candidate, candidate.id)
            if fresh.stage == CandidateStage.HR_BOOKED:
                try:
                    await move(session, fresh, CandidateStage.HR_DONE, by=None, model_recommendation=recommendation_text(evaluation))
                except StageError as exc:
                    logger.warning(f"[{interview_id}] evaluation stored but the stage could not move: {exc}")
            await session.commit()
        logger.info(f"[{interview_id}] HR round evaluated: {evaluation.recommendation.value}")
        return READY
    return status


async def run_once() -> int:
    """One pass over every HR round waiting on after-call work."""
    async with get_sessionmaker()() as session:
        pending = list(
            await session.scalars(
                select(HRRound.interview_id).where(HRRound.eval_status.in_([WAITING, TRANSCRIBING, EVALUATING]))
            )
        )
    for interview_id in pending:
        try:
            await advance(interview_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{interview_id}] HR round pipeline step failed")
            await _fail(interview_id, f"Stopped unexpectedly: {exc}")
    return len(pending)


def pipeline_enabled() -> bool:
    return os.environ.get(DISABLED_ENV, "").strip().lower() not in _TRUTHY


async def hr_pipeline_forever() -> None:
    """The background loop, started by the app's lifespan: HR rounds, then MITRA interview rounds."""
    while True:
        await asyncio.sleep(LOOP_SECONDS)
        try:
            await run_once()
            # The technical round's after-call bookkeeping rides the same loop.
            from hiring.technical import sweep_once

            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"HR round pipeline pass failed, will run again: {exc}")

