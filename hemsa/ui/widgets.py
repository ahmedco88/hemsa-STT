"""Canvas-drawn furniture for the shell: rounded cards, pill buttons, toggles, the
ring gauge, day dots, and a hover fade.

tk has no rounded corners, no transitions and no per-widget hover state, so these
are drawn by hand. Every colour is read from palette at DRAW time through its slot
NAME (`_slot("MIST")`), never captured as a value: a captured hex is the
`from palette import ACCENT` trap one level up, and it would survive a tray theme
switch until something happened to repaint. Nothing in this file holds a hex.
"""

import tkinter as tk
from tkinter import font as tkfont

from .. import palette as P
from . import theme
from .scale import px


def mix(a: str, b: str, t: float) -> str:
    """Blend two "#RRGGBB" values; t is clamped to 0..1."""
    t = min(1.0, max(0.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def _slot(name: str) -> str:
    return getattr(P, name)


def _pill_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """Corner points for a smooth=True polygon that renders as a rounded box.
    r is clamped to half the shorter side, so a short wide box becomes a pill."""
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1, x1 + r, y1]


def hover(widgets, rest: str = "CARD", lit: str = "MIST", steps: int = 3, ms: int = 30,
          on_change=None) -> None:
    """Fade the background of a widget GROUP between two palette slots on
    Enter/Leave. Slot names, not values, and the fade is skipped while the first
    widget carries `_hover_locked` (an active nav row keeps its fill).

    Tk fires <Leave> on a frame when the pointer merely crosses into one of its
    own children, so a Leave is ignored while the pointer is still over any
    member of the group; without that a row flickers every time the mouse
    passes over its label. on_change(bool) fires once per real enter / leave."""
    widgets = list(widgets)
    if not widgets:
        return
    state = {"gen": 0, "lit": False}

    def paint(colour: str) -> None:
        for w in widgets:
            try:
                w.configure(bg=colour)
            except tk.TclError:
                pass

    def fade(frm: str, to: str) -> None:
        state["gen"] += 1
        gen = state["gen"]
        a, b = _slot(frm), _slot(to)
        if steps <= 0 or ms <= 0:
            paint(b)
            return

        def step(i: int) -> None:
            if gen != state["gen"]:
                return
            try:
                paint(mix(a, b, i / steps))
                if i < steps:
                    widgets[0].after(ms, lambda: step(i + 1))
            except tk.TclError:
                pass
        step(1)

    def inside(e) -> bool:
        try:
            under = widgets[0].winfo_containing(e.x_root, e.y_root)
        except tk.TclError:
            return False
        while under is not None:
            if under in widgets:
                return True
            under = under.master
        return False

    def enter(e) -> None:
        if state["lit"] or getattr(widgets[0], "_hover_locked", False):
            return
        state["lit"] = True
        fade(rest, lit)
        if on_change:
            on_change(True)

    def leave(e) -> None:
        if not state["lit"] or inside(e):
            return
        state["lit"] = False
        if not getattr(widgets[0], "_hover_locked", False):
            fade(lit, rest)
        if on_change:
            on_change(False)

    for w in widgets:
        w.bind("<Enter>", enter, add="+")
        w.bind("<Leave>", leave, add="+")


class RoundCard(tk.Canvas):
    """A rounded rectangle hosting one Frame (`.body`). Width follows the canvas,
    height follows the body, so pack(fill="x") behaves like a normal container."""

    def __init__(self, parent: tk.Misc, radius: int = 10, fill: str = "CARD",
                 outline: str = "LINE", ground: str = "PAPER", pad: int = 0,
                 stretch: bool = False, **kw):
        """stretch=True: the body fills the canvas vertically too, for a card that
        is packed with expand (the word list box, a transcript pane)."""
        super().__init__(parent, highlightthickness=0, bd=0, **kw)
        self._r, self._fill, self._outline, self._ground, self._pad = \
            px(radius), fill, outline, ground, px(pad)
        self._stretch = stretch
        self._shape = self.create_polygon(0, 0, 0, 0, 0, 0, smooth=True, width=1)
        self.body = tk.Frame(self)
        self._win = self.create_window(self._pad + 1, self._pad + 1, anchor="nw",
                                       window=self.body)
        self.bind("<Configure>", self._on_resize)
        self.body.bind("<Configure>", self._on_body)
        self.restyle()

    def _on_body(self, _e=None) -> None:
        if self._stretch:
            return
        h = self.body.winfo_reqheight() + 2 * self._pad + 2
        if self.winfo_height() != h:
            self.configure(height=h)

    def _on_resize(self, e) -> None:
        inset = 2 * self._pad + 2
        self.itemconfigure(self._win, width=max(1, e.width - inset))
        if self._stretch:
            self.itemconfigure(self._win, height=max(1, e.height - inset))
        self.coords(self._shape, *_pill_points(1, 1, e.width - 1, e.height - 1, self._r))
        self._on_body()

    def restyle(self) -> None:
        self.configure(bg=_slot(self._ground))
        self.itemconfigure(self._shape, fill=_slot(self._fill), outline=_slot(self._outline))
        self.body.configure(bg=_slot(self._fill))


class ScrollFrame(tk.Frame):
    """A vertical scroller: build page content into .body. The wheel scrolls only
    while this frame is on screen and only when the content is taller than the
    view, so several pages can share one toplevel binding."""

    def __init__(self, parent: tk.Misc, ground: str = "PAPER"):
        super().__init__(parent)
        self._ground = ground
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        self.winfo_toplevel().bind("<MouseWheel>", self._on_wheel, add="+")
        self.restyle()

    def _on_wheel(self, e) -> None:
        if not (self.winfo_exists() and self.winfo_ismapped()):
            return
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def restyle(self) -> None:
        for w in (self, self.canvas, self.body):
            w.configure(bg=_slot(self._ground))


# kind -> (fill, hover fill, text, outline or None) as palette slot names
KINDS = {
    "primary": ("INK", "DEEP", "TEXT_ON_ACCENT", None),
    "ghost": ("CARD", "MIST", "INK", "LINE"),
    "danger": ("CARD", "MIST", "DANGER", "LINE"),
    "stop": ("DANGER", "DANGER", "TEXT_ON_ACCENT", None),
    "ok": ("OK_INK", "OK_INK", "TEXT_ON_ACCENT", None),
}
DISABLED = ("PAPER", "PAPER", "MUTED", "LINE")


class PillButton(tk.Canvas):
    def __init__(self, parent: tk.Misc, text: str, command=None, kind: str = "primary",
                 padx: int = 18, pady: int = 9, font=None, ground: str = "PAPER"):
        self._command = command
        self._kind = kind
        self._padx, self._pady = px(padx), px(pady)
        self._font = font or theme.F.medium
        self._ground = ground
        self._hover = self._pressed = False
        self._enabled = True
        self._metric = tkfont.Font(root=parent, font=self._font)
        w, h = self._size(text)
        super().__init__(parent, width=w, height=h, highlightthickness=0, bd=0,
                         cursor="hand2")
        self._shape = self.create_polygon(*_pill_points(1, 1, w - 1, h - 1, h),
                                          smooth=True, width=1)
        self._text = self.create_text(w / 2, h / 2, text=text, font=self._font)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.restyle()

    def _size(self, text: str) -> tuple[int, int]:
        return (self._metric.measure(text) + 2 * self._padx,
                self._metric.metrics("linespace") + 2 * self._pady)

    def _slots(self) -> tuple:
        return KINDS[self._kind] if self._enabled else DISABLED

    def fill(self) -> str:
        return _slot(self._slots()[0])

    def _paint(self) -> None:
        fill_s, hover_s, text_s, outline_s = self._slots()
        fill = _slot(hover_s) if (self._hover and self._enabled) else _slot(fill_s)
        if self._pressed and self._enabled:
            fill = mix(fill, P.INK, 0.15)
        self.itemconfigure(self._shape, fill=fill,
                           outline=_slot(outline_s) if outline_s else fill)
        self.itemconfigure(self._text, fill=_slot(text_s))
        self.configure(bg=_slot(self._ground),
                       cursor="hand2" if self._enabled else "arrow")

    def _enter(self, _e) -> None:
        self._hover = True
        self._paint()

    def _leave(self, _e) -> None:
        self._hover = self._pressed = False
        self._paint()

    def _press(self, _e) -> None:
        self._pressed = True
        self._paint()

    def _release(self, e) -> None:
        was = self._pressed
        self._pressed = False
        self._paint()
        inside = 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()
        if was and inside and self._enabled and self._command:
            self._command()

    def configure_text(self, text: str) -> None:
        w, h = self._size(text)
        self.configure(width=w, height=h)
        self.coords(self._shape, *_pill_points(1, 1, w - 1, h - 1, h))
        self.coords(self._text, w / 2, h / 2)
        self.itemconfigure(self._text, text=text)

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._paint()

    def set_command(self, command) -> None:
        self._command = command

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._paint()

    def restyle(self) -> None:
        self._paint()


class Toggle(tk.Canvas):
    # logical sizes; the instance carries the px() ones (self.w, self.h, ...)
    W, H, KNOB, MARGIN = 38, 22, 16, 3

    def __init__(self, parent: tk.Misc, variable: tk.BooleanVar, command=None,
                 ground: str = "CARD"):
        self.w, self.h = px(self.W), px(self.H)
        self.knob, self.margin = px(self.KNOB), px(self.MARGIN)
        super().__init__(parent, width=self.w, height=self.h, highlightthickness=0,
                         bd=0, cursor="hand2")
        self._var, self._command, self._ground = variable, command, ground
        self._track = self.create_polygon(*_pill_points(0, 0, self.w, self.h, self.h / 2),
                                          smooth=True, width=0)
        self._knob = self.create_oval(0, 0, 0, 0, width=0)
        self._x = self._target_x()
        self._gen = 0
        self.bind("<Button-1>", self._click)
        self._trace = variable.trace_add("write", lambda *a: self._on_var())
        self.bind("<Destroy>", lambda e: self._untrace())
        self.restyle()

    def _untrace(self) -> None:
        try:
            self._var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _target_x(self) -> float:
        return self.w - self.margin - self.knob if self._var.get() else self.margin

    def _click(self, _e) -> None:
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _on_var(self) -> None:
        try:
            self._animate()
        except tk.TclError:
            pass

    def _animate(self) -> None:
        self._gen += 1
        gen = self._gen
        start, end = self._x, self._target_x()

        def step(i: int) -> None:
            if gen != self._gen or not self.winfo_exists():
                return
            self._x = start + (end - start) * i / 4
            self._draw()
            if i < 4:
                self.after(15, lambda: step(i + 1))
        step(1)

    def _draw(self) -> None:
        on = bool(self._var.get())
        self.itemconfigure(self._track, fill=P.ACCENT if on else P.MIST)
        y = (self.h - self.knob) / 2
        self.coords(self._knob, self._x, y, self._x + self.knob, y + self.knob)
        self.itemconfigure(self._knob, fill=P.CARD)
        self.configure(bg=_slot(self._ground))

    def restyle(self) -> None:
        self._x = self._target_x()
        self._draw()


class Star(tk.Canvas):
    """A five-point star toggle: filled when on, a thin outline when off.

    Drawn rather than typed. "\u2605" is not in either bundled face, so Tk would
    fall back to whatever system font happens to carry it and the glyph would
    change size and weight between machines - the one thing a 22 px control
    cannot absorb.
    """

    SIZE = 20                  # logical; self.size is the px() one

    def __init__(self, parent: tk.Misc, on: bool = False, command=None,
                 ground: str = "CARD"):
        self.size = px(self.SIZE)
        super().__init__(parent, width=self.size, height=self.size,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._on = bool(on)
        self._hover = False
        self._command = command
        self._ground = ground
        self._shape = self.create_polygon(*self._points(), width=max(1, px(1)))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.restyle()

    def _points(self) -> list[float]:
        import math
        c = self.size / 2
        outer, inner = c * 0.92, c * 0.40
        pts = []
        for i in range(10):
            r = outer if i % 2 == 0 else inner
            a = -math.pi / 2 + i * math.pi / 5      # first point straight up
            pts += [c + r * math.cos(a), c + r * math.sin(a)]
        return pts

    @property
    def on(self) -> bool:
        return self._on

    def set(self, on: bool) -> None:
        self._on = bool(on)
        self._paint()

    def set_ground(self, ground: str) -> None:
        """The row under it fades between CARD and MIST on hover, so the star has
        to be told - a canvas cannot inherit a background."""
        self._ground = ground
        self._paint()

    def _click(self, e) -> None:
        if not (0 <= e.x <= self.size and 0 <= e.y <= self.size):
            return
        self._on = not self._on
        self._paint()
        if self._command:
            self._command(self._on)

    def _enter(self, _e) -> None:
        self._hover = True
        self._paint()

    def _leave(self, _e) -> None:
        self._hover = False
        self._paint()

    def _paint(self) -> None:
        ground = _slot(self._ground)
        if self._on:
            self.itemconfigure(self._shape, fill=P.ACCENT, outline=P.ACCENT)
        elif self._hover:
            self.itemconfigure(self._shape, fill=ground, outline=P.ACCENT)
        else:
            self.itemconfigure(self._shape, fill=ground, outline=P.MUTED)
        self.configure(bg=ground)

    def restyle(self) -> None:
        self.coords(self._shape, *self._points())
        self._paint()


class Ring(tk.Canvas):
    """A circular gauge. set(fraction) sweeps clockwise from the top."""

    FRAMES, FRAME_MS = 24, 25

    def __init__(self, parent: tk.Misc, size: int = 72, width: int = 7,
                 ground: str = "CARD"):
        size, width = px(size), px(width)
        super().__init__(parent, width=size, height=size, highlightthickness=0, bd=0)
        # NOT self._w: tkinter keeps its own widget path there
        self._size, self._stroke, self._ground = size, width, ground
        self.fraction = 0.0
        self._shown = 0.0
        self._gen = 0
        inset = width / 2 + 1
        box = (inset, inset, size - inset, size - inset)
        self._track = self.create_arc(*box, start=0, extent=359.9, style="arc", width=width)
        self._fill = self.create_arc(*box, start=90, extent=0, style="arc", width=width)
        self.restyle()

    def set(self, fraction: float, animate: bool = True) -> None:
        self.fraction = min(1.0, max(0.0, float(fraction)))
        self._gen += 1
        gen = self._gen
        if not animate:
            self._shown = self.fraction
            self._draw()
            return
        start = self._shown

        def step(i: int) -> None:
            if gen != self._gen or not self.winfo_exists():
                return
            self._shown = start + (self.fraction - start) * i / self.FRAMES
            self._draw()
            if i < self.FRAMES:
                self.after(self.FRAME_MS, lambda: step(i + 1))
        step(1)

    def _draw(self) -> None:
        # a full 360 extent normalises to 0 in Tk, so the top is 359.9
        self.itemconfigure(self._fill, extent=-min(359.9, 360 * self._shown),
                           outline=P.ACCENT)
        self.itemconfigure(self._track, outline=P.MIST)
        self.configure(bg=_slot(self._ground))

    def restyle(self) -> None:
        self._draw()


class DayDots(tk.Frame):
    """A row of small circles, one per day, filled for a day with activity."""

    SIZE, GAP = 10, 5          # logical; self.size / self.gap are the px() ones

    def __init__(self, parent: tk.Misc, days: list[bool], ground: str = "CARD"):
        super().__init__(parent)
        self.size, self.gap = px(self.SIZE), px(self.GAP)
        self._ground = ground
        self._days = list(days)
        self._dots: list[tuple[tk.Canvas, int]] = []
        for i in range(len(self._days)):
            c = tk.Canvas(self, width=self.size, height=self.size,
                          highlightthickness=0, bd=0)
            c.pack(side="left", padx=(0 if i == 0 else self.gap, 0))
            self._dots.append((c, c.create_oval(0, 0, self.size, self.size, width=0)))
        self.restyle()

    def set(self, days: list[bool]) -> None:
        self._days = list(days)[:len(self._dots)]
        self.restyle()

    def restyle(self) -> None:
        self.configure(bg=_slot(self._ground))
        for (c, oid), on in zip(self._dots, self._days + [False] * len(self._dots)):
            c.configure(bg=_slot(self._ground))
            c.itemconfigure(oid, fill=P.ACCENT if on else P.MIST)
