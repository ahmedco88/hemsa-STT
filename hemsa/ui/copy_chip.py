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
from . import theme
from .scale import px

W, H = 108, 34           # logical; self.w / self.h are the px() ones
SHOW_MS = 7000


class CopyChip:
    def __init__(self, root: tk.Tk, orb, get_text):
        self._orb = orb
        self._get_text = get_text
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=P.TRANSPARENT_KEY)
        self.win.wm_attributes("-transparentcolor", P.TRANSPARENT_KEY)
        self.w, self.h = px(W), px(H)
        self.canvas = tk.Canvas(self.win, width=self.w, height=self.h,
                                bg=P.TRANSPARENT_KEY, highlightthickness=0)
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
        orb_w, gap = self._orb.size, px(10)
        x = ox - self.w - gap if ox + orb_w + self.w + gap > right else ox + orb_w + gap
        y = max(top + px(8), oy + (orb_w - self.h) // 2)
        x = max(left + px(8), x)
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")
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
        w, h = self.w, self.h
        r, e = h / 2, 2 * (w / W)
        c.create_oval(e, e, h - e, h - e, fill=P.DARK_CARD, outline=P.DARK_ACCENT)
        c.create_oval(w - h + e, e, w - e, h - e, fill=P.DARK_CARD, outline=P.DARK_ACCENT)
        c.create_rectangle(r, e, w - r, h - e, fill=P.DARK_CARD, outline=P.DARK_CARD)
        c.create_line(r, e, w - r, e, fill=P.DARK_ACCENT)
        c.create_line(r, h - e, w - r, h - e, fill=P.DARK_ACCENT)
        c.create_text(w / 2, h / 2, text=label, fill=P.DARK_INK,
                      font=theme.F.dark_bold)
