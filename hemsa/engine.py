"""Parakeet TDT 0.6B v2 int8 via sherpa-onnx. Config values verified in
the sherpa-onnx Parakeet notes against a running transcription.

~2 GB RAM resident once loaded; ~40x real time on CPU. Audio over 400 s FAILS
(fixed encoder position table) - dictation never gets near that, but never feed
this a long recording without splitting.
"""

import logging
import threading
import time

import numpy as np
import sherpa_onnx

from . import config

log = logging.getLogger("hemsa.engine")

SAMPLE_RATE = 16000


class Engine:
    """Loads once in a background thread; transcribe() blocks until ready."""

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._recognizer = None
        self._error: str | None = None
        self._ready = threading.Event()
        threading.Thread(target=self._load, daemon=True, name="engine-load").start()

    def _load(self) -> None:
        d = config.models_dir(self._cfg)
        try:
            t0 = time.perf_counter()
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"),
                joiner=str(d / "joiner.int8.onnx"),
                tokens=str(d / "tokens.txt"),
                model_type="nemo_transducer",  # mandatory, will not load without it
                feature_dim=128,               # NOT the default 80
                num_threads=4,                 # measured sweet spot, 8 was slower
                sample_rate=SAMPLE_RATE,
                decoding_method="greedy_search",
            )
            log.info("model loaded in %.1f s", time.perf_counter() - t0)
        except Exception as exc:
            self._error = str(exc)
            log.error("model load failed: %s", exc)
        finally:
            self._ready.set()

    @property
    def state(self) -> str:
        if not self._ready.is_set():
            return "loading"
        return "error" if self._error else "loaded"

    @property
    def error(self) -> str | None:
        return self._error

    def transcribe(self, audio: np.ndarray) -> str:
        """16 kHz mono float32 in [-1, 1] -> text. Raises RuntimeError if load failed."""
        self._ready.wait()
        if self._recognizer is None:
            raise RuntimeError(self._error or "engine not loaded")
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()
