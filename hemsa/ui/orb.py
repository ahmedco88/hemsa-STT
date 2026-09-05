"""The floating orb - Ahmed's mouse-first trigger. Click toggles dictation (no hold),
drag moves it (a small movement threshold separates the two), release snaps it to
the nearest screen edge. Semi-transparent until hovered. Never steals focus.

A watchdog re-checks the orb on a timer rather than only at startup: Hemsa
autostarts and then runs for days, and over that time the OS can hide the window
outright or the desktop can change shape underneath it. See _ensure_visible for
the two failure modes - together they are the "the orb disappeared and I had to
hide and show it again" bug.
"""

import logging
import tkinter as tk

from .. import palette as P
from .. import winutil
from .scale import px

SIZE = 56                # logical; self.size is the px() one
DRAG_THRESHOLD = 6
ALPHA_IDLE = 0.82
ALPHA_HOVER = 1.0
WATCHDOG_MS = 3000       # how often the orb is re-checked against the desktop


class Orb:
    def __init__(self, root: tk.Tk, cfg: dict, on_click, menu=None):
        self.cfg = cfg
        self._on_click = on_click
        self._menu = menu               # OrbMenu, or None in tests/selftest
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=P.TRANSPARENT_KEY)
        self.win.wm_attributes("-transparentcolor", P.TRANSPARENT_KEY)
        self.win.attributes("-alpha", ALPHA_IDLE)
        self.size = px(SIZE)
        self.canvas = tk.Canvas(self.win, width=self.size, height=self.size,
                                bg=P.TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack()
        self._state = "idle"
        self._tick = 0
        self._drag = {"x": 0, "y": 0, "moved": False}

        self.canvas.bind("<ButtonPress-3>", self._context)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Enter>", lambda e: self.win.attributes("-alpha", ALPHA_HOVER))
        self.canvas.bind("<Leave>", lambda e: self.win.attributes("-alpha", ALPHA_IDLE))

        pos = cfg.get("orb_pos")
        if pos:
            x, y = winutil.snap_to_edge(pos[0], pos[1], self.size, self.size)
        else:
            left, _t, right, bottom = winutil.work_area()
            x, y = right - self.size - px(8), bottom - self.size - px(120)
        self.win.geometry(f"{self.size}x{self.size}+{x}+{y}")
        winutil.set_noactivate(self.win)
        self._draw()
        self._animating = False
        self._dragging = False
        if not cfg.get("show_orb", True):
            self.win.withdraw()
        self.win.after(WATCHDOG_MS, self._ensure_visible)

    # ---- visibility ----
    def show(self, visible: bool) -> None:
        if visible:
            self.win.deiconify()
            winutil.set_noactivate(self.win)
            self._draw()
        else:
            self.win.withdraw()

    # ---- state from controller ----
    def set_state(self, state: str) -> None:
        self._state = state
        if state == "processing" and not self._animating:
            self._animating = True
            self._animate()
        self._draw()

    # ---- input ----
    def _context(self, e) -> None:
        if self._menu is not None:
            self._menu.popup(e.x_root, e.y_root)

    def _press(self, e) -> None:
        # insurance: the NOACTIVATE exstyle can silently reset (Windows re-styles
        # on some geometry/DPI events), and a click on an activatable orb steals
        # focus from the text field - the exact "dictation lands nowhere" bug
        winutil.set_noactivate(self.win)
        self._dragging = True
        self._drag = {"x": e.x, "y": e.y, "moved": False}

    def _motion(self, e) -> None:
        dx = abs(self.win.winfo_pointerx() - self.win.winfo_x() - self._drag["x"])
        dy = abs(self.win.winfo_pointery() - self.win.winfo_y() - self._drag["y"])
        if not self._drag["moved"] and max(dx, dy) < DRAG_THRESHOLD:
            return
        self._drag["moved"] = True
        x = self.win.winfo_pointerx() - self._drag["x"]
        y = self.win.winfo_pointery() - self._drag["y"]
        self.win.geometry(f"+{x}+{y}")

    def _release(self, _e) -> None:
        self._dragging = False
        if self._drag["moved"]:
            x, y = winutil.snap_to_edge(self.win.winfo_x(), self.win.winfo_y(),
                                        self.size, self.size)
            self.win.geometry(f"+{x}+{y}")
            self.cfg["orb_pos"] = [x, y]
            from .. import config
            config.save(self.cfg)
        else:
            self._on_click()

    # ---- drawing ----
    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        # every coordinate below is written for a SIZE box and multiplied by k,
        # so the glyph keeps its proportions whatever the screen scale is
        k = self.size / SIZE
        m = 3 * k
        ring = {"idle": P.DARK_ACCENT, "recording": P.REC, "processing": P.DARK_ACCENT_LIT}[self._state]
        c.create_oval(m, m, self.size - m, self.size - m, fill=P.DARK_CARD,
                      outline=ring, width=2.5 * k)

        cx = cy = self.size / 2
        if self._state == "processing":
            start = (self._tick * 17) % 360
            c.create_arc(cx - 11 * k, cy - 11 * k, cx + 11 * k, cy + 11 * k, start=start,
                         extent=100, style="arc", outline=P.DARK_ACCENT_LIT, width=3 * k)
        else:
            # mic glyph: capsule + stand. Recording only recolours it - the live
            # level already animates in the HUD pill, and a second pulsing shape
            # 40 px away read as a fault rather than as feedback.
            ink = P.REC if self._state == "recording" else P.DARK_ACCENT
            c.create_oval(cx - 5 * k, cy - 13 * k, cx + 5 * k, cy + k, fill=ink,
                          outline="")
            c.create_arc(cx - 9 * k, cy - 8 * k, cx + 9 * k, cy + 8 * k, start=180,
                         extent=180, style="arc", outline=ink, width=2 * k)
            c.create_line(cx, cy + 8 * k, cx, cy + 13 * k, fill=ink, width=2 * k)
            c.create_line(cx - 5 * k, cy + 13 * k, cx + 5 * k, cy + 13 * k, fill=ink,
                          width=2 * k)

    def _animate(self) -> None:
        if self._state != "processing":
            self._animating = False
            self._draw()
            return
        self._tick += 1
        self._draw()
        self.win.after(55, self._animate)

    # ---- visibility watchdog ----
    def _ensure_visible(self) -> None:
        """The fix for "the orb vanished and I had to hide and show it again".

        Two separate failures, because hiding and re-showing only cures one of
        them and it is worth being explicit about which:

        * The OS hid the window. A topmost WS_EX_TOOLWINDOW gets demoted or
          hidden by an exclusive-fullscreen app, a lock screen or a display
          change, and Tk is never told - it still reports state "normal". This
          is what the manual hide/show was actually undoing.
        * The desktop moved. A resolution, scaling or monitor change leaves the
          saved coordinates pointing off the edge of the world. Re-showing would
          NOT have fixed that one; only moving it back does.

        Both checks are a couple of syscalls, so a plain timer is enough - there
        is no reliable Tk event for either.
        """
        log = logging.getLogger("hemsa.orb")
        try:
            if not self._dragging and self.cfg.get("show_orb", True):
                if not winutil.is_window_visible(self.win):
                    log.info("orb was hidden by the OS - showing it again")
                    self.win.deiconify()
                    self.win.attributes("-topmost", True)
                    winutil.set_noactivate(self.win)
                    self._draw()
                x, y = self.win.winfo_x(), self.win.winfo_y()
                if not winutil.on_screen(x, y, self.size, self.size):
                    nx, ny = winutil.snap_to_edge(x, y, self.size, self.size)
                    self.win.geometry(f"+{nx}+{ny}")
                    self.cfg["orb_pos"] = [nx, ny]
                    from .. import config
                    config.save(self.cfg)
                    log.info("orb was off-screen at (%d, %d) - moved to (%d, %d)",
                             x, y, nx, ny)
        except tk.TclError:
            return                      # window gone (quitting): stop rescheduling
        self.win.after(WATCHDOG_MS, self._ensure_visible)
