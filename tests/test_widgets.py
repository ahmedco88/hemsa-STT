"""Canvas widgets: colours come from palette slots at event time, kinds and
disabled states resolve to the right fills, the ring clamps, the toggle flips."""

import tkinter as tk

import pytest

from hemsa import palette as P
from hemsa.ui import widgets as W


@pytest.fixture(scope="session")
def root(tk_root):
    """The session-wide interpreter (tests/conftest.py). Nothing is
    destroyed here: a fresh tk.Tk() after a destroy fails on Windows."""
    return tk_root


@pytest.fixture(scope="module")
def top(root):
    """A MAPPED window: Tk drops synthetic crossing and button events on an
    unmapped widget, so the event tests need one that is really on screen."""
    t = tk.Toplevel(root)
    t.geometry("200x120+0+0")
    t.update()
    yield t
    t.destroy()


@pytest.fixture(autouse=True)
def restore_theme():
    yield
    P.set_theme(P.DEFAULT)


def test_mix_endpoints_and_clamp():
    assert W.mix(P.CARD, P.INK, 0) == P.CARD
    assert W.mix(P.CARD, P.INK, 1) == P.INK
    assert W.mix(P.INK, P.CARD, 7) == P.CARD
    mid = W.mix(P.INK, P.CARD, 0.5)
    assert mid not in (P.INK, P.CARD) and len(mid) == 7


def test_hover_reads_the_slot_at_event_time(top):
    f = tk.Frame(top, width=50, height=50)
    f.pack()
    top.update()
    W.hover([f], rest="CARD", lit="MIST", steps=1, ms=0)
    before = P.MIST
    P.set_theme("navy")
    f.event_generate("<Enter>")
    top.update()
    assert f.cget("bg").upper() == P.MIST.upper() != before.upper()


def test_hover_respects_the_lock(top):
    f = tk.Frame(top, bg=P.CARD, width=50, height=50)
    f.pack()
    top.update()
    W.hover([f], rest="CARD", lit="MIST", steps=1, ms=0)
    f._hover_locked = True
    f.event_generate("<Enter>")
    top.update()
    assert f.cget("bg").upper() == P.CARD.upper()


def test_pill_kinds_and_disabled(root):
    b = W.PillButton(root, "Save", kind="primary")
    assert b.fill() == P.INK
    b.set_kind("stop")
    assert b.fill() == P.DANGER
    b.set_enabled(False)
    assert b.fill() == P.PAPER
    b.set_enabled(True)
    assert b.fill() == P.DANGER
    b.configure_text("Stop recording")          # resizes without error
    assert int(b.cget("width")) > 0


def test_ring_clamps(root):
    r = W.Ring(root)
    r.set(2.0, animate=False)
    assert r.fraction == 1.0
    r.set(-1, animate=False)
    assert r.fraction == 0.0


def test_toggle_flips_variable_and_calls(top):
    v = tk.BooleanVar(top, value=False)
    hits = []
    t = W.Toggle(top, v, command=lambda: hits.append(1))
    t.pack()
    top.update()
    t.event_generate("<Button-1>", x=5, y=5)
    top.update()
    assert v.get() is True and hits == [1]
    t.destroy()
    v.set(False)                                 # trace removed on destroy: no error


def test_card_and_dots_build(root):
    c = W.RoundCard(root)
    tk.Label(c.body, text="x").pack()
    d = W.DayDots(c.body, [True, False, True])
    d.set([False] * 3)
    c.restyle()
    assert c.body.winfo_exists()


def test_star_toggles_on_click_and_reports_the_new_state(top):
    """The click is bound on the canvas, so it needs a really-mapped window.
    Off/on is the whole contract: everything else about the star is paint."""
    seen = []
    star = W.Star(top, on=False, command=seen.append)
    star.pack()
    top.update()
    star.event_generate("<ButtonRelease-1>", x=2, y=2)
    assert star.on is True and seen == [True]
    star.event_generate("<ButtonRelease-1>", x=2, y=2)
    assert star.on is False and seen == [True, False]
    star.destroy()


def test_star_ignores_a_release_outside_its_box(top):
    """A press that wanders off the star before the release is a cancelled click,
    the same rule PillButton uses - otherwise a drag across a row stars it."""
    seen = []
    star = W.Star(top, on=False, command=seen.append)
    star.pack()
    top.update()
    star.event_generate("<ButtonRelease-1>", x=star.size + 40, y=2)
    assert star.on is False and seen == []
    star.destroy()
