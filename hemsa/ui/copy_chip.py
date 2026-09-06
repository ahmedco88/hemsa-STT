"""The rescue chip - a small "Copy text" pill that floats next to the orb for a
few seconds when a dictation may not have landed (the target window lost focus
and the paste had nowhere to go). One click puts the text back on the clipboard;
injector.paste restores the OLD clipboard 0.6 s after pasting, so without this
chip a lost paste means the text is gone entirely.
Never steals focus, same as the orb and HUD.
"""

import tkinter as tk
from tkinter import font as tkfont

import pyperclip

from .. import palette as P
from .. import winutil
from . import theme
from .scale import px

W, H = 108, 34           # logical; self.w / self.h are the px() ones
PAD = 18                 # logical, either side of a measured label
SHOW_MS = 7000
NOTICE_MS = 11000        # longer: it is read, not clicked, and it is a safety message


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
        self._place(SHOW_MS)

    def notice(self, numbers: list[str]) -> None:
        """Say that a cleanup was REFUSED because the model invented a number.

        Lives here rather than in its own file because it is the same pill, the same
        no-activate window and the same placement beside the orb, and the two must not
        drift apart into two different-looking popups.

        It exists because a refusal is otherwise indistinguishable from Ollama being
        stopped: both leave the user looking at their own untidied words. That
        ambiguity is what made "test your own model" impossible to act on."""
        self._draw("Cleanup blocked",
                   f"it added {', '.join(numbers[:3])}, which you did not say")
        self._place(NOTICE_MS)

    def _place(self, hide_after: int) -> None:
        ox, oy = self._orb.win.winfo_x(), self._orb.win.winfo_y()
        left, top, right, _b = winutil.work_area()
        orb_w, gap = self._orb.size, px(10)
        x = ox - self.w - gap if ox + orb_w + self.w + gap > right else ox + orb_w + gap
        y = max(top + px(8), oy + (orb_w - self.h) // 2)
        x = max(left + px(8), x)
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.win.deiconify()
        winutil.set_noactivate(self.win)   # style can reset on re-show
        self._schedule_hide(hide_after)

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

    def _draw(self, label: str, detail: str = "") -> None:
        """One pill, one or two lines. The width is measured rather than fixed once a
        detail line is involved, because a clipped safety message is worse than none."""
        c = self.canvas
        if detail:
            metric = tkfont.Font(root=self.win, font=theme.F.dark_small)
            self.w = metric.measure(detail) + 2 * px(PAD)
            self.h = px(H) + px(16)
            edge = P.WARN
        else:
            self.w, self.h = px(W), px(H)
            edge = P.DARK_ACCENT
        c.configure(width=self.w, height=self.h)
        c.delete("all")
        w, h = self.w, self.h
        r, e = h / 2, 2 * (px(W) / W)
        c.create_oval(e, e, h - e, h - e, fill=P.DARK_CARD, outline=edge)
        c.create_oval(w - h + e, e, w - e, h - e, fill=P.DARK_CARD, outline=edge)
        c.create_rectangle(r, e, w - r, h - e, fill=P.DARK_CARD, outline=P.DARK_CARD)
        c.create_line(r, e, w - r, e, fill=edge)
        c.create_line(r, h - e, w - r, h - e, fill=edge)
        if not detail:
            c.create_text(w / 2, h / 2, text=label, fill=P.DARK_INK,
                          font=theme.F.dark_bold)
            return
        c.create_text(w / 2, h / 2 - px(9), text=label, fill=P.WARN,
                      font=theme.F.dark_bold)
        c.create_text(w / 2, h / 2 + px(9), text=detail, fill=P.DARK_MUTED,
                      font=theme.F.dark_small)
