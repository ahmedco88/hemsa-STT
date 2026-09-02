import time
import wave
import numpy as np
import pytest

from hemsa.meeting_audio import MeetingRecorder, PaddedWavWriter, downmix_resample

# PyAudioWPatch is Windows-only (WASAPI). Skipping at collection keeps the rest
# of the suite runnable on a machine or CI box that cannot install it, instead
# of failing every test in the run with an ImportError.
pyaudio = pytest.importorskip("pyaudiowpatch")


def test_downmix_resample_stereo_48k_to_mono_16k():
    stereo = np.zeros((4800, 2), dtype=np.float32)   # 0.1 s @ 48 kHz
    stereo[:, 0] = 0.5
    out = downmix_resample(stereo, 48000)
    assert out.ndim == 1 and out.dtype == np.float32
    assert len(out) == 1600                          # 0.1 s @ 16 kHz
    assert abs(float(out.mean()) - 0.25) < 0.01      # (0.5 + 0) / 2


def test_padded_writer_inserts_silence_for_wall_clock_gaps(tmp_path):
    path = tmp_path / "them.wav"
    w = PaddedWavWriter(path)
    chunk = np.full(1600, 0.5, dtype=np.float32)     # 0.1 s of signal
    w.append(0.1, chunk)                             # arrives at t=0.1s (end time)
    w.append(1.1, chunk)                             # 0.9 s gap -> silence inserted
    seconds = w.close()
    assert abs(seconds - 1.1) < 0.02
    with wave.open(str(path)) as f:
        assert f.getframerate() == 16000 and f.getnchannels() == 1
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    assert abs(len(audio) / 16000 - 1.1) < 0.02
    mid = audio[int(0.5 * 16000):int(0.9 * 16000)]   # inside the gap
    assert np.all(mid == 0)


def test_padded_writer_never_pads_negative(tmp_path):
    w = PaddedWavWriter(tmp_path / "me.wav")
    chunk = np.zeros(1600, dtype=np.float32)
    w.append(0.1, chunk)
    w.append(0.15, chunk)      # overlapping wall clock: append, don't pad or crash
    assert w.close() >= 0.2 - 0.02


def test_padded_writer_close_pads_to_final_wall_clock(tmp_path):
    path = tmp_path / "them.wav"
    w = PaddedWavWriter(path)
    chunk = np.full(1600, 0.5, dtype=np.float32)     # 0.1 s of signal at t=0.1
    w.append(0.1, chunk)
    seconds = w.close(t_end=2.0)                     # no further chunks ever arrive
    assert abs(seconds - 2.0) < 0.02
    with wave.open(str(path)) as f:
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    assert abs(len(audio) / 16000 - 2.0) < 0.02
    tail = audio[int(1.0 * 16000):]                  # well past the last chunk
    assert np.all(tail == 0)


def test_mic_callback_exception_sets_error_and_aborts_stream(tmp_path):
    rec = MeetingRecorder(cfg={}, dest=tmp_path)
    rec._t0 = time.perf_counter()
    rec._mic_fmt = pyaudio.paFloat32
    rec._me_w = PaddedWavWriter(tmp_path / "me.wav")
    rec._them_w = PaddedWavWriter(tmp_path / "them.wav")

    # 7 bytes is not a multiple of 4 (float32 itemsize) - np.frombuffer raises.
    result = rec._mic_cb(b"\x00" * 7, 0, None, 0)

    assert rec.error is not None
    assert result[1] == pyaudio.paAbort

    rec.stop()   # everything already written must still come out as a valid WAV
    with wave.open(str(tmp_path / "me.wav")):
        pass
    with wave.open(str(tmp_path / "them.wav")):
        pass

    rec.stop()   # a second stop() (double-cleanup) must not raise


def test_start_tears_down_and_reraises_on_setup_failure(tmp_path, monkeypatch):
    class FakePA:
        def __init__(self):
            self.terminate_calls = 0

        def terminate(self):
            self.terminate_calls += 1

    fake_pa = FakePA()
    monkeypatch.setattr(pyaudio, "PyAudio", lambda: fake_pa)

    def _raise(self, pa_module):
        raise RuntimeError("no loopback")

    monkeypatch.setattr(MeetingRecorder, "_find_loopback", _raise)

    rec = MeetingRecorder(cfg={}, dest=tmp_path)
    with pytest.raises(RuntimeError):
        rec.start()

    assert fake_pa.terminate_calls == 1
    assert rec.error is not None and "no loopback" in rec.error
    with wave.open(str(tmp_path / "me.wav")):
        pass
    with wave.open(str(tmp_path / "them.wav")):
        pass

    rec.stop()   # a caller's cleanup stop() after a failed start() must not raise,
    assert fake_pa.terminate_calls == 1   # and must not terminate a second time


def test_an_aborted_channel_is_not_padded_to_the_stop_time(tmp_path):
    """After an abort at t=1 s, stop() at t=100 s must not write 99 s of zeros
    into the dead channel - in a real hour-long call that is ~112 MB of silence,
    written synchronously on the main thread."""
    rec = MeetingRecorder(cfg={}, dest=tmp_path)
    rec._t0 = time.perf_counter()
    rec._mic_fmt = pyaudio.paFloat32
    rec._me_w = PaddedWavWriter(tmp_path / "me.wav")
    rec._them_w = PaddedWavWriter(tmp_path / "them.wav")
    rec._me_w.append(1.0, np.full(16000, 0.4, dtype=np.float32))   # 1 s captured

    rec._mic_cb(b"\x00" * 7, 0, None, 0)      # not a multiple of float32: aborts
    rec._me_end = 1.0                          # pretend the abort landed at t=1 s
    rec._t0 = time.perf_counter() - 100.0      # ...and stop() happens 100 s later
    rec.stop()

    with wave.open(str(tmp_path / "me.wav")) as f:
        assert abs(f.getnframes() / 16000 - 1.0) < 0.05      # kept, not padded
