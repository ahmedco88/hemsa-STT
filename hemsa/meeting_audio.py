"""Meeting capture: mic ("me") + WASAPI loopback of the default speakers ("them"),
both written incrementally to 16 kHz mono WAV - an hour of audio must never live in
RAM. Loopback may deliver NOTHING while the system is silent (spike, 2026-09-02), so
each writer pads with zeros against a shared wall clock; sample count is never a
clock. Uses PyAudioWPatch ONLY here - dictation's sounddevice path is untouched.
"""

import logging
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .engine import SAMPLE_RATE

log = logging.getLogger("hemsa.meeting_audio")


def downmix_resample(data: np.ndarray, src_rate: int) -> np.ndarray:
    """Any-channel float32 at src_rate -> mono float32 at SAMPLE_RATE."""
    mono = data.mean(axis=1) if data.ndim == 2 else data
    if src_rate == SAMPLE_RATE:
        return mono.astype(np.float32)
    n = int(len(mono) * SAMPLE_RATE / src_rate)
    return np.interp(np.linspace(0, len(mono), n, endpoint=False),
                     np.arange(len(mono)), mono).astype(np.float32)


class PaddedWavWriter:
    """Appends chunks by wall-clock END time, zero-padding gaps."""

    def __init__(self, path: Path, rate: int = SAMPLE_RATE):
        self._rate = rate
        self._written = 0                       # frames on disk
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(rate)

    def append(self, t_wall: float, samples: np.ndarray) -> None:
        expected_start = int((t_wall * self._rate)) - len(samples)
        gap = expected_start - self._written
        if gap > 0:
            self._wav.writeframes(np.zeros(gap, dtype=np.int16).tobytes())
            self._written += gap
        pcm = np.clip(samples, -1.0, 1.0)
        self._wav.writeframes((pcm * 32767).astype(np.int16).tobytes())
        self._written += len(samples)

    def close(self, t_end: float | None = None) -> float:
        if t_end is not None:
            target = int(t_end * self._rate)
            pad = target - self._written
            if pad > 0:
                self._wav.writeframes(np.zeros(pad, dtype=np.int16).tobytes())
                self._written += pad
        self._wav.close()
        return self._written / self._rate


