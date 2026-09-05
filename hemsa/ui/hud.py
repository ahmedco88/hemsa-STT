"""The pill HUD - visible only while recording/processing, bottom-centre above the
taskbar. Never takes focus (WS_EX_NOACTIVATE): if it did, the text field being
dictated into would lose focus and there'd be nothing to paste into.
"""

import tkinter as tk

from .. import palette as P
from .. import winutil
from . import theme
from .scale import px


# logical pixels: the pill is drawn for a W x H box and scaled by k at draw
# time, so it keeps its proportions on a 125% or 150% screen
W, H = 190, 44
BAR_X0 = 66
BARS = 5


class Hud:
    def __init__(self, root: tk.Tk, recorder):
        self._recorder = recorder
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=P.TRANSPARENT_KEY)
        self.win.wm_attributes("-transparentcolor", P.TRANSPARENT_KEY)
        self.w, self.h = px(W), px(H)
        self._k = self.w / W
        self.canvas = tk.Canvas(self.win, width=self.w, height=self.h,
                                bg=P.TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack()
        self._state = "idle"
        self._tick = 0
        self.win.withdraw()
        winutil.set_noactivate(self.win)

    def _place(self) -> None:
        left, _top, right, bottom = winutil.work_area()
        x = left + ((right - left) - self.w) // 2
        self.win.geometry(f"{self.w}x{self.h}+{x}+{bottom - self.h - px(14)}")

    def set_state(self, state: str, cleanup_on: bool = False) -> None:
        """cleanup_on means the SLOW (Ollama) pass; the rules pass is instant
        and would flash the word for one frame."""
        self._state = state
        self._cleanup = cleanup_on
        if state == "idle":
            self.win.withdraw()
            return
        self._place()
        self.win.deiconify()
        winutil.set_noactivate(self.win)   # style can reset on re-show
        self._animate()

    def _animate(self) -> None:
        if self._state == "idle":
            return
        c = self.canvas
        c.delete("all")
        k, w, h = self._k, self.w, self.h
        e = 2 * k
        c.create_oval(e, e, w - e, h - e, fill=P.DARK_CARD, outline=P.DARK_LINE)  # ends
        c.create_rectangle(h / 2, e, w - h / 2, h - e, fill=P.DARK_CARD, outline=P.DARK_CARD)
        c.create_arc(e, e, h - e, h - e, start=90, extent=180, fill=P.DARK_CARD, outline=P.DARK_LINE, style="pieslice")
        c.create_arc(w - h + e, e, w - e, h - e, start=270, extent=180, fill=P.DARK_CARD, outline=P.DARK_LINE, style="pieslice")
        c.create_line(h / 2, e, w - h / 2, e, fill=P.DARK_LINE)
        c.create_line(h / 2, h - e, w - h / 2, h - e, fill=P.DARK_LINE)

        self._tick += 1
        pulse = self._tick % 20 < 12
        if self._state == "recording":
            c.create_oval(20 * k, h / 2 - 5 * k, 30 * k, h / 2 + 5 * k,
                          fill=P.REC if pulse else P.DARK_MIST, outline="")
            c.create_text(40 * k, h / 2, text="listening", anchor="w",
                          fill=P.DARK_INK, font=theme.F.dark)
            # live level bars, welded to the mic like the mock
            level = min(1.0, self._recorder.level * 40)
            import math
            for i in range(BARS):
                phase = math.sin((self._tick * 0.55) + i * 1.1) * 0.5 + 0.5
                bar_h = (4 + (4 + 12 * phase) * level) * k
                x = (BAR_X0 + 62 + i * 7) * k
                c.create_rectangle(x, h / 2 - bar_h / 2, x + 3.4 * k, h / 2 + bar_h / 2,
                                   fill=P.DARK_ACCENT_LIT, outline="")
        else:
            label = "polishing…" if self._cleanup else "typing…"
            c.create_oval(20 * k, h / 2 - 5 * k, 30 * k, h / 2 + 5 * k,
                          fill=P.DARK_ACCENT if pulse else P.DARK_MIST, outline="")
            c.create_text(40 * k, h / 2, text=label, anchor="w",
                          fill=P.DARK_INK, font=theme.F.dark)
        self.win.after(55, self._animate)
