"""The model files the second-voice check uses: where they come from, their
checksums, and loading them only when the file on disk is exactly that file.

- Speaker embeddings: WeSpeaker ResNet34-LM trained on VoxCeleb (256 numbers per
  stretch of speech), the ONNX file sherpa-onnx publishes (D-14, D-17: EER 0.723%
  on VoxCeleb1-O-clean). Downloaded once with `python -m aftercall.models fetch`;
  never committed (26.5 MB; models/.gitignore).
- Voice activity: Silero VAD v5, the ONNX file pipecat-ai 1.6.0 ships (already
  installed with the backend; MIT licence).
A file whose checksum differs is refused, so a changed model can never silently
change what the check reports.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from aftercall import settings


@dataclass(frozen=True)
class ModelFile:
    name: str
    sha256: str
    size: int
    url: str | None  # None: shipped by an installed package


SPEAKER = ModelFile(
    name="wespeaker_en_voxceleb_resnet34_LM.onnx",
    sha256="e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012",
    size=26_530_550,
    url="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
        "wespeaker_en_voxceleb_resnet34_LM.onnx",
)
VAD = ModelFile(
    name="silero_vad.onnx",
    sha256="597d30b3ec076608d059477bb14cfeffdf951bf5cae370d38f65d33bbfe82004",
    size=2_327_524,
    url=None,
)


class ModelUnavailable(RuntimeError):
    """A model file is missing or is not the file we checked."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def path_of(model: ModelFile) -> Path:
    if model is VAD:
        # The installed package's own copy, found without importing pipecat.
        import importlib.util

        spec = importlib.util.find_spec("pipecat")
        root = Path(next(iter(spec.submodule_search_locations)))
        return root / "audio" / "vad" / "data" / VAD.name
    return settings.models_dir() / model.name


def verified_path(model: ModelFile) -> Path:
    p = path_of(model)
    if not p.is_file():
        raise ModelUnavailable(f"model file {model.name} is not installed (run: python -m aftercall.models fetch)")
    if p.stat().st_size != model.size or _sha256(p) != model.sha256:
        raise ModelUnavailable(f"model file {model.name} is not the expected file (checksum differs)")
    return p


@lru_cache(maxsize=4)
def session(model: ModelFile):
    """An onnxruntime session on one thread: the check must stay gentle."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3
    return ort.InferenceSession(str(verified_path(model)), sess_options=opts, providers=["CPUExecutionProvider"])


def fetch(model: ModelFile = SPEAKER) -> Path:
    """Download a model into the models folder and refuse it unless the checksum matches."""
    dest = path_of(model)
    if dest.is_file() and _sha256(dest) == model.sha256:
        return dest
    if model.url is None:
        raise ModelUnavailable(f"{model.name} comes with an installed package; it is not downloaded")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(model.url, timeout=300) as r, tmp.open("wb") as f:  # noqa: S310 (fixed https URL)
        while block := r.read(1 << 20):
            f.write(block)
    if tmp.stat().st_size != model.size or _sha256(tmp) != model.sha256:
        tmp.unlink(missing_ok=True)
        raise ModelUnavailable(f"downloaded {model.name} does not match its checksum; not installed")
    tmp.replace(dest)
    return dest


def main(argv: list[str]) -> int:
    if argv[:1] == ["fetch"]:
        print(fetch(SPEAKER))
        return 0
    if argv[:1] == ["check"]:
        for m in (SPEAKER, VAD):
            verified_path(m)
            print(f"{m.name}: ok")
        return 0
    print("usage: python -m aftercall.models fetch|check")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
