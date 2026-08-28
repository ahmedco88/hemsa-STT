"""Soft start/stop ticks, generated (no asset files).

Played through ONE persistent OutputStream fed by a small worker thread - the same
always-open trick as audio.py's input side. sd.play() opened a fresh WASAPI stream
per tick, which measured ~57 ms from call to audible on Ahmed's machine (plus more
after the output device had idled), so the "listening" cue lagged the keypress even
though the mic gate was already open. Writing into a pre-opened stream takes ~1 ms.
"""

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from .engine import SAMPLE_RATE

log = logging.getLogger("hemsa.sounds")


def _tick(freq: float, dur: float = 0.07, vol: float = 0.12) -> np.ndarray:
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    tone = np.sin(2 * np.pi * freq * t) * np.exp(-t * 40)     # fast decay = soft tick
    return (tone * vol).astype(np.float32).reshape(-1, 1)


_START = _tick(880)
_STOP = _tick(587)

_q: "queue.Queue[tuple[np.ndarray, float]]" = queue.Queue()
_started = False
_lock = threading.Lock()


def _open() -> sd.OutputStream:
    stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    stream.start()
    return stream


def _run() -> None:
    stream = None
    try:
        stream = _open()          # pre-open so the first tick is instant too
    except Exception:
        log.exception("could not pre-open tick stream")
    while True:
        clip, t_req = _q.get()
        try:
            if stream is None:
                stream = _open()
            stream.write(clip)
            log.info("tick out %.0f ms after request",
                     (time.perf_counter() - t_req) * 1000)
        except Exception:
            log.exception("tick playback failed, will reopen stream")
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
            stream = None


def warm_up() -> None:
    """Start the worker (and its stream) ahead of the first dictation."""
    global _started
    with _lock:
        if not _started:
            threading.Thread(target=_run, daemon=True, name="sounds").start()
            _started = True


def play_start(cfg: dict) -> None:
    if cfg.get("sounds"):
        warm_up()
        _q.put((_START, time.perf_counter()))


def play_stop(cfg: dict) -> None:
    if cfg.get("sounds"):
        warm_up()
        _q.put((_STOP, time.perf_counter()))
