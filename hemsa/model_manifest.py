"""The speech model's file list: names, exact sizes, SHA256s.

Deliberately dependency-free (stdlib only). config.py needs this to answer "is
the model installed?", and config must not drag in `requests` to do it - that
made a missing HTTP library turn a simple file check into a crash.
download.py adds the network layer on top.

Checksums: the three .onnx values are Hugging Face's own LFS oids, confirmed
byte-for-byte against a known-good local copy (2026-08-23). tokens.txt is a plain
git blob so HF publishes only its size; that hash comes from the same verified
copy and is cross-checked against HF's stated size.
"""

from dataclasses import dataclass

MODEL_NAME = "Parakeet TDT 0.6B v2"
MODEL_DETAIL = "English, int8, runs on the CPU"
MODEL_LICENCE = "CC-BY-4.0"
MODEL_REPO = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str


FILES = (
    ModelFile("encoder.int8.onnx", 652184296,
              "a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab"),
    ModelFile("decoder.int8.onnx", 7257753,
              "b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e"),
    ModelFile("joiner.int8.onnx", 1739080,
              "7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2"),
    ModelFile("tokens.txt", 9384,
              "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"),
)

TOTAL_BYTES = sum(f.size for f in FILES)


def missing(dest) -> list[ModelFile]:
    """Files absent or the wrong size. A wrong size means a truncated download,
    which must count as missing: four correctly-named stubs used to read as a
    working model and then failed deep inside sherpa-onnx."""
    out = []
    for f in FILES:
        p = dest / f.name
        try:
            if not p.exists() or p.stat().st_size != f.size:
                out.append(f)
        except OSError:                 # unreadable path: treat as not usable
            out.append(f)
    return out
