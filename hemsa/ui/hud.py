"""The pill HUD - visible only while recording/processing, bottom-centre above the
taskbar. Never takes focus (WS_EX_NOACTIVATE): if it did, the text field being
dictated into would lose focus and there'd be nothing to paste into.
"""

import tkinter as tk

from .. import palette as P
from .. import winutil

_TRANS = "#010203"          # transparentcolor key, never visible

W, H = 190, 44
BAR_X0 = 66
BARS = 5


class Hud:
    def __init__(self, root: tk.Tk, recorder):
        self._recorder = recorder
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=_TRANS)
        self.win.wm_attributes("-transparentcolor", _TRANS)
        self.canvas = tk.Canvas(self.win, width=W, height=H, bg=_TRANS,
                                highlightthickness=0)
        self.canvas.pack()
        self._state = "idle"
        self._tick = 0
        self.win.withdraw()
        winutil.set_noactivate(self.win)

    def _place(self) -> None:
        left, _top, right, bottom = winutil.work_area()
        x = left + ((right - left) - W) // 2
        self.win.geometry(f"{W}x{H}+{x}+{bottom - H - 14}")

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
        c.create_oval(2, 2, W - 2, H - 2, fill=P.DARK_CARD, outline=P.DARK_LINE)  # ends
        c.create_rectangle(H // 2, 2, W - H // 2, H - 2, fill=P.DARK_CARD, outline=P.DARK_CARD)
        c.create_arc(2, 2, H - 2, H - 2, start=90, extent=180, fill=P.DARK_CARD, outline=P.DARK_LINE, style="pieslice")
        c.create_arc(W - H + 2, 2, W - 2, H - 2, start=270, extent=180, fill=P.DARK_CARD, outline=P.DARK_LINE, style="pieslice")
        c.create_line(H // 2, 2, W - H // 2, 2, fill=P.DARK_LINE)
        c.create_line(H // 2, H - 2, W - H // 2, H - 2, fill=P.DARK_LINE)

        self._tick += 1
        pulse = self._tick % 20 < 12
        if self._state == "recording":
            c.create_oval(20, H // 2 - 5, 30, H // 2 + 5,
                          fill=P.REC if pulse else P.DARK_MIST, outline="")
            c.create_text(40, H // 2, text="listening", anchor="w",
                          fill=P.DARK_INK, font=("Segoe UI", 11))
            # live level bars, welded to the mic like the mock
            level = min(1.0, self._recorder.level * 40)
            import math
            for i in range(BARS):
                phase = math.sin((self._tick * 0.55) + i * 1.1) * 0.5 + 0.5
                h = 4 + (4 + 12 * phase) * level
                x = BAR_X0 + 62 + i * 7
                c.create_rectangle(x, H // 2 - h / 2, x + 3.4, H // 2 + h / 2,
                                   fill=P.DARK_ACCENT_LIT, outline="")
        else:
            label = "polishing…" if self._cleanup else "typing…"
            c.create_oval(20, H // 2 - 5, 30, H // 2 + 5,
                          fill=P.DARK_ACCENT if pulse else P.DARK_MIST, outline="")
            c.create_text(40, H // 2, text=label, anchor="w",
                          fill=P.DARK_INK, font=("Segoe UI", 11))
        self.win.after(55, self._animate)
