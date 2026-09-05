"""The activity card, and the one thing about it that is not decoration: which
state it claims to be in, and that the transcription count is real rather than a
spinner pretending to be progress.

The motion itself is judged by eye (scratchpad mock). What is tested here is the
state machine and the numbers, because those are what would lie to the user.
"""

import tkinter as tk

import pytest

from hemsa import palette as P
from hemsa.ui import activity


@pytest.fixture(scope="session")
def root(tk_root):
    """The session-wide interpreter (tests/conftest.py). Nothing is
    destroyed here: a fresh tk.Tk() after a destroy fails on Windows."""
    return tk_root


@pytest.fixture()
def card(root):
    c = activity.ActivityCard(root)
    yield c
    c.destroy()


def test_mmss_never_shows_a_negative_or_a_stray_float():
    assert activity._mmss(0) == "00:00"
    assert activity._mmss(65.9) == "01:05"
    assert activity._mmss(-4) == "00:00"          # a clock that moved backwards
    assert activity._mmss(3600) == "60:00"        # minutes keep counting past an hour


def test_recording_shows_the_clock_and_scrolls_the_history(card):
    card.set("recording", level=0.5, elapsed=75)
    assert card._label.cget("text") == "Recording"
    assert card._clock.cget("text") == "01:15"
    newest = card._levels[-1]
    card.set("recording", level=0.0, elapsed=76)
    # the loud sample moved one place left rather than being overwritten
    assert card._levels[-2] == newest
    assert card._levels[-1] == 0.0


def test_recording_level_is_clamped(card):
    card.set("recording", level=99.0)
    assert card._levels[-1] == 1.0
    card.set("recording", level=-5.0)
    assert card._levels[-1] == 0.0


def test_transcribing_reports_the_real_chunk_count(card):
    card.set("transcribing", done=3, total=12)
    assert card._label.cget("text") == "Transcribing"
    assert card._clock.cget("text") == "3 of 12"


def test_transcribing_shows_no_count_before_the_chunks_are_planned(card):
    """A "0 of 0" would read as a stalled job. Blank until the number is real."""
    card.set("transcribing", done=0, total=0)
    assert card._clock.cget("text") == ""


def test_summarising_has_no_progress_bar(card):
    """Two model calls with nothing measurable in between: the motion must not
    look like a bar filling, or it promises a completion time it cannot know."""
    card.set("summarising", elapsed=3)
    assert not card._track.winfo_ismapped()
    assert card._label.cget("text").startswith("Writing the summary")


def test_transcribing_keeps_the_recording_shape_and_greys_it(card):
    """The card is showing the audio being READ BACK, so the shape carries over
    from the recording rather than resetting to a flat line."""
    card.set("recording", level=1.0)
    shape = list(card._levels)
    card.set("transcribing", done=1, total=4)
    assert card._levels == shape


def test_transcribing_falls_back_to_a_static_trace_with_nothing_to_freeze(card):
    """Page opened mid-job, or Hemsa restarted: there is no recording shape to
    keep, and a flat line reads as "stopped" - the impression this card exists
    to remove."""
    card.set("transcribing", done=1, total=4)
    assert max(card._levels) > 0


def test_a_new_recording_starts_from_an_empty_waveform(card):
    card.set("transcribing", done=1, total=4)
    card.set("recording", level=0.0)
    assert max(card._levels) == 0


def test_restyle_survives_a_theme_switch(card):
    card.set("recording", level=0.4)
    P.set_theme("navy")
    try:
        card.restyle()
        assert card._label.cget("bg") == P.CARD
    finally:
        P.set_theme(P.DEFAULT)
