"""The second-voice check (Session D) on CONSTRUCTED cases with known answers.

Built from aftercall/fixtures (LibriSpeech readers 1462 and 2428, CC BY 4.0, and
11 s of Mitra's own "cedar" voice not used to build mitra_voice.json) by
aftercall/testset.py. The case kinds and why each answer is expected are in
D_findings D-50 to D-52. Positive controls first: a test that finds nothing only
means something once the same machinery is shown finding something.

Needs the speaker model (python -m aftercall.models fetch); skipped, saying so,
where it is not installed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from aftercall import models, runner, settings, store, testset as ts
from aftercall import second_voice as sv
from aftercall.features import fbank
from shared.interview_contracts import CandidateAudioCapture, CaptureManifest, SecondVoiceSignal

FIX = Path(__file__).resolve().parents[1] / "aftercall" / "fixtures"

try:
    models.verified_path(models.SPEAKER)
    models.verified_path(models.VAD)
    HAVE_MODELS = True
except models.ModelUnavailable:
    HAVE_MODELS = False
needs_models = pytest.mark.skipif(not HAVE_MODELS, reason="speaker model not installed: python -m aftercall.models fetch")


@pytest.fixture(scope="module")
def sources():
    return ([ts.load(FIX / "candidate_ls1462.wav")], [ts.load(FIX / "other_ls2428.wav")], [ts.load(FIX / "mitra_cedar.wav")])


_CACHE: dict = {}


def case(sources, kind, seed):
    """Build and measure once per (kind, seed) for the whole module."""
    if (kind, seed) not in _CACHE:
        c, o, m = sources
        built = ts.build(kind, c, o, m, seed=seed, seconds=60)
        _CACHE[(kind, seed)] = (built, sv.measure(built.audio))
    return _CACHE[(kind, seed)]


def found(sources, kind, seed, **kw):
    built, meas = case(sources, kind, seed)
    return built, sv.decide(meas, built.mitra_turns if kw.pop("exclude", True) else None,
                            exclude_mitra=kw.pop("exclude_mitra", True), **kw)


# -- the instrument ---------------------------------------------------------------------------------------


@needs_models
def test_our_features_give_sherpa_onnxs_embedding():
    """features.py was written to reproduce sherpa-onnx (D-48); the golden
    embeddings were made by sherpa-onnx 2.0.1 on the fixture clip."""
    gold = json.loads((FIX / "sherpa_golden.json").read_text())
    assert gold["model_sha256"] == models.SPEAKER.sha256
    x = ts.load(FIX / gold["file"])
    sess = models.session(models.SPEAKER)
    for seg in gold["segments"]:
        ours = sess.run(None, {"feats": fbank(x[seg["start_sample"]:seg["end_sample"]])[None]})[0][0]
        theirs = np.asarray(seg["embedding"])
        cos = float(ours @ theirs / np.linalg.norm(ours) / np.linalg.norm(theirs))
        assert cos > 0.9999, cos


def test_a_changed_or_missing_model_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(settings.MODELS_DIR_ENV, str(tmp_path))
    with pytest.raises(models.ModelUnavailable, match="not installed"):
        models.verified_path(models.SPEAKER)
    (tmp_path / models.SPEAKER.name).write_bytes(b"\0" * models.SPEAKER.size)
    with pytest.raises(models.ModelUnavailable, match="checksum"):
        models.verified_path(models.SPEAKER)


def test_fbank_frames_and_shape():
    x = np.zeros(16000, dtype=np.float32)
    assert fbank(x).shape == (100, 80)
    assert fbank(np.zeros(0, dtype=np.float32)).shape == (0, 80)


# -- positive controls: the check can find something -------------------------------------------------------


@needs_models
def test_a_clear_second_voice_is_found_where_it_is(sources):
    """Another person speaking in a pause in the middle of the candidate's answer."""
    built, r = found(sources, "two_mid_answer", 0)
    assert built.second_voice and r.ranges, "the check must be shown finding a second voice"
    assert ts.overlaps(r.ranges, built.truth) >= 1
    assert all(any(fa < b + 2 and fb > a - 2 for a, b in built.truth) for fa, fb in r.ranges), \
        "every range it reports is near a true one"
    assert 0 < r.confidence <= 1


@needs_models
def test_a_second_voice_over_the_candidate_is_found(sources):
    built, r = found(sources, "two_overlap", 0)
    assert ts.overlaps(r.ranges, built.truth) >= 1


