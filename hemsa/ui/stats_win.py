"""Stats window - usage totals for today / this week / all time. Counts only,
no dictated text (that lives in History). Typing-saved estimate assumes 40 wpm
typing, the usual clinician-typist figure."""

import tkinter as tk
from tkinter import ttk

from .. import stats, winutil
from . import theme

TYPING_WPM = 40


def _fmt_secs(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60} min"


def _saved(words: int, audio_s: float) -> float:
    """Seconds saved vs typing the same words at TYPING_WPM."""
    return max(0.0, words / TYPING_WPM * 60 - audio_s)


class StatsWindow:
    def __init__(self, root: tk.Tk):
        self.win = tk.Toplevel(root)
        self.win.title("Hemsa - Stats")
        winutil.place_near_tray(self.win, 380, 430)
        self.win.resizable(False, False)
        theme.apply(self.win)

        s = stats.summary()
        self._block("TODAY", s["today"])
        self._block("LAST 7 DAYS", s["week"])
        self._block("ALL TIME" + (f" - since {s['first']}" if s["first"] else ""), s["all"])

        if s["all"]["audio_s"] >= 30:
            wpm = s["all"]["words"] / (s["all"]["audio_s"] / 60)
            ttk.Label(self.win, style="Muted.TLabel",
                      text=f"You speak at about {wpm:.0f} words per minute.").pack(
                anchor="w", padx=16, pady=(14, 0))
        ttk.Label(self.win, style="Muted.TLabel",
                  text="Counts only - dictated text is never stored here.").pack(
            side="bottom", pady=(0, 12))

    def _block(self, title: str, d: dict) -> None:
        ttk.Label(self.win, text=title, style="Section.TLabel").pack(
            anchor="w", padx=16, pady=(14, 2))
        if not d["n"]:
            ttk.Label(self.win, text="No dictations yet.", style="Muted.TLabel").pack(
                anchor="w", padx=16)
            return
        lines = [
            f"{d['n']} dictation{'s' if d['n'] != 1 else ''} · {d['words']} words",
            f"{_fmt_secs(d['audio_s'])} of speech",
            f"≈ {_fmt_secs(_saved(d['words'], d['audio_s']))} of typing saved",
        ]
        for line in lines:
            ttk.Label(self.win, text=line).pack(anchor="w", padx=16)
