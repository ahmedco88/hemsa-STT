"""The DPI scale factor, and the one ordering rule that cannot be seen at runtime.

Without DPI awareness Windows renders the window at 96 DPI and bitmap-stretches
it to the user's scale, so every glyph and card edge goes soft. The fix has two
halves and BOTH are easy to break silently: awareness must be claimed before
tk.Tk() reads the screen (after it, the call succeeds and changes nothing
visible), and px() must be read at USE time, never baked into a module constant
at import when K is still 1.0.
"""

import tkinter as tk

import pytest

from hemsa.ui import scale


@pytest.fixture(autouse=True)
def reset_k():
    before = scale.K
    yield
    scale.K = before


def test_px_is_identity_until_init():
    assert scale.K == 1.0
    assert scale.px(40) == 40


def test_px_scales_and_rounds_to_whole_pixels():
    scale.K = 1.25
    assert scale.px(40) == 50
    assert scale.px(7) == 9              # 8.75, and a coordinate must be an int
    assert isinstance(scale.px(7), int)


def test_init_clamps_an_absurd_screen():
    class _Screen:
        def __init__(self, dpi):
            self._dpi = dpi

        def winfo_fpixels(self, _spec):
            return self._dpi

    assert scale.init(_Screen(120.0)) == pytest.approx(1.25)
    assert scale.init(_Screen(48.0)) == 1.0          # never shrink the UI
    assert scale.init(_Screen(9600.0)) == 3.0        # never blow it off the desktop


def test_init_survives_a_screen_it_cannot_measure():
    class _Broken:
        def winfo_fpixels(self, _spec):
            raise tk.TclError("no display")

    assert scale.init(_Broken()) == 1.0


def test_awareness_is_claimed_before_the_root_exists():
    """Ordering, and the reason this test exists at all: SetProcessDpiAwareness
    after tk.Tk() returns success and does nothing, so nothing at runtime fails
    loudly enough to notice. Only the source order proves it."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "hemsa" / "__main__.py").read_text(
        encoding="utf-8")
    aware = src.index("scale_mod.set_dpi_aware()")
    root = src.index("root = tk.Tk()")
    assert aware < root, "set_dpi_aware() must run before tk.Tk()"
    assert src.index("scale_mod.init(root)") > root, "init() needs the root"
