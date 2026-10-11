"""The pill HUD - visible only while recording/processing, bottom-centre above the
taskbar. Never takes focus (WS_EX_NOACTIVATE): if it did, the text field being
dictated into would lose focus and there'd be nothing to paste into.

Two rows once there are words: the live draft on top (the TAIL of it, two lines at
most), then the target app's icon, the status and the level bars. Before the first
word it is the one-row pill it always was, plus the icon.
"""

import base64
import io
import math
import os
import tkinter as tk
import tkinter.font as tkfont

from .. import palette as P
from .. import winutil
from . import theme
from .scale import px
from .widgets import _pill_points


# logical pixels, scaled by k at draw time so a 125% / 150% screen keeps proportions
ROW_H = 44          # the status row
MIN_W = 220
MAX_W = 560
PAD = 18            # text inset from the pill edge
LINE_H = 21
LINES = 2
ICON = 20
BARS = 5
RADIUS = 18


def tail_lines(text: str, measure, max_w: int, n: int = LINES) -> list[str]:
    """The last words of text that fit in n lines of max_w, filled from the END
    backwards so the newest words are always visible. A leading ellipsis marks
    that older words were dropped. measure(str) -> width."""
    words = text.split()
    lines: list[str] = []
    while words and len(lines) < n:
        line = [words.pop()]
        while words and measure(" ".join([words[-1]] + line)) <= max_w:
            line.insert(0, words.pop())
        lines.insert(0, " ".join(line))
    if words and lines:
        lines[0] = "… " + lines[0]
        while measure(lines[0]) > max_w and " " in lines[0][2:]:
            lines[0] = "… " + lines[0][2:].split(" ", 1)[1]
    return lines


def _photo(img, size: int) -> tk.PhotoImage:
    """Pillow image -> Tk photo via PNG bytes: Tk 8.6 reads PNG itself, so this
    needs no PIL.ImageTk compiled module in the bundle."""
    from PIL import Image
    buf = io.BytesIO()
    img.resize((size, size), Image.LANCZOS).save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"))


class Hud:
    def __init__(self, root: tk.Tk, recorder):
        self._recorder = recorder
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=P.TRANSPARENT_KEY)
        self.win.wm_attributes("-transparentcolor", P.TRANSPARENT_KEY)
        self.canvas = tk.Canvas(self.win, bg=P.TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack()
        self._state = "idle"
        self._cleanup = False
        self._tick = 0
        self._text = ""
        self._lines: list[str] = []
        self._icon: tk.PhotoImage | None = None
        self._letter = ""
        self._k = px(100) / 100
        self.w, self.h = px(MIN_W), px(ROW_H)
        self.win.withdraw()
        winutil.set_noactivate(self.win)

    # ---- inputs ----
    def set_target(self, hwnd: int) -> None:
        """Which app the text will land in. Called on the main thread at record start."""
        self._icon, self._letter = None, ""
        path = winutil.window_exe(hwnd) if hwnd else ""
        name = os.path.splitext(os.path.basename(path))[0]
        img = None
        # a UWP app's window belongs to its frame host, whose icon would be a lie
        if name.lower() != "applicationframehost":
            img = winutil.exe_icon(path)
        if img is not None:
            try:
                self._icon = _photo(img, round(ICON * self._k))
            except Exception:
                self._icon = None
        if self._icon is None and hwnd:
            label = winutil.window_title(hwnd) or name
            self._letter = label[:1].upper()

    def set_text(self, text: str) -> None:
        self._text = text
        self._layout()

    def set_state(self, state: str, cleanup_on: bool = False) -> None:
        """cleanup_on means the SLOW (Ollama) pass; the rules pass is instant
        and would flash the word for one frame."""
        prev, self._state = self._state, state
        self._cleanup = cleanup_on
        if state == "idle":
            self._text, self._lines = "", []
            self.win.withdraw()
            return
        if prev == "idle":
            self._layout()
            self.win.deiconify()
            winutil.set_noactivate(self.win)   # style can reset on re-show
            self._animate()

    # ---- geometry ----
    def _layout(self) -> None:
        self._k = px(100) / 100
        k = self._k
        font = tkfont.Font(font=theme.F.dark)
        self._lines = tail_lines(self._text, font.measure, round((MAX_W - 2 * PAD) * k))
        text_w = max((font.measure(s) for s in self._lines), default=0)
        self.w = round(max(MIN_W * k, min(MAX_W * k, text_w + 2 * PAD * k)))
        top = (len(self._lines) * LINE_H + 12) * k if self._lines else 0
        self.h = round(ROW_H * k + top)
        self.canvas.config(width=self.w, height=self.h)
        left, _t, right, bottom = winutil.work_area()
        x = left + ((right - left) - self.w) // 2
        self.win.geometry(f"{self.w}x{self.h}+{x}+{bottom - self.h - px(14)}")

    # ---- drawing ----
    def _animate(self) -> None:
        if self._state == "idle":
            return
        c = self.canvas
        c.delete("all")
        k, w, h = self._k, self.w, self.h
        e = 2 * k
        r = RADIUS * k if self._lines else h / 2
        c.create_polygon(_pill_points(e, e, w - e, h - e, r), smooth=True,
                         fill=P.DARK_CARD, outline=P.DARK_LINE)

        for i, line in enumerate(self._lines):
            c.create_text(PAD * k, (14 + LINE_H * i) * k, text=line, anchor="nw",
                          fill=P.DARK_INK, font=theme.F.dark)

        cy = h - ROW_H * k / 2               # centre line of the status row
        x = PAD * k
        if self._icon is not None:
            c.create_image(x, cy, image=self._icon, anchor="w")
        elif self._letter:
            s = ICON * k
            c.create_polygon(_pill_points(x, cy - s / 2, x + s, cy + s / 2, 5 * k),
                             smooth=True, fill=P.DARK_MIST, outline="")
            c.create_text(x + s / 2, cy, text=self._letter, fill=P.DARK_INK,
                          font=theme.F.dark)
        x += (ICON + 10) * k

        self._tick += 1
        pulse = self._tick % 20 < 12
        if self._state == "recording":
            dot, label = (P.REC if pulse else P.DARK_MIST), "listening"
        else:
            dot = P.DARK_ACCENT if pulse else P.DARK_MIST
            label = "polishing…" if self._cleanup else "typing…"
        c.create_oval(x, cy - 5 * k, x + 10 * k, cy + 5 * k, fill=dot, outline="")
        c.create_text(x + 18 * k, cy, text=label, anchor="w",
                      fill=P.DARK_INK, font=theme.F.dark)

        if self._state == "recording":
            # live level bars, right-aligned in the status row
            level = min(1.0, self._recorder.level * 40)
            x0 = w - (PAD + BARS * 7) * k
            for i in range(BARS):
                phase = math.sin((self._tick * 0.55) + i * 1.1) * 0.5 + 0.5
                bar_h = (4 + (4 + 12 * phase) * level) * k
                bx = x0 + i * 7 * k
                c.create_rectangle(bx, cy - bar_h / 2, bx + 3.4 * k, cy + bar_h / 2,
                                   fill=P.DARK_ACCENT_LIT, outline="")
        self.win.after(55, self._animate)
