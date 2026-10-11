"""Live pill: the draft words while recording, and the target app's icon."""

import numpy as np
import pytest

from hemsa import audio, controller
from hemsa.ui import hud as hud_mod


# ---- tail_lines: the newest words must always be the ones on screen ----
def test_short_text_is_one_line():
    assert hud_mod.tail_lines("hello there", len, 40) == ["hello there"]


def test_long_text_keeps_the_tail_with_an_ellipsis():
    words = " ".join(f"w{i}" for i in range(40))
    lines = hud_mod.tail_lines(words, len, 20, n=2)
    assert len(lines) == 2
    assert lines[-1].endswith("w39")
    assert lines[0].startswith("… ")
    assert all(len(s) <= 20 for s in lines)


def test_a_single_overlong_word_still_shows():
    assert hud_mod.tail_lines("x" * 50, len, 10) == ["x" * 50]


# ---- Recorder.snapshot ----
def _bare_recorder(rate=audio.SAMPLE_RATE):
    import threading
    r = audio.Recorder.__new__(audio.Recorder)
    r._lock = threading.Lock()
    r._rate = rate
    r._recording = True
    r.level = 0.0
    r._chunks = [np.ones(160, dtype=np.float32), np.zeros(160, dtype=np.float32)]
    return r


@pytest.mark.parametrize("rate", [audio.SAMPLE_RATE, 48000])
def test_snapshot_matches_stop_and_keeps_recording(rate):
    r = _bare_recorder(rate)
    snap = r.snapshot()
    assert r._recording and len(r._chunks) == 2      # the gate stayed open
    assert np.array_equal(snap, r.stop())


# ---- the preview loop ----
class _Engine:
    state = "loaded"

    def __init__(self, on_decode=None):
        self.calls = 0
        self.on_decode = on_decode

    def transcribe(self, clip):
        self.calls += 1
        if self.on_decode:
            self.on_decode()
        return "hemsa test"


class _Rec:
    level = 0.0

    def __init__(self, cfg):
        pass

    def snapshot(self):
        return np.full(1600, 0.1, dtype=np.float32)


@pytest.fixture()
def ctl(monkeypatch):
    monkeypatch.setattr(controller.audio, "Recorder", _Rec)
    monkeypatch.setattr(controller.dictionary, "load", lambda: [])
    monkeypatch.setattr(controller, "PREVIEW_MIN_S", 0.01)
    posted = []
    c = controller.Controller({"sounds": False}, _Engine(), posted.append)
    c.posted = posted
    return c


def test_preview_delivers_the_draft(ctl):
    got = []
    ctl.on_preview = got.append
    ctl.post = lambda fn: fn()       # the app pump drains each post as it arrives
    gen = ctl._preview_gen
    # run two passes, then simulate release
    orig = ctl.engine.transcribe

    def counted(clip):
        out = orig(clip)
        if ctl.engine.calls >= 2:
            ctl._preview_gen += 1        # release lands after this pass
        return out
    ctl.engine.transcribe = counted
    ctl._preview_loop(gen)
    # the first pass was delivered; the second finished after release and dropped
    assert got == ["hemsa test"]


def test_long_clip_decodes_only_the_window_and_marks_the_cut(ctl, monkeypatch):
    seen, got = [], []
    ctl.on_preview = got.append
    ctl.post = lambda fn: fn()
    monkeypatch.setattr(ctl._recorder, "snapshot",
                        lambda: np.full(controller.PREVIEW_WINDOW * 2, 0.1, np.float32))
    gen = ctl._preview_gen

    def once(clip):
        seen.append(len(clip))
        ctl._preview_gen = gen if len(seen) < 2 else gen + 1
        return "rtial word then more"
    ctl.engine.transcribe = once
    ctl._preview_loop(gen)
    assert seen[0] == controller.PREVIEW_WINDOW
    assert got[0] == "… word then more"


def test_a_pass_finishing_after_release_is_dropped(ctl):
    got = []
    ctl.on_preview = got.append
    gen = ctl._preview_gen
    ctl.engine.on_decode = lambda: setattr(ctl, "_preview_gen", gen + 1)
    ctl._preview_loop(gen)
    for fn in ctl.posted:
        fn()
    assert got == []


def test_a_delivery_queued_before_release_is_dropped_on_the_main_thread(ctl):
    got = []
    ctl.on_preview = got.append
    ctl._deliver_preview(ctl._preview_gen - 1, "stale")
    ctl._deliver_preview(ctl._preview_gen, "fresh")
    assert got == ["fresh"]


def test_preview_failure_is_contained(ctl):
    def boom(clip):
        raise RuntimeError("decoder fell over")
    ctl.engine.transcribe = boom
    ctl._preview_loop(ctl._preview_gen)      # must return, not raise


# ---- the pill itself ----
@pytest.fixture()
def hud(clean_root):
    h = hud_mod.Hud(clean_root, _Rec(None))
    yield h
    h.win.destroy()


def test_pill_grows_with_text_and_stops_at_the_cap(hud):
    from hemsa.ui.scale import px
    hud.set_text("")
    one_row = hud.h
    hud.set_text("short words")
    assert hud.h > one_row
    hud.set_text(" ".join(["dictation"] * 200))
    assert hud.w <= px(hud_mod.MAX_W) + 1
    assert len(hud._lines) == hud_mod.LINES


def test_target_icon_comes_from_the_exe(hud, monkeypatch):
    monkeypatch.setattr(hud_mod.winutil, "window_exe", lambda h: r"C:\Windows\explorer.exe")
    hud.set_target(1234)
    assert hud._icon is not None


def test_unknown_target_falls_back_to_a_letter(hud, monkeypatch):
    monkeypatch.setattr(hud_mod.winutil, "window_exe", lambda h: "")
    monkeypatch.setattr(hud_mod.winutil, "window_title", lambda h: "notes - Editor")
    hud.set_target(1234)
    assert hud._icon is None and hud._letter == "N"


def test_hud_draws_every_state_without_error(hud):
    for state in ("recording", "processing"):
        hud.set_state(state)
        for text in ("", "some live words", " ".join(["dictation"] * 200)):
            hud.set_text(text)
            hud._animate()
            x1, y1, x2, y2 = hud.canvas.bbox("all")
            # nothing drawn outside the window, or it is clipped mid-glyph
            assert x1 >= 0 and y1 >= 0 and x2 <= hud.w + 1 and y2 <= hud.h + 1, text
    hud.set_state("idle")
    assert hud._lines == []
