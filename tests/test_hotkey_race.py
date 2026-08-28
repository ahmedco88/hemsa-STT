"""The WH_KEYBOARD_LL stale-state race (found 2026-08-23).

Inside a low-level keyboard hook, GetAsyncKeyState has NOT yet been updated for
the key that triggered the event. The old chord detection polled async state on
every event, so on win-down it saw "win still up", missed the chord, and only
fired on the NEXT keyboard event - the autorepeat ~0.5-1 s later on a physical
keyboard, or the key RELEASE for injected input (measured: 1.51 s press-to-gate).

These tests drive Hotkey._check with fake events while _physical_down always
returns the state as it was BEFORE the current event (the worst-case stale hook
view). Detection must rely on the events themselves.
"""

import hemsa.hotkey as hotkey_mod
from hemsa.hotkey import Hotkey


class FakeEvent:
    def __init__(self, name, event_type):
        self.name = name
        self.event_type = event_type


class Rig:
    """Async key state that always lags one event behind, like the real hook."""

    def __init__(self, monkeypatch, parts):
        self.state = {p: False for p in parts}
        monkeypatch.setattr(hotkey_mod, "_physical_down",
                            lambda name: self.state.get(self._canon(name), False))
        self.presses = 0
        self.releases = 0
        self.hk = Hotkey(self._on_press, self._on_release)
        self.hk._parts = list(parts)

    @staticmethod
    def _canon(name):
        n = name.lower()
        if "win" in n:
            return "win"
        if "ctrl" in n:
            return "ctrl"
        return n

    def _on_press(self):
        self.presses += 1

    def _on_release(self):
        self.releases += 1

    def event(self, name, event_type):
        """Deliver the hook event FIRST (stale async), update async AFTER."""
        self.hk._check(FakeEvent(name, event_type))
        self.state[self._canon(name)] = event_type == "down"


def test_chord_fires_on_the_completing_keydown_despite_stale_async(monkeypatch):
    rig = Rig(monkeypatch, ["ctrl", "win"])
    rig.event("ctrl", "down")
    assert rig.presses == 0
    rig.event("left windows", "down")      # async still says win is up here
    assert rig.presses == 1, "chord must fire on the win-down event itself"


def test_release_fires_on_the_first_keyup_despite_stale_async(monkeypatch):
    rig = Rig(monkeypatch, ["ctrl", "win"])
    rig.event("ctrl", "down")
    rig.event("left windows", "down")
    assert rig.presses == 1
    rig.event("left windows", "up")        # async still says win is down here
    assert rig.releases == 1, "release must fire on the first up event"


def test_unrelated_keys_do_not_fire(monkeypatch):
    rig = Rig(monkeypatch, ["ctrl", "win"])
    rig.event("ctrl", "down")
    rig.event("j", "down")
    rig.event("j", "up")
    rig.event("ctrl", "up")
    assert rig.presses == 0 and rig.releases == 0


def test_missed_release_event_heals_on_next_key_activity(monkeypatch):
    # e.g. keys released on the UAC secure desktop, where hooks see nothing
    rig = Rig(monkeypatch, ["ctrl", "win"])
    rig.event("ctrl", "down")
    rig.state["ctrl"] = False              # released, but no up event delivered
    rig.event("j", "down")                 # any later activity prunes the ghost
    rig.event("left windows", "down")      # win alone must NOT complete the chord
    assert rig.presses == 0


# ---- solo Ctrl (2026-08-28) ---------------------------------------------
# Ctrl is held during every Ctrl+anything shortcut, so a single-key trigger needs
# the hold delay AND a "something else went down" cancel. These drive the timer
# directly rather than sleeping through HOLD_DELAY.

def _pending(rig):
    """The armed-in-waiting timer, or None."""
    return rig.hk._timer


def test_solo_ctrl_starts_a_pending_arm(monkeypatch):
    rig = Rig(monkeypatch, ["ctrl"])
    rig.event("ctrl", "down")
    assert rig.presses == 0                 # not instant - a single key waits
    assert _pending(rig) is not None


def test_solo_ctrl_fires_after_the_hold_delay(monkeypatch):
    rig = Rig(monkeypatch, ["ctrl"])
    rig.event("ctrl", "down")
    _pending(rig).cancel()
    rig.hk._fire_press()                    # what the timer would have called
    assert rig.presses == 1
    rig.event("ctrl", "up")
    assert rig.releases == 1


def test_ctrl_c_never_arms(monkeypatch):
    """The reason solo Ctrl was excluded in the first place."""
    rig = Rig(monkeypatch, ["ctrl"])
    rig.event("ctrl", "down")
    rig.event("c", "down")                  # a shortcut, not a hold to talk
    assert _pending(rig) is None
    rig.hk._fire_press()                    # even if the timer had already fired
    assert rig.presses == 0
    rig.event("c", "up")
    rig.event("ctrl", "up")
    assert rig.presses == 0 and rig.releases == 0


def test_ctrl_works_again_after_a_shortcut(monkeypatch):
    """The suppression must clear on release, or one Ctrl+C kills the hotkey."""
    rig = Rig(monkeypatch, ["ctrl"])
    rig.event("ctrl", "down")
    rig.event("c", "down")
    rig.event("c", "up")
    rig.event("ctrl", "up")
    rig.event("ctrl", "down")
    assert _pending(rig) is not None
    rig.hk._fire_press()
    assert rig.presses == 1


def test_a_chord_is_not_cancelled_by_other_keys(monkeypatch):
    """The guard is single-key only: a chord fires instantly and stays fired."""
    rig = Rig(monkeypatch, ["ctrl", "win"])
    rig.event("ctrl", "down")
    rig.event("win", "down")
    assert rig.presses == 1
    rig.event("x", "down")
    assert rig.hk._armed and rig.releases == 0
