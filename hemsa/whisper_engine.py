"""Optional Whisper large-v3-turbo (whisper.cpp via pywhispercpp) for MEETINGS only.

Dictation stays on Parakeet: on this CPU Whisper took 16-20 s per short clip, far too
slow for push-to-talk (see learnings.md, 2026-09-29). A meeting is a background job
nobody is waiting on, so the slower, more accurate engine is affordable there.

The 574 MB model is NOT bundled and NOT downloaded by Hemsa. It is looked up beside the
Parakeet folder and a missing file is an ERROR, never a silent fall back to Parakeet:
the user picked Whisper, and a transcript quietly made by a different engine (English
only, no Arabic) is the kind of substitution nothing on screen would reveal.
"""

import logging
import time

import numpy as np

from . import config

log = logging.getLogger("hemsa.whisper")

MODEL_DIR = "whisper-large-v3-turbo"
MODEL_FILE = "ggml-large-v3-turbo-q5_0.bin"
CHUNK_S = 30            # Whisper's own window; also keeps dictation waits short

LANGUAGES = (("en", "English"), ("ar", "Arabic"), ("auto", "Auto-detect"))


class WhisperUnavailable(Exception):
    """Model file or runtime missing - the message is shown to the user."""


def model_path(cfg: dict):
    return config.models_dir(cfg).parent / MODEL_DIR / MODEL_FILE


def available(cfg: dict) -> bool:
    return model_path(cfg).is_file()


class WhisperEngine:
    """Same transcribe(audio) contract as engine.Engine. Load it for one meeting and
    drop it afterwards: it holds ~1.5 GB while alive."""

    def __init__(self, cfg: dict):
        path = model_path(cfg)
        if not path.is_file():
            raise WhisperUnavailable(
                f"Whisper model not found. Expected {path}. Choose Parakeet in "
                "Settings, or put the ggml-large-v3-turbo-q5_0.bin file there.")
        try:
            from pywhispercpp.model import Model
        except Exception as exc:
            raise WhisperUnavailable(f"Whisper runtime missing ({exc})") from exc
        self.language = cfg.get("meeting_language", "en")
        t0 = time.perf_counter()
        self._model = Model(str(path), n_threads=4, print_realtime=False,
                            print_progress=False)
        log.info("whisper loaded in %.1f s", time.perf_counter() - t0)

    def transcribe(self, audio: np.ndarray) -> str:
        """16 kHz mono float32 -> text."""
        segs = self._model.transcribe(audio, language=self.language,
                                      translate=False, no_context=True)
        return " ".join(s.text.strip() for s in segs).strip()
