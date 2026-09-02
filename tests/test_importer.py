import wave
from pathlib import Path

import numpy as np
import pytest

from hemsa.importer import ImportUnsupported, to_wav


@pytest.fixture()
def m4a(tmp_path) -> Path:
    """2 s 440 Hz tone written as .m4a by PyAV itself - synthetic, licence-free."""
    import av
    path = tmp_path / "tone.m4a"
    out = av.open(str(path), "w")
    stream = out.add_stream("aac", rate=44100, layout="mono")
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(88200) / 44100))
    frame = av.AudioFrame.from_ndarray(
        tone.astype(np.float32).reshape(1, -1), format="fltp", layout="mono")
    frame.sample_rate = 44100
    for packet in stream.encode(frame):
        out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()
    return path


@pytest.fixture()
def stereo_wav(tmp_path) -> Path:
    """2 s 440 Hz tone written as 44.1 kHz STEREO .wav via stdlib wave - exercises
    the resampler's channel downmix, which the mono m4a fixture does not."""
    path = tmp_path / "tone_stereo.wav"
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(88200) / 44100))
    pcm = (tone * 32767).astype(np.int16)
    stereo = np.repeat(pcm.reshape(-1, 1), 2, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(stereo.tobytes())
    return path


def test_m4a_decodes_to_16k_mono_wav(tmp_path, m4a):
    dest = tmp_path / "import.wav"
    seconds = to_wav(m4a, dest)
    # AAC adds encoder priming/padding delay on top of the source 2.0 s, so this
    # window is looser than the stereo wav test below - 1.9-2.1 s still catches
    # a dropped tail (the resampler-flush bug) while tolerating AAC's own delay.
    assert 1.9 <= seconds <= 2.1
    with wave.open(str(dest)) as f:
        assert f.getframerate() == 16000 and f.getnchannels() == 1
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    assert np.abs(audio).max() > 1000          # the tone survived


def test_garbage_raises_readable_error(tmp_path):
    src = tmp_path / "notaudio.m4a"
    src.write_bytes(b"definitely not media")
    with pytest.raises(ImportUnsupported):
        to_wav(src, tmp_path / "out.wav")


def test_stereo_44k_wav_downmixes_to_16k_mono(tmp_path, stereo_wav):
    # This fixture is exactly 2.000 s of samples (88200 / 44100), with no codec
    # priming delay (raw PCM via stdlib wave) - so both the returned duration and
    # the written frame count can be pinned exactly, unlike the AAC case above.
    # Measured on this build (av 18.1.0 / bundled ffmpeg) with the resampler
    # flush in place: exactly 32000 frames (88200 * 16000 / 44100 = 32000.0
    # exactly), no off-by-one. Without the flush the tail was short by ~16
    # frames (measured 31984) - this is the assertion that catches that.
    dest = tmp_path / "import.wav"
    seconds = to_wav(stereo_wav, dest)
    assert abs(seconds - 2.0) < 0.001
    with wave.open(str(dest)) as f:
        assert f.getframerate() == 16000 and f.getnchannels() == 1
        n_frames = f.getnframes()
        audio = np.frombuffer(f.readframes(n_frames), dtype=np.int16)
    assert n_frames == 32000
    assert np.abs(audio).max() > 1000
