"""Mic capture. The InputStream is opened ONCE and kept running for the app's whole
life; start()/stop() just gate whether the callback keeps what it hears. Opening a
fresh device on every keypress cost ~150-250 ms of WASAPI negotiation per dictation -
that was the delay after Ctrl+Win. Discord/Teams-style always-on capture removes it;
the tradeoff is Windows shows the mic-in-use indicator continuously while Hemsa runs,
same as any push-to-talk app.
"""

import logging
import threading

import numpy as np
import sounddevice as sd

from .engine import SAMPLE_RATE

log = logging.getLogger("hemsa.audio")


def device_names() -> list[str]:
    seen = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and d["name"] not in seen:
            seen.append(d["name"])
    return seen


def _resolve(cfg: dict):
    want = cfg.get("mic_device")
    if not want:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and want in d["name"]:
            return i
    log.warning("configured mic %r not found, using default", want)
    return None


class Recorder:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._rate = SAMPLE_RATE
        self._recording = False
        self._lock = threading.Lock()
        self.level = 0.0            # rolling RMS of the newest chunk, read by the UI
        self._open()

    def _callback(self, indata, _frames, _time, status):
        if status:
            log.warning("audio status: %s", status)
        if not self._recording:
            return
        mono = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(mono)
        self.level = float(np.sqrt(np.mean(mono**2)))

    def _open(self) -> None:
        """Opens and starts the persistent stream. Safe to call again after reopen()."""
        device = _resolve(self._cfg)
        # WASAPI shared mode usually resamples to 16 kHz; some devices refuse, so
        # fall back to 48 kHz capture + downsample in stop().
        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                          dtype="float32", device=device,
                                          callback=self._callback)
            self._rate = SAMPLE_RATE
        except sd.PortAudioError:
            self._stream = sd.InputStream(samplerate=48000, channels=1,
                                          dtype="float32", device=device,
                                          callback=self._callback)
            self._rate = 48000
        self._stream.start()

    def reopen(self) -> None:
        """Call after the configured mic device changes in Settings."""
        was_recording = self._recording
        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._open()
        self._recording = was_recording

    def close(self) -> None:
        """App shutdown only."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        self._recording = True

    def stop(self) -> np.ndarray:
        """Returns the whole utterance as 16 kHz mono float32 (may be empty).
        The stream itself keeps running - only the gate flips off."""
        self._recording = False
        self.level = 0.0
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks)
        if self._rate != SAMPLE_RATE:
            n = int(len(audio) * SAMPLE_RATE / self._rate)
            audio = np.interp(np.linspace(0, len(audio), n, endpoint=False),
                              np.arange(len(audio)), audio)
        return audio.astype(np.float32)


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0
