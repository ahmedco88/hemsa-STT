"""The rescue chip - a small "Copy text" pill that floats next to the orb for a
few seconds when a dictation may not have landed (the target window lost focus
and the paste had nowhere to go). One click puts the text back on the clipboard;
injector.paste restores the OLD clipboard 0.6 s after pasting, so without this
chip a lost paste means the text is gone entirely.
Never steals focus, same as the orb and HUD.
"""

import tkinter as tk

import pyperclip

from .. import palette as P
from .. import winutil

_TRANS = "#010203"          # transparentcolor key, never visible
W, H = 108, 34
SHOW_MS = 7000


class CopyChip:
    def __init__(self, root: tk.Tk, orb, get_text):
        self._orb = orb
        self._get_text = get_text
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=_TRANS)
        self.win.wm_attributes("-transparentcolor", _TRANS)
        self.canvas = tk.Canvas(self.win, width=W, height=H, bg=_TRANS,
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._copy())
        self.win.withdraw()
        winutil.set_noactivate(self.win)
        self._hide_job = None

    def flash(self) -> None:
        """Show next to the orb, auto-hide after SHOW_MS."""
        self._draw("Copy text")
        ox, oy = self._orb.win.winfo_x(), self._orb.win.winfo_y()
        left, top, right, _b = winutil.work_area()
        x = ox - W - 10 if ox + 56 + W + 10 > right else ox + 56 + 10
        y = max(top + 8, oy + (56 - H) // 2)
        x = max(left + 8, x)
        self.win.geometry(f"{W}x{H}+{x}+{y}")
        self.win.deiconify()
        winutil.set_noactivate(self.win)   # style can reset on re-show
        self._schedule_hide(SHOW_MS)

    def _schedule_hide(self, ms: int) -> None:
        if self._hide_job:
            self.win.after_cancel(self._hide_job)
        self._hide_job = self.win.after(ms, self._hide)

    def _hide(self) -> None:
        self._hide_job = None
        self.win.withdraw()

    def _copy(self) -> None:
        try:
            pyperclip.copy(self._get_text())
        except Exception:
            return
        self._draw("Copied ✓")
        self._schedule_hide(1200)

    def _draw(self, label: str) -> None:
        c = self.canvas
        c.delete("all")
        r = H // 2
        c.create_oval(2, 2, H - 2, H - 2, fill=P.DARK_CARD, outline=P.DARK_ACCENT)
        c.create_oval(W - H + 2, 2, W - 2, H - 2, fill=P.DARK_CARD, outline=P.DARK_ACCENT)
        c.create_rectangle(r, 2, W - r, H - 2, fill=P.DARK_CARD, outline=P.DARK_CARD)
        c.create_line(r, 2, W - r, 2, fill=P.DARK_ACCENT)
        c.create_line(r, H - 2, W - r, H - 2, fill=P.DARK_ACCENT)
        c.create_text(W / 2, H / 2, text=label, fill=P.DARK_INK,
                      font=("Segoe UI", 10, "bold"))
