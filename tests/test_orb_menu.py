"""The orb's right-click menu, and the focus rule that makes "Paste it again" work.

The orb is a no-activate window precisely so it never takes the caret away from
what you are dictating into. A popup menu DOES take the foreground, so the target
window has to be captured before the menu opens - reading it from inside a menu
command gets the menu's own owner and the paste lands nowhere. That is the single
thing most worth pinning down here, because it fails silently: the paste "works",
into the wrong window, and injector.paste puts the old clipboard back 0.6 s later
so the text is simply gone.
"""

import pytest

from hemsa import controller as controller_mod


class FakeRecorder:
    def __init__(self, cfg):
        pass


@pytest.fixture
def ctl(monkeypatch):
    monkeypatch.setattr(controller_mod.audio, "Recorder", FakeRecorder)
    monkeypatch.setattr(controller_mod.sounds, "warm_up", lambda: None)
    c = controller_mod.Controller({"sounds": False, "hotkey": "ctrl+win"}, None, lambda fn: fn())
    c.on_paste_risk = lambda: risks.append(1)
    return c


risks: list = []


@pytest.fixture(autouse=True)
def _clear_risks():
    risks.clear()


# ---- copy ----------------------------------------------------------------

def test_copy_last_puts_the_transcript_on_the_clipboard(ctl, monkeypatch):
    copied = []
    monkeypatch.setattr(controller_mod.pyperclip, "copy", copied.append)
    ctl.last_text = "the patient reports the cough has settled"
    assert ctl.copy_last() is True
    assert copied == ["the patient reports the cough has settled"]


def test_copy_last_with_nothing_dictated_yet(ctl, monkeypatch):
    copied = []
    monkeypatch.setattr(controller_mod.pyperclip, "copy", copied.append)
    assert ctl.copy_last() is False
    assert copied == []


# ---- paste ---------------------------------------------------------------

def test_paste_last_targets_the_window_it_was_given(ctl, monkeypatch):
    """NOT the foreground at paste time - that is the menu by then."""
    focused, pasted = [], []
    monkeypatch.setattr(controller_mod.winutil, "focus_window",
                        lambda h: focused.append(h) or True)
    monkeypatch.setattr(controller_mod.winutil, "has_caret", lambda h: True)
    monkeypatch.setattr(controller_mod.winutil, "foreground_window",
                        lambda: pytest.fail("must not read the foreground here"))
    monkeypatch.setattr(controller_mod.injector, "paste", pasted.append)
    ctl.last_text = "hello"
    assert ctl.paste_last(4242) is True
    assert focused == [4242]
    assert pasted == ["hello"]
    assert risks == []


def test_paste_last_offers_the_rescue_chip_when_there_is_no_caret(ctl, monkeypatch):
    """A menu click is mouse-first, so there is often nowhere for the text to go."""
    monkeypatch.setattr(controller_mod.winutil, "focus_window", lambda h: True)
    monkeypatch.setattr(controller_mod.winutil, "has_caret", lambda h: False)
    monkeypatch.setattr(controller_mod.injector, "paste", lambda t: None)
    ctl.last_text = "hello"
    ctl.paste_last(4242)
    assert risks == [1]


def test_paste_last_offers_the_rescue_chip_when_the_window_is_gone(ctl, monkeypatch):
    monkeypatch.setattr(controller_mod.winutil, "focus_window", lambda h: False)
    monkeypatch.setattr(controller_mod.winutil, "has_caret",
                        lambda h: pytest.fail("not asked when focus failed"))
    monkeypatch.setattr(controller_mod.injector, "paste", lambda t: None)
    ctl.last_text = "hello"
    ctl.paste_last(0)
    assert risks == [1]


def test_paste_last_with_nothing_dictated_yet(ctl, monkeypatch):
    monkeypatch.setattr(controller_mod.injector, "paste",
                        lambda t: pytest.fail("nothing to paste"))
    assert ctl.paste_last(4242) is False


# ---- preview -------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("short one", '"short one"'),
    ("  collapses\n  whitespace  ", '"collapses whitespace"'),
    ("the patient reports that the cough has now settled completely",
     '"the patient reports that the cough has…"'),
])
def test_preview_line(text, expected):
    from hemsa.ui.orb_menu import _preview
    assert _preview(text) == expected
