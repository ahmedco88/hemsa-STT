"""The live activity card for Meetings: one card that keeps its place while the
meeting moves through recording -> transcribing -> summarising.

Why it exists: a red Stop button was the only sign that anything was happening,
and after Stop there was no sign at all for however long transcription took. The
card is the answer to "is it actually doing something".

Each state gets a DIFFERENT kind of motion, because they are different promises:

* recording   - a new level arrives at the right and the history scrolls left,
                newest bar brightest. Reads as time passing, not as a jiggle.
* transcribing- the bars freeze to LINE grey and a real progress bar fills. Grey
                says "this is the recording being read back", not new audio, and
                the count is honest: longform already knows chunk i of n.
* summarising - a travelling wave. Two model calls with nothing measurable in
                between, so the motion must NOT look like a progress bar.

It is driven by polling, never by a callback from the worker thread: the job
thread writes plain attributes and the UI timer reads them, so no Tk call ever
happens off the main thread.
"""

import math
import tkinter as tk

from .. import palette as P
from . import theme
from .scale import px
from .widgets import RoundCard, mix

BARS = 44
FRAME_MS = 40
WAVE_H = 28            # logical; px() at use time
LEVEL_GAIN = 12        # same feel as the consent-line dot

# A fixed, unremarkable speech-shaped trace. Only ever used as the frozen
# backdrop when there is no real recording shape to freeze - it is never
# presented as measured audio, and it never animates.
_STATIC = [0.18 + 0.55 * abs(math.sin(i * 0.9)) * (0.55 + 0.45 * math.cos(i * 0.31))
           for i in range(BARS)]


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class ActivityCard(RoundCard):
    """pack() it once and call set(state=..., level=..., elapsed=...) from a
    timer. Call hide() when nothing is running."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent, radius=14, pad=0)
        self.state = "idle"
        self._levels = [0.0] * BARS
        self._phase = 0.0
        self._elapsed = 0.0
        self._done = self._total = 0

        body = self.body
        self._top = tk.Frame(body)
        self._top.pack(fill="x", padx=px(18), pady=(px(13), px(5)))
        self._dot = tk.Canvas(self._top, width=px(12), height=px(12),
                              highlightthickness=0, bd=0)
        self._dot_id = self._dot.create_oval(0, 0, px(11), px(11), width=0)
        self._dot.pack(side="left", padx=(0, px(10)))
        self._label = tk.Label(self._top, font=theme.F.medium, anchor="w")
        self._label.pack(side="left")
        self._clock = tk.Label(self._top, font=theme.F.medium, anchor="e")
        self._clock.pack(side="right")

        self._wave = tk.Canvas(body, height=px(WAVE_H), highlightthickness=0, bd=0)
        self._wave.pack(fill="x", padx=px(18), pady=(0, px(4)))
        self._bars = [self._wave.create_rectangle(0, 0, 0, 0, width=0)
                      for _ in range(BARS)]

        self._track = tk.Canvas(body, height=px(4), highlightthickness=0, bd=0)
        self._track_bg = self._track.create_rectangle(0, 0, 0, 0, width=0)
        self._track_fill = self._track.create_rectangle(0, 0, 0, 0, width=0)

        self._hint = tk.Label(body, font=theme.F.small, anchor="w")
        self._hint.pack(fill="x", padx=px(18), pady=(0, px(12)))
        self.restyle()

    # ---- the one entry point ----
    def set(self, state: str, level: float = 0.0, elapsed: float = 0.0,
            done: int = 0, total: int = 0) -> None:
        if state != self.state:
            self.state = state
            self._track.pack_forget()
            if state == "recording":
                self._levels = [0.0] * BARS          # a new recording starts empty
            elif state == "transcribing":
                # KEEP the recording's shape and grey it: the card is showing the
                # audio being read back. If there is nothing to keep (the page was
                # opened mid-job, or Hemsa restarted) fall back to a static trace,
                # because a flat line reads as "stopped", which is the exact
                # impression this card exists to remove.
                if max(self._levels) <= 0.0:
                    self._levels = list(_STATIC)
                self._track.pack(fill="x", padx=px(18), pady=(px(6), px(4)),
                                 before=self._hint)
        self._elapsed, self._done, self._total = elapsed, done, total
        self._phase += 0.16
        if state == "recording":
            self._recording(level)
        elif state == "transcribing":
            self._transcribing()
        elif state == "summarising":
            self._summarising()

    # ---- per state ----
    def _recording(self, level: float) -> None:
        self._levels = self._levels[1:] + [min(1.0, max(0.0, level * LEVEL_GAIN))]
        self._paint(P.REC, live=True)
        lit = math.sin(self._phase * 1.6) > -0.3
        self._dot.itemconfigure(self._dot_id, fill=P.REC if lit else P.MIST)
        self._label.configure(text="Recording", fg=P.INK)
        self._clock.configure(text=_mmss(self._elapsed))
        self._hint.configure(text="Both sides are being captured on this PC.")

    def _transcribing(self) -> None:
        self._paint(P.LINE, live=False)
        self._dot.itemconfigure(self._dot_id, fill=P.ACCENT)
        w, h = self._track.winfo_width(), px(4)
        frac = (self._done / self._total) if self._total else 0.0
        self._track.coords(self._track_bg, 0, 0, w, h)
        self._track.coords(self._track_fill, 0, 0, w * min(1.0, frac), h)
        self._track.itemconfigure(self._track_bg, fill=P.MIST)
        self._track.itemconfigure(self._track_fill, fill=P.ACCENT)
        self._label.configure(text="Transcribing", fg=P.INK)
        # no count until the first chunk is planned, rather than a fake "0 of 0"
        self._clock.configure(
            text=f"{self._done} of {self._total}" if self._total else "")
        self._hint.configure(
            text="Turning the recording into text. Nothing leaves this PC.")

    def _summarising(self) -> None:
        for i in range(BARS):
            self._levels[i] = 0.25 + 0.55 * max(
                0.0, math.sin(self._phase - i * 0.35))
        self._paint(P.ACCENT, live=False)
        self._dot.itemconfigure(self._dot_id, fill=P.ACCENT)
        dots = "." * (int(self._phase * 1.5) % 4)
        self._label.configure(text=f"Writing the summary{dots}", fg=P.INK)
        self._clock.configure(text=_mmss(self._elapsed))
        self._hint.configure(text="Usually 5 to 20 seconds on this PC.")

    # ---- drawing ----
    def _paint(self, colour: str, live: bool) -> None:
        w = self._wave.winfo_width() or px(600)
        h = self._wave.winfo_height() or px(WAVE_H)
        gap = px(2)
        bw = max(px(2), (w - gap * (BARS - 1)) / BARS)
        for i, bid in enumerate(self._bars):
            x = i * (bw + gap)
            bh = max(px(2), self._levels[i] * h * 0.92)
            # oldest on the left fades into the card, so the eye lands on "now"
            fill = mix(_ground(), colour, 0.40 + (i / BARS) * 0.60) if live \
                else colour
            self._wave.coords(bid, x, (h - bh) / 2, x + bw, (h + bh) / 2)
            self._wave.itemconfigure(bid, fill=fill)

    def restyle(self) -> None:
        super().restyle()
        # RoundCard.__init__ calls restyle() before this subclass has built any
        # of its children, so the first call has nothing to paint yet
        if not hasattr(self, "_top"):
            return
        for w in (self._top, self._dot, self._wave, self._track):
            w.configure(bg=P.CARD)
        self._label.configure(bg=P.CARD, fg=P.INK)
        self._clock.configure(bg=P.CARD, fg=P.MUTED)
        self._hint.configure(bg=P.CARD, fg=P.MUTED)


def _ground() -> str:
    return P.CARD
