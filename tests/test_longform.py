import wave
import numpy as np
import pytest

from hemsa import longform


def make_wav(path, spans, rate=16000):
    """spans: list of (seconds, amplitude). Writes mono int16."""
    audio = np.concatenate([np.full(int(s * rate), a, dtype=np.float32)
                            for s, a in spans])
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
        f.writeframes((audio * 32767).astype(np.int16).tobytes())
    return audio


def test_plan_chunks_short_audio_is_one_chunk():
    audio = np.zeros(16000 * 30, dtype=np.float32)
    chunks = longform.plan_chunks(len(audio), 16000, lambda a, b: audio[a:b])
    assert chunks == [(0, len(audio))]


def test_plan_chunks_cuts_at_quiet_point_and_respects_max():
    rate = 16000
    # 200 s: loud until 70 s, near-silence 70-72 s, loud to the end
    audio = np.concatenate([
        np.full(70 * rate, 0.5, dtype=np.float32),
        np.zeros(2 * rate, dtype=np.float32),
        np.full(128 * rate, 0.5, dtype=np.float32)])
    chunks = longform.plan_chunks(len(audio), rate, lambda a, b: audio[a:b])
    for a, b in chunks:
        assert (b - a) <= longform.MAX_CHUNK_S * rate
    # first cut lands inside the silent window
    assert 69 * rate <= chunks[0][1] <= 73 * rate
    # chunks tile the audio exactly
    assert chunks[0][0] == 0 and chunks[-1][1] == len(audio)
    assert all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1))


def test_transcribe_wav_offsets_channels_and_corrections(tmp_path, monkeypatch):
    path = tmp_path / "me.wav"
    make_wav(path, [(30, 0.4), (2, 0.0), (30, 0.4)])

    class FakeEngine:
        busy = False
        def transcribe(self, audio):
            return "hello wonthaggi"

    import hemsa.dictionary as dictionary
    monkeypatch.setattr(dictionary, "apply",
                        lambda text, words: (text.replace("wonthaggi", "Wonthaggi"), []))
    segs = longform.transcribe_wav(path, "me", FakeEngine(), words=[],
                                   wait_idle=lambda: None)
    assert all(s["channel"] == "me" for s in segs)
    assert segs[0]["start"] == 0.0
    assert all("Wonthaggi" in s["text"] for s in segs)
    assert [round(s["start"], 1) for s in segs] == sorted(
        round(s["start"], 1) for s in segs)


def test_transcribe_wav_skips_silent_chunks(tmp_path):
    path = tmp_path / "them.wav"
    make_wav(path, [(20, 0.0)])

    class FakeEngine:
        busy = False
        def transcribe(self, audio):
            raise AssertionError("silent chunk must not reach the engine")

    segs = longform.transcribe_wav(path, "them", FakeEngine(), words=[],
                                   wait_idle=lambda: None)
    assert segs == []


def test_transcribe_wav_rejects_wrong_format(tmp_path):
    path = tmp_path / "bad.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000)
        f.writeframes(np.zeros(48000, dtype=np.int16).tobytes())

    class FakeEngine:
        busy = False
        def transcribe(self, audio):
            raise AssertionError("wrong-format wav must not reach the engine")

    with pytest.raises(ValueError, match="16 kHz mono"):
        longform.transcribe_wav(path, "me", FakeEngine(), words=[],
                                wait_idle=lambda: None)


def test_transcribe_wav_multi_segment_offsets_stay_bounded(tmp_path):
    path = tmp_path / "me.wav"
    rate = 16000
    make_wav(path, [(200, 0.4)], rate=rate)

    lengths = []
    starts = []

    class FakeEngine:
        busy = False
        def transcribe(self, audio):
            lengths.append(len(audio))
            return "hello"

    segs = longform.transcribe_wav(path, "me", FakeEngine(), words=[],
                                   wait_idle=lambda: None)
    starts = [s["start"] for s in segs]
    assert len(lengths) > 1
    assert all(n <= longform.MAX_CHUNK_S * rate for n in lengths)
    assert all(starts[i] < starts[i + 1] for i in range(len(starts) - 1))


def test_merge_interleaves_by_start():
    a = [{"start": 0.0, "end": 5.0, "channel": "me", "text": "one"},
         {"start": 9.0, "end": 12.0, "channel": "me", "text": "three"}]
    b = [{"start": 4.0, "end": 8.0, "channel": "them", "text": "two"}]
    assert [s["text"] for s in longform.merge(a, b)] == ["one", "two", "three"]
