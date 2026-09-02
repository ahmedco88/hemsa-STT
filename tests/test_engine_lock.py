"""Engine.transcribe must serialize concurrent callers and expose busy."""
import threading
import time
import numpy as np
import pytest


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    import hemsa.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    from hemsa.engine import Engine
    eng = Engine.__new__(Engine)          # skip __init__: no model on CI
    eng._recognizer = None
    eng._error = None
    eng._ready = threading.Event()
    eng._ready.set()
    eng._lock = threading.Lock()
    eng._busy = 0
    return eng


def test_transcribe_serializes_and_reports_busy(engine, monkeypatch):
    order = []

    class FakeStream:
        result = type("R", (), {"text": "ok"})()
        def accept_waveform(self, rate, audio): pass

    class FakeRecognizer:
        def create_stream(self): return FakeStream()
        def decode_stream(self, s):
            order.append(("in", engine.busy))
            time.sleep(0.05)
            order.append(("out", None))

    engine._recognizer = FakeRecognizer()
    threads = [threading.Thread(target=engine.transcribe,
                                args=(np.zeros(16000, dtype=np.float32),))
               for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    # never two "in" without an "out" between them, and busy was True inside
    entries = [e[0] for e in order]
    assert entries == ["in", "out", "in", "out"]
    assert all(busy for kind, busy in order if kind == "in")
    assert engine.busy is False
