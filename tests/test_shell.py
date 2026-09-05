"""Shell contract: pages build lazily, the window hides without dying, and an
unknown page name is a programming error, not a silent no-op."""

import tkinter as tk

import pytest

from hemsa.ui import shell as shell_mod


@pytest.fixture(scope="session")
def root(tk_root):
    """The session-wide interpreter (tests/conftest.py). Nothing is
    destroyed here: a fresh tk.Tk() after a destroy fails on Windows."""
    return tk_root


class Page(tk.Frame):
    shown = 0
    hidden = 0

    def on_show(self):
        Page.shown += 1

    def on_hide(self):
        Page.hidden += 1


def test_pages_build_lazily_and_the_window_survives_hide(root):
    built = []
    s = shell_mod.Shell(root, app=None, pages={
        "home": lambda parent: (built.append("home"), Page(parent))[1],
        "words": lambda parent: (built.append("words"), Page(parent))[1]})
    assert built == []
    s.show("words")
    assert built == ["words"] and s.current == "words" and s.visible
    s.hide()
    assert not s.visible and s.win.winfo_exists() and Page.hidden == 1
    s.show("words")
    assert built == ["words"] and Page.shown == 2
    s.show("home")
    assert built == ["words", "home"] and Page.hidden == 2
    s.set_recording(True)
    s.set_recording(False)
    s.restyle()
    s.win.destroy()


def test_unknown_page_is_a_programming_error(root):
    s = shell_mod.Shell(root, app=None, pages={"home": Page})
    with pytest.raises(KeyError):
        s.show("nope")
    s.win.destroy()
