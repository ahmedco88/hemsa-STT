"""Hold-to-talk hotkey, single key or a chord (e.g. Ctrl+Win). The key is OBSERVED,
never swallowed (the Windows rule: a swallowed key-down whose key-up escapes
leaves the target app thinking the modifier is stuck down forever).

Hold-delay debounce applies to SINGLE-KEY triggers only. A 2+ key chord (Ctrl+Win)
is deliberate by construction - nobody holds that exact pair by accident - so it
fires instantly, no delay.

Solo Ctrl (added 2026-08-28 by request; it had been dropped from CHOICES for this
reason) needs a SECOND guard on top of the delay, because Ctrl is held down during
every Ctrl+C / Ctrl+V / Ctrl+anything. The delay alone only covers a fast shortcut;
a slow one still armed the HUD. So for a single-key trigger, any OTHER key going
down cancels the pending arm until the trigger is released: Ctrl held alone is a
dictation trigger, Ctrl held with something else is a shortcut. What neither guard
can see is a modifier held for the MOUSE - Ctrl+scroll to zoom, Ctrl+click to
multi-select - because a keyboard hook gets no mouse events. Hold either for more
than HOLD_DELAY and dictation starts. Ctrl+Win remains the recommended default.

Chord detection is watched through a single keyboard.hook rather than per-key
bindings, because add_hotkey/on_press_key can't do press-and-hold for a combo.

THE STALE-STATE RACE (found 2026-08-23, cost weeks of "slight delay" complaints):
inside a WH_KEYBOARD_LL hook, GetAsyncKeyState has NOT been updated yet for the
key that triggered the event. Polling it on win-down said "win still up", so the
chord only fired on the NEXT keyboard event - the autorepeat ~0.5-1 s later on a
physical keyboard (injected input, which never autorepeats, measured 1.51 s: it
waited for the release). Fix: the hook events themselves are the authority - keep
an event-fed down-set, and use GetAsyncKeyState only as a fallback for keys that
were already held before we hooked, plus a pruner that heals a missed release
event (e.g. keys let go on the UAC secure desktop, which hooks never see).
"""

import ctypes
import logging
import threading
import time

import keyboard

log = logging.getLogger("hemsa.hotkey")

# perf_counter of the most recent press-fire, read by controller/__main__ to log
# the press -> gate -> HUD latency chain. 0.0 until the first press.
LAST_PRESS = 0.0

CHOICES = ["ctrl+win", "ctrl", "right shift", "caps lock", "f13"]

HOLD_DELAY = 0.5  # seconds; single-key triggers only, see module docstring

_VK = {
    "ctrl": 0x11, "left ctrl": 0xA2, "right ctrl": 0xA3,
    "win": 0x5B, "windows": 0x5B, "left win": 0x5B, "right win": 0x5C,
    "shift": 0x10, "left shift": 0xA0, "right shift": 0xA1,
    "alt": 0x12, "caps lock": 0x14, "f13": 0x7C,
}


def _physical_down(key_name: str):
    vk = _VK.get(key_name.lower())
    if vk is None:
        return None
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _key_down(key_name: str) -> bool:
    physical = _physical_down(key_name)
    if physical is not None:
        return physical
    try:
        return keyboard.is_pressed(key_name)
    except Exception:
        return False


class Hotkey:
    def __init__(self, on_press, on_release):
        self._on_press = on_press
        self._on_release = on_release
        self._parts: list[str] = []
        self._down: set[str] = set()   # parts seen down via hook events (authoritative)
        self._hook = None
        self._timer: threading.Timer | None = None
        self._armed = False   # True once HOLD_DELAY elapsed and on_press fired
        self._shortcut = False  # single-key trigger is being used as a modifier

    def bind(self, key_name: str) -> bool:
        self.unbind()
        self._parts = [p.strip() for p in key_name.split("+") if p.strip()]
        if not self._parts:
            return False
        try:
            self._hook = keyboard.hook(self._check)
            return True
        except Exception as exc:
            log.error("could not bind %r: %s", key_name, exc)
            return False

    def unbind(self) -> None:
        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except Exception:
                pass
            self._hook = None
        self._cancel_timer()
        self._armed = False
        self._shortcut = False
        self._down.clear()

    def _event_part(self, name) -> str | None:
        """Which configured part (if any) this event's key name belongs to."""
        name = (name or "").lower()
        for p in self._parts:
            base = p.lower()
            if name == base:
                return p
            if base in ("win", "windows") and "windows" in name:
                return p
            if base in ("ctrl", "shift", "alt") and name.endswith(" " + base):
                return p   # 'left ctrl' / 'right shift' style names
        return None

    def _all_down(self, ignore: str | None = None) -> bool:
        # event-fed set first; async state only as fallback (it lags the very
        # event being handled - see module docstring)
        return bool(self._parts) and all(
            p != ignore and (p in self._down or _key_down(p)) for p in self._parts)

    def _check(self, event=None) -> None:
        ev_part = None
        ev_up = False
        if event is not None:
            ev_part = self._event_part(getattr(event, "name", None))
            if ev_part is not None:
                ev_up = getattr(event, "event_type", None) != "down"
                if ev_up:
                    self._down.discard(ev_part)
                else:
                    self._down.add(ev_part)
            elif (len(self._parts) == 1 and not self._armed
                  and getattr(event, "event_type", None) == "down"):
                # Another key went down. For a single-key trigger that means the
                # trigger is being held AS A MODIFIER (Ctrl+C), not as a hold to
                # talk. Suppress until it is released - the hold delay alone only
                # catches shortcuts pressed quickly.
                self._shortcut = True
                self._cancel_timer()
        # heal ghosts from missed release events; never prune the current
        # event's own key - its async state is exactly the stale one
        for p in list(self._down):
            if p != ev_part and _physical_down(p) is False:
                self._down.discard(p)
        down = self._all_down(ignore=ev_part if ev_up else None)
        if down and not self._armed and self._timer is None and not self._shortcut:
            if len(self._parts) >= 2:     # a chord - deliberate by construction, no delay
                self._fire_press()
            else:
                self._timer = threading.Timer(HOLD_DELAY, self._fire_press)
                self._timer.daemon = True
                self._timer.start()
        elif not down:
            self._cancel_timer()
            self._shortcut = False        # trigger released: the shortcut is over
            if self._armed:
                self._armed = False
                try:
                    self._on_release()
                except Exception:
                    log.exception("release handler")

    def _fire_press(self) -> None:
        global LAST_PRESS
        self._timer = None
        # _shortcut is re-checked HERE, not just at cancel time: Timer.cancel()
        # cannot stop a callback that has already started, so a Ctrl+C landing in
        # that window would otherwise arm anyway.
        if self._all_down() and not self._shortcut:   # a real hold, not a tap
            self._armed = True
            LAST_PRESS = time.perf_counter()
            try:
                self._on_press()
            except Exception:
                log.exception("press handler")

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
