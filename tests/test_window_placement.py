"""Screen-geometry guards for the floating windows.

The orb going missing was never a drawing bug: its saved position is only valid
for the desktop it was saved on, and a resolution/DPI change or an unplugged
monitor moves the desktop out from under it. on_screen() is the check that
catches that, so it gets a test with hand-made bounds.
"""

import pytest

from hemsa import winutil


@pytest.fixture
def desktop(monkeypatch):
    """A single 1920x1080 desktop at the origin."""
    monkeypatch.setattr(winutil, "virtual_bounds", lambda: (0, 0, 1920, 1080))


SIZE = 56


def test_a_normal_position_is_on_screen(desktop):
    assert winutil.on_screen(1856, 1016, SIZE, SIZE)


def test_a_position_from_a_bigger_monitor_is_not(desktop):
    """The exact failure: saved at the bottom-right of a 2752x1152 desktop, then
    reopened on 1920x1080."""
    assert not winutil.on_screen(2688, 1040, SIZE, SIZE)


def test_just_off_the_edge_counts_as_gone(desktop):
    assert not winutil.on_screen(1910, 500, SIZE, SIZE)      # 10 px of 56 showing
    assert winutil.on_screen(1890, 500, SIZE, SIZE)          # 30 px showing, grabbable


def test_negative_coordinates_from_a_removed_left_monitor(desktop):
    assert not winutil.on_screen(-1800, 300, SIZE, SIZE)


def test_snap_to_edge_always_lands_inside_the_work_area(monkeypatch):
    monkeypatch.setattr(winutil, "work_area", lambda: (0, 0, 1920, 1040))
    monkeypatch.setattr(winutil, "virtual_bounds", lambda: (0, 0, 1920, 1080))
    for x, y in [(9999, 9999), (-4000, -4000), (2688, 1040), (0, 0)]:
        nx, ny = winutil.snap_to_edge(x, y, SIZE, SIZE)
        assert winutil.on_screen(nx, ny, SIZE, SIZE, need=SIZE)
