import numpy as np
import pytest

from hemsa import longform, whisper_engine


def test_missing_model_is_an_error_not_a_fallback(tmp_path):
    cfg = {"models_dir": str(tmp_path / "parakeet-v2")}
    assert whisper_engine.available(cfg) is False
    with pytest.raises(whisper_engine.WhisperUnavailable) as exc:
        whisper_engine.WhisperEngine(cfg)
    assert whisper_engine.MODEL_FILE in str(exc.value)


def test_model_sits_beside_the_parakeet_folder(tmp_path):
    cfg = {"models_dir": str(tmp_path / "parakeet-v2")}
    assert whisper_engine.model_path(cfg) == (
        tmp_path / whisper_engine.MODEL_DIR / whisper_engine.MODEL_FILE)


def test_whisper_chunks_stay_inside_its_window():
    rate = 16000
    audio = np.random.default_rng(0).normal(0, 0.05, rate * 200).astype(np.float32)
    chunks = longform.plan_chunks(len(audio), rate, lambda a, b: audio[a:b],
                                  whisper_engine.CHUNK_S)
    assert all(b - a <= whisper_engine.CHUNK_S * rate for a, b in chunks)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(audio)
    assert all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1))
    # default behaviour (Parakeet) unchanged: 90 s
    default = longform.plan_chunks(len(audio), rate, lambda a, b: audio[a:b])
    assert max(b - a for a, b in default) <= 90 * rate