@needs_models
def test_without_the_exclusion_mitras_leaked_voice_would_be_reported(sources):
    """The control for the leak tests below: the leak is there to be found."""
    built, r = found(sources, "mitra_leak_loud", 0, exclude=False, exclude_mitra=False)
    assert r.ranges and any(fa < b and fb > a for fa, fb in r.ranges for a, b in built.leak)


# -- no signal where there is no second voice ---------------------------------------------------------------


@needs_models
@pytest.mark.parametrize("kind,seed", [("one_speaker", 0), ("noise_echo", 0), ("long_silences", 0)])
def test_one_speaker_gives_no_signal(sources, kind, seed):
    built, r = found(sources, kind, seed)
    assert not built.second_voice and r.ranges == []


@needs_models
@pytest.mark.parametrize("kind", ["mitra_leak", "mitra_leak_loud"])
def test_mitras_leaked_voice_is_never_a_second_voice(sources, kind):
    """Priority 1 (Karandeep, 2026-11-19): no false alarm from Mitra's voice
    leaking from the candidate's speakers, quiet (-20 dB) or loud (-10 dB)."""
    built, r = found(sources, kind, 0)
    assert built.leak, "the case must contain a leak"
    assert r.ranges == []


# -- the accepted limitation, pinned -------------------------------------------------------------------------


@needs_models
@pytest.mark.xfail(strict=True, reason="accepted limitation D-52/D-53: a voice right after Mitra's question "
                                       "falls inside the 1.0 s echo tail and is usually missed")
@pytest.mark.parametrize("kind,seed", [("two_clear", 2), ("two_quiet", 1)])
def test_known_miss_a_second_voice_right_after_mitras_question(sources, kind, seed):
    built, r = found(sources, kind, seed)
    assert ts.overlaps(r.ranges, built.truth) >= 1


# -- Indian-English voices (made on this machine, never committed) ---------------------------------------------


@needs_models
@pytest.mark.skipif(shutil.which("say") is None, reason="macOS `say` is needed for the Indian-English voices")
def test_indian_english_voices(tmp_path, sources):
    text = ("In my last role I owned the payments service. The batch jobs kept missing their window, so I "
            "measured where the time went, split the job by merchant and ran the pieces in parallel. That "
            "brought the run from six hours down to about forty minutes. The harder part was the operations "
            "team, because their manual check depended on the old order, so I rewrote it as a query.")
    voices = {}
    for v in ("Rishi", "Tara"):
        out = tmp_path / f"{v}.wav"
        subprocess.run(["say", "-v", v, "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@16000", text],
                       check=True, timeout=120)
        voices[v] = ts.load(out)
    m = sources[2]
    one = ts.build("one_speaker", [voices["Rishi"]], [voices["Tara"]], m, seed=0, seconds=60)
    two = ts.build("two_mid_answer", [voices["Rishi"]], [voices["Tara"]], m, seed=0, seconds=60)
    assert sv.analyse(one.audio, one.mitra_turns).ranges == []
    assert ts.overlaps(sv.analyse(two.audio, two.mitra_turns).ranges, two.truth) >= 1


# -- inputs it refuses, and the output's wording -------------------------------------------------------------


@needs_models
def test_too_little_speech_is_skipped_with_a_reason():
    x = np.zeros(16000 * 20, dtype=np.float32)
    with pytest.raises(sv.CheckSkipped, match="too little speech"):
        sv.analyse(x, [])


def test_without_mitras_times_it_does_not_guess():
    with pytest.raises(sv.CheckSkipped, match="Mitra's speaking times"):
        sv.analyse(np.zeros(16000, dtype=np.float32), None)


def test_only_16_khz_mono_16_bit_is_read(tmp_path):
    import wave

    p = tmp_path / "x.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * 400)
    with pytest.raises(sv.CheckSkipped, match="16 kHz mono 16-bit"):
        sv.read_wav(p)


def test_wording_is_neutral_and_says_where():
    w = sv.wording([(754.0, 769.5), (800, 805), (900, 910), (1000, 1010)])
    assert w.startswith("A voice other than the candidate's may be audible at 12:34-12:50")
    assert "1 more place" in w
    SecondVoiceSignal(interview_id="i", ranges=[], confidence=0.5, method="m", method_version="1", wording=w)
    for word in ("cheat", "suspicious", "dishonest"):
        assert word not in w.lower()


