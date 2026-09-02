"""Long-audio transcription. The engine FAILS above 400 s (fixed position table),
so audio is tiled into chunks <= 90 s, each cut at the quietest 200 ms window in the
last third of the span - energy-based on purpose: no VAD model, no new download.
90 s also bounds how long a dictation can be stuck behind a meeting chunk (~2.5 s).
"""

import logging
import wave
from pathlib import Path

import numpy as np

from . import fastclean
from .engine import SAMPLE_RATE

log = logging.getLogger("hemsa.longform")

MAX_CHUNK_S = 90
HARD_LIMIT_S = 400          # engine's real ceiling; assert-guarded, never approached
SEARCH_FROM_S = 60          # cut somewhere in [60, 90] s
QUIET_WIN_S = 0.2
SILENCE_RMS = 0.0015        # same threshold as dictation's silence_rms default


class WavReader:
    """Streams a WAV file in slices instead of loading it whole. Peak RAM stays
    one chunk (90 s ~= 5.8 MB) plus the scoring span, not the whole recording
    (~230 MB per hour per channel at float32)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._f = wave.open(str(self.path), "rb")
        rate, channels = self._f.getframerate(), self._f.getnchannels()
        if rate != SAMPLE_RATE or channels != 1:
            self._f.close()
            raise ValueError(f"{self.path.name}: expected 16 kHz mono, "
                              f"got {rate} Hz {channels} ch")
        self.n_samples = self._f.getnframes()

    def slice(self, a: int, b: int) -> np.ndarray:
        a, b = max(0, a), min(b, self.n_samples)
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        self._f.setpos(a)
        pcm = np.frombuffer(self._f.readframes(b - a), dtype=np.int16)
        return (pcm / 32767).astype(np.float32)

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def plan_chunks(n_samples, rate, audio_getter):
    """Tile [0, n_samples) into ranges <= MAX_CHUNK_S, cutting at quiet points.
    audio_getter(a, b) returns samples for scoring only (keeps RAM bounded)."""
    max_len, search_from = MAX_CHUNK_S * rate, SEARCH_FROM_S * rate
    win = int(QUIET_WIN_S * rate)
    chunks, start = [], 0
    while n_samples - start > max_len:
        span = audio_getter(start + search_from, start + max_len)
        # RMS of each 200 ms window; cut at the quietest one
        n_win = max(1, len(span) // win)
        windows = span[:n_win * win].reshape(n_win, win)
        quiet = int(np.argmin(np.sqrt(np.mean(windows ** 2, axis=1))))
        cut = start + search_from + quiet * win + win // 2
        chunks.append((start, cut))
        start = cut
    chunks.append((start, n_samples))
    assert all((b - a) <= HARD_LIMIT_S * rate for a, b in chunks)
    return chunks


def transcribe_wav(path, channel, engine, words, wait_idle):
    """WAV -> [{"start", "end", "channel", "text"}], engine-safe chunking.
    Reads the WAV per chunk (WavReader), never loads it whole."""
    from . import dictionary
    out = []
    with WavReader(path) as reader:
        for a, b in plan_chunks(reader.n_samples, SAMPLE_RATE, reader.slice):
            clip = reader.slice(a, b)
            if len(clip) == 0:
                continue
            if float(np.sqrt(np.mean(clip ** 2))) < SILENCE_RMS:
                continue
            wait_idle()                  # dictation always wins between chunks
            text = engine.transcribe(clip)
            if not text:
                continue
            text, _ = dictionary.apply(text, words)
            text = fastclean.clean(text)
            out.append({"start": a / SAMPLE_RATE, "end": b / SAMPLE_RATE,
                        "channel": channel, "text": text})
    return out


def merge(*segment_lists):
    return sorted((s for lst in segment_lists for s in lst),
                  key=lambda s: s["start"])