class MeetingRecorder:
    """Two streams for ONE recording; opened at start(), closed at stop().
    Errors stop the recording but keep everything already written."""

    def __init__(self, cfg: dict, dest: Path):
        self._cfg = cfg
        self._dest = dest
        self._pa = None
        self._mic = self._loop = None
        self._me_w = self._them_w = None
        self._t0 = 0.0
        self._lock = threading.Lock()
        # Wall-clock second at which a channel's stream aborted, if it did. A dead
        # channel must NOT be padded out to the stop time: an abort 100 s into an
        # hour-long call would otherwise write ~3500 s of zeros (~112 MB) on the
        # main thread inside stop_recording().
        self._me_end: float | None = None
        self._them_end: float | None = None
        self.level = 0.0
        self.error: str | None = None

    def start(self) -> None:
        import pyaudiowpatch as pyaudio       # imported lazily: dictation never pays
        # t0 is set FIRST, before anything that can fail, so a teardown triggered by
        # any setup exception below pads by a small correct elapsed time - not by
        # perf_counter()'s arbitrary large reference point (self._t0 still at its
        # __init__ default of 0.0).
        self._t0 = time.perf_counter()
        try:
            self._dest.mkdir(parents=True, exist_ok=True)
            self._me_w = PaddedWavWriter(self._dest / "me.wav")
            self._them_w = PaddedWavWriter(self._dest / "them.wav")
            self._pa = pyaudio.PyAudio()
            loop_info = self._find_loopback(pyaudio)
            self._loop_rate = int(loop_info["defaultSampleRate"])
            self._loop_ch = max(1, loop_info["maxInputChannels"])
            self._loop_fmt = pyaudio.paFloat32
            try:
                self._loop = self._pa.open(
                    format=pyaudio.paFloat32, channels=self._loop_ch, rate=self._loop_rate,
                    frames_per_buffer=2048, input=True,
                    input_device_index=loop_info["index"],
                    stream_callback=self._loop_cb)
            except OSError:
                self._loop_fmt = pyaudio.paInt16
                self._loop = self._pa.open(
                    format=pyaudio.paInt16, channels=self._loop_ch, rate=self._loop_rate,
                    frames_per_buffer=2048, input=True,
                    input_device_index=loop_info["index"],
                    stream_callback=self._loop_cb)
            mic_index = self._find_mic(pyaudio)
            self._mic_rate = 16000
            self._mic_fmt = pyaudio.paFloat32
            try:
                self._mic = self._pa.open(format=pyaudio.paFloat32, channels=1,
                                          rate=16000, frames_per_buffer=1024, input=True,
                                          input_device_index=mic_index,
                                          stream_callback=self._mic_cb)
            except OSError:
                self._mic_rate = 48000            # same fallback audio.Recorder uses
                try:
                    self._mic = self._pa.open(format=pyaudio.paFloat32, channels=1,
                                              rate=48000, frames_per_buffer=4096, input=True,
                                              input_device_index=mic_index,
                                              stream_callback=self._mic_cb)
                except OSError:
                    self._mic_fmt = pyaudio.paInt16
                    self._mic = self._pa.open(format=pyaudio.paInt16, channels=1,
                                              rate=48000, frames_per_buffer=4096, input=True,
                                              input_device_index=mic_index,
                                              stream_callback=self._mic_cb)
        except Exception as exc:
            # Setup failed partway - some of {writers, PyAudio, one stream} may
            # already exist and be running. Tear down exactly like stop() does so
            # nothing leaks and whatever was already captured stays on disk with a
            # valid WAV header, then surface the failure to the caller.
            self.error = str(exc)
            self._teardown()
            raise

    def _find_loopback(self, pyaudio):
        api = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self._pa.get_device_info_by_index(api["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for lb in self._pa.get_loopback_device_info_generator():
            if speakers["name"] in lb["name"]:
                return lb
        raise RuntimeError("no loopback twin for the default speakers")

    def _find_mic(self, pyaudio):
        want = self._cfg.get("mic_device")
        if want:
            for i in range(self._pa.get_device_count()):
                d = self._pa.get_device_info_by_index(i)
                if d["maxInputChannels"] > 0 and want in d["name"] \
                        and not d.get("isLoopbackDevice"):
                    return i
        return None                            # PortAudio default input

    def _to_float32(self, in_data: bytes, fmt) -> np.ndarray:
        import pyaudiowpatch as pyaudio
        if fmt == pyaudio.paInt16:
            return np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        return np.frombuffer(in_data, dtype=np.float32)

    def _mic_cb(self, in_data, n, t, status):
        import pyaudiowpatch as pyaudio
        try:
            raw = self._to_float32(in_data, self._mic_fmt)
            mono = downmix_resample(raw, self._mic_rate)
            self.level = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
            with self._lock:
                if self._me_w:
                    self._me_w.append(time.perf_counter() - self._t0, mono)
        except Exception as exc:
            log.exception("mic callback failed")
            self.error = f"mic stream failed: {exc}"
            self._me_end = time.perf_counter() - self._t0
            return (None, pyaudio.paAbort)
        return (None, pyaudio.paContinue)

    def _loop_cb(self, in_data, n, t, status):
        import pyaudiowpatch as pyaudio
        try:
            data = self._to_float32(in_data, self._loop_fmt)
            if self._loop_ch > 1:
                data = data.reshape(-1, self._loop_ch)
            mono = downmix_resample(data, self._loop_rate)
            with self._lock:
                if self._them_w:
                    self._them_w.append(time.perf_counter() - self._t0, mono)
        except Exception as exc:
            log.exception("loopback callback failed")
            self.error = f"loopback stream failed: {exc}"
            self._them_end = time.perf_counter() - self._t0
            return (None, pyaudio.paAbort)
        return (None, pyaudio.paContinue)

    def _teardown(self) -> float:
        """Detach + close both writers (valid WAV headers either way) and release
        whatever device resources exist. Used by stop() AND by a failed start() -
        a partial setup can already have writers, a PyAudio instance, and/or one
        running stream, all of which must go regardless of how far start() got.
        Safe to call twice: every resource is detached to None BEFORE it is acted
        on (same shape the writers already use), so a second call finds nothing
        left to close/terminate and returns 0.0."""
        with self._lock:
            me_w, self._me_w = self._me_w, None
            them_w, self._them_w = self._them_w, None
            t_end = time.perf_counter() - self._t0
        mic, self._mic = self._mic, None
        loop, self._loop = self._loop, None
        pa, self._pa = self._pa, None
        for s in (mic, loop):
            try:
                if s is not None:
                    s.stop_stream(); s.close()
            except OSError as exc:
                log.warning("stream close: %s", exc)
        if pa is not None:
            try:
                pa.terminate()
            except OSError as exc:
                log.warning("PyAudio terminate: %s", exc)
        secs = 0.0
        # A channel whose stream aborted stops at the abort, not at t_end: padding
        # a dead channel to the stop time is minutes of zeros nobody asked for.
        if me_w:
            secs = me_w.close(self._me_end if self._me_end is not None else t_end)
        if them_w:
            them_end = self._them_end if self._them_end is not None else t_end
            secs = max(secs, them_w.close(them_end))
        self.level = 0.0
        return secs

    def stop(self) -> float:
        return self._teardown()