# -- the whole path: WAV and manifest in, status and signal out --------------------------------------------------


@needs_models
def test_from_a_manifest_to_a_signal_on_the_interview_clock(tmp_path, monkeypatch, sources):
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "rec"))
    monkeypatch.setenv(settings.ENABLED_ENV, "1")
    built, _ = case(sources, "two_mid_answer", 0)
    iid, start = "int_sv1", 30.0
    ts.write(store.capture_dir(iid) / "candidate.wav", built.audio)
    store.manifest_path(iid).write_text(CaptureManifest(interview_id=iid, candidate_audio=CandidateAudioCapture(
        relative_path=f"{iid}/candidate.wav", start_seconds=start, duration_seconds=len(built.audio) / 16000)
    ).model_dump_json())
    turns = [(a + start, b + start) for a, b in built.mitra_turns]
    status = runner.run_job(iid, mitra_turns=turns)
    assert {c.check.value: c.state.value for c in status.checks} == {"second_voice": "done", "screen_content": "skipped"}
    signal = store.read_signal(iid)
    assert signal and signal.method_version == sv.METHOD_VERSION
    truth = [(a + start, b + start) for a, b in built.truth]
    assert ts.overlaps([(r.start_seconds, r.end_seconds) for r in signal.ranges], truth) >= 1
    assert store.read_events(iid)[0].kind.value == "second_voice"


# -- reading in pieces (D-62, D-63): the same numbers, a voice across a boundary, pause --------------------


def _same(a: sv.Measured, b: sv.Measured) -> bool:
    return (np.array_equal(a.starts, b.starts) and np.array_equal(a.emb, b.emb) and np.array_equal(a.mitra, b.mitra)
            and a.speech_seconds == b.speech_seconds and a.audio_seconds == b.audio_seconds)


@needs_models
def test_reading_in_pieces_gives_bit_identical_numbers(sources, tmp_path, monkeypatch):
    built, _ = case(sources, "two_mid_answer", 0)
    wav = tmp_path / "track.wav"
    ts.write(wav, built.audio)
    monkeypatch.setattr(sv, "CHUNK_S", 1e6)  # the whole track as one piece
    whole = sv.measure_file(wav)
    for seconds in (sv.CHUNK_S, 15.0, 2.3, 0.7):
        monkeypatch.setattr(sv, "CHUNK_S", seconds)
        assert _same(sv.measure_file(wav), whole), f"chunks of {seconds} s changed the numbers"


@needs_models
def test_a_second_voice_across_a_chunk_boundary_is_found_as_in_the_middle(sources, tmp_path, monkeypatch):
    built, _ = case(sources, "two_mid_answer", 0)
    wav = tmp_path / "track.wav"
    ts.write(wav, built.audio)
    a, b = built.truth[0]
    boundary = (a + b) / 2 + 0.013  # inside the voice, and not on a window start
    assert a < boundary < b
    monkeypatch.setattr(sv, "CHUNK_S", 1e6)
    whole = sv.measure_file(wav)
    monkeypatch.setattr(sv, "CHUNK_S", boundary)  # the first piece ends in the middle of the voice
    split = sv.measure_file(wav)
    assert _same(split, whole), "a window across the boundary was cut or lost"
    r_whole, r_split = sv.decide(whole, built.mitra_turns), sv.decide(split, built.mitra_turns)
    assert r_split.ranges == r_whole.ranges and ts.overlaps(r_split.ranges, [(a, b)]) == 1


@needs_models
def test_pause_takes_effect_between_pieces(sources, tmp_path, monkeypatch):
    built, _ = case(sources, "one_speaker", 0)
    wav = tmp_path / "track.wav"
    ts.write(wav, built.audio)
    monkeypatch.setattr(sv, "CHUNK_S", 10.0)
    fed = []
    real_feed = sv._Stream.feed

    def counting_feed(self, chunk):
        real_feed(self, chunk)
        fed.append(len(chunk))

    monkeypatch.setattr(sv._Stream, "feed", counting_feed)
    with pytest.raises(sv.Paused):
        sv.measure_file(wav, should_stop=lambda: len(fed) >= 2)
    assert len(fed) == 2, "it stopped at the next piece, not after reading everything"


def test_reading_from_a_file_refuses_other_formats(tmp_path):
    import wave

    p = tmp_path / "x.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * 400)
    with pytest.raises(sv.CheckSkipped, match="16 kHz mono 16-bit"):
        sv.measure_file(p)
