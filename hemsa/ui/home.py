"""Home page: a greeting, three counters, and recent dictations grouped by day
with a hover-to-copy pill on every row.

Replaces the History and Stats windows (2026-09-03). The counters are counts and
durations only, never dictated text; the rows are the local history, which can
hold real clinical content and never leaves this PC. The pure helpers at the top
have no tkinter in them so they are unit-testable.
"""

import logging
import time
import tkinter as tk
from datetime import datetime

import pyperclip

from .. import history, palette as P, stats
from . import theme
from .scale import px
from .widgets import DayDots, PillButton, Ring, RoundCard, Star, hover

log = logging.getLogger("hemsa.home")

TYPING_WPM = 40          # the usual clinician-typist figure
RING_TOP_WPM = 160       # a full ring: fast, fluent speech
PAD = 40                 # logical px, through px() at use time
PREVIEW = 140
PILL_INSET = 52          # Copy pill's gap from the right edge: it clears the star
REPLAY_AFTER_S = 60      # the ring sweeps again only after this long hidden


# ---- pure helpers ----

def greeting(hour: int) -> str:
    if hour < 12:
        return "Good morning."
    if hour < 17:
        return "Good afternoon."
    return "Good evening."


def group_by_day(items: list[dict], now: datetime) -> list[tuple[str, list[dict]]]:
    """[(label, entries)], newest day first, undated entries last."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    undated: list[dict] = []
    for entry in items:
        then = history.entry_time(entry)
        if then is None:
            undated.append(entry)
            continue
        back = (now.date() - then.astimezone().date()).days
        label = "Today" if back == 0 else "Yesterday" if back == 1 \
            else f"{then:%a} {then.day} {then:%b}"
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(entry)
    out = [(label, groups[label]) for label in order]
    if undated:
        out.append(("Undated", undated))
    return out


def ring_fraction(wpm: float) -> float:
    return min(1.0, max(0.0, wpm / RING_TOP_WPM))


def faster_label(wpm: float) -> str:
    if wpm <= 0:
        return "not enough yet"
    return f"{max(1, round(wpm / TYPING_WPM))}x faster than typing"


def fmt_secs(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60:02d}"


def saved_seconds(words: int, audio_s: float) -> float:
    """Seconds saved against typing the same words at TYPING_WPM."""
    return max(0.0, words / TYPING_WPM * 60 - audio_s)


def compact(n: int) -> str:
    """1,180 -> 1.2K, so a tile sub-line stays on one line."""
    return f"{n / 1000:.1f}K" if n >= 1000 else f"{n:,}"


def wpm_of(bucket: dict) -> float:
    return bucket["words"] / (bucket["audio_s"] / 60) if bucket["audio_s"] >= 30 else 0.0


def clock(entry: dict) -> str:
    then = history.entry_time(entry)
    return then.astimezone().strftime("%I:%M %p").lstrip("0").lower() if then else ""


# ---- the page ----

class HomePage(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent)
        self._app = app
        self._items: list[dict] = []
        self._hidden_at = 0.0
        self._texts: list[tk.Label] = []
        self._paper: list[tuple[tk.Widget, str | None]] = []    # (widget, fg slot)
        self._card: list[tuple[tk.Widget, str | None]] = []
        self._widgets: list = []                                 # things with restyle()
        self._build()
        self.restyle()

    # ---- build ----
    def _build(self) -> None:
        self.greet = tk.Label(self, font=theme.F.display, anchor="w")
        self.greet.pack(fill="x", padx=px(PAD), pady=(px(30), px(16)))
        self._paper.append((self.greet, "INK"))

        tiles = tk.Frame(self)
        tiles.pack(fill="x", padx=px(PAD))
        self._paper.append((tiles, None))
        # grid, not pack: the ring tile needs more room than the other two
        for col_i, weight in enumerate((7, 6, 5)):
            tiles.columnconfigure(col_i, weight=weight, uniform="tile")

        # tile 1: words per minute, with the ring
        inner, col = self._tile(tiles, 0)
        self.ring = Ring(inner, size=64)          # Ring scales its own size
        self.ring.pack(side="left")
        self._widgets.append(self.ring)
        col.pack(side="left", padx=(px(16), 0))
        self.wpm_num, self.wpm_sub = self._numbers(col, "WORDS PER MINUTE")

        # tile 2: words dictated, with the today / best-day bar
        inner, col = self._tile(tiles, 1)
        col.pack(side="left", fill="x", expand=True)
        self.words_num, self.words_sub = self._numbers(col, "WORDS DICTATED", before_sub=self._bar)

        # tile 3: typing saved, with the seven day dots
        inner, col = self._tile(tiles, 2)
        col.pack(side="left")
        self.saved_num, self.saved_sub = self._numbers(col, "TYPING SAVED", before_sub=self._dots)

        # the list
        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=px(PAD), pady=(px(10), 0))
        self._paper.append((body, None))
        self._canvas = tk.Canvas(body, highlightthickness=0, bd=0)
        self._paper.append((self._canvas, None))
        self._scroll = tk.Scrollbar(body, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._rows_frame = tk.Frame(self._canvas)
        self._paper.append((self._rows_frame, None))
        self._window_id = self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        # on the toplevel, not per row: Tk does not propagate wheel events to
        # intermediate frames. The guard keeps it to this page while it is showing.
        self.winfo_toplevel().bind("<MouseWheel>", self._on_wheel, add="+")
        self._empty = tk.Label(self._rows_frame, font=theme.F.body, anchor="w",
                               text="Nothing dictated yet. Hold the key and speak.")
        self._paper.append((self._empty, "MUTED"))

        foot = tk.Frame(self)
        foot.pack(fill="x", padx=px(PAD), pady=(px(10), px(20)))
        self._paper.append((foot, None))
        self._status = tk.Label(foot, font=theme.F.small, anchor="w")
        self._status.pack(side="left")
        self._paper.append((self._status, "MUTED"))
        self._clear = PillButton(foot, "Clear all", kind="danger", padx=14, pady=6,
                                 font=theme.F.small, command=self._clear_all)
        self._clear.pack(side="right")
        self._widgets.append(self._clear)

    def _tile(self, parent: tk.Widget, column: int):
        card = RoundCard(parent, width=px(100))
        card.grid(row=0, column=column, sticky="ew",
                  padx=(0 if column == 0 else px(7), 0 if column == 2 else px(7)))
        self._widgets.append(card)
        inner = tk.Frame(card.body)
        inner.pack(fill="x", padx=px(16), pady=px(18))
        col = tk.Frame(inner)
        self._card += [(inner, None), (col, None)]
        return inner, col

    def _numbers(self, col: tk.Widget, eyebrow: str, before_sub=None):
        num = tk.Label(col, font=theme.F.number, anchor="w")
        num.pack(anchor="w")
        lbl = tk.Label(col, text=eyebrow, font=theme.F.eyebrow, anchor="w")
        lbl.pack(anchor="w", pady=(px(2), 0))
        self._card += [(num, "INK"), (lbl, "MUTED")]
        if before_sub:
            before_sub(col)
        sub = tk.Label(col, font=theme.F.small, anchor="w")
        sub.pack(anchor="w", pady=(px(8), 0))
        self._card.append((sub, "MUTED"))
        return num, sub

    def _bar(self, col: tk.Widget) -> None:
        self._bar_h = px(6)
        self.bar = tk.Canvas(col, height=self._bar_h, highlightthickness=0, bd=0)
        self.bar.pack(fill="x", pady=(px(10), 0))
        self._bar_track = self.bar.create_rectangle(0, 0, 0, self._bar_h, width=0)
        self._bar_fill = self.bar.create_rectangle(0, 0, 0, self._bar_h, width=0)
        self._bar_frac = 0.0
        self.bar.bind("<Configure>", lambda e: self._draw_bar())
        self._card.append((self.bar, None))

    def _dots(self, col: tk.Widget) -> None:
        self.dots = DayDots(col, [False] * 7)
        self.dots.pack(anchor="w", pady=(px(10), 0))
        self._widgets.append(self.dots)

    def _draw_bar(self) -> None:
        w = self.bar.winfo_width()
        self.bar.coords(self._bar_track, 0, 0, w, self._bar_h)
        self.bar.coords(self._bar_fill, 0, 0, w * self._bar_frac, self._bar_h)
        self.bar.itemconfigure(self._bar_track, fill=P.MIST)
        self.bar.itemconfigure(self._bar_fill, fill=P.ACCENT)

    # ---- data -> screen ----
    def on_show(self) -> None:
        recent = self._hidden_at and (time.monotonic() - self._hidden_at) < REPLAY_AFTER_S
        self._refresh(animate=not recent)

    def on_hide(self) -> None:
        self._hidden_at = time.monotonic()

    def _refresh(self, animate: bool) -> None:
        self.greet.configure(text=greeting(datetime.now().hour))
        s = stats.summary()
        days = stats.last_days(7)
        wpm = wpm_of(s["all"])
        self.wpm_num.configure(text=f"{wpm:.0f}" if wpm else "–")
        self.wpm_sub.configure(text=faster_label(wpm))
        self.ring.set(ring_fraction(wpm), animate=animate)

        self.words_num.configure(text=f"{s['all']['words']:,}")
        self.words_sub.configure(
            text=f"{compact(s['today']['words'])} today · {compact(s['week']['words'])} this week")
        best = max((d["words"] for d in days), default=0)
        self._bar_frac = (s["today"]["words"] / best) if best else 0.0
        self._draw_bar()

        self.saved_num.configure(text=fmt_secs(saved_seconds(s["all"]["words"], s["all"]["audio_s"])))
        active = [d["n"] > 0 for d in days]
        self.dots.set(active)
        self.saved_sub.configure(text=f"{sum(active)} of the last 7 days")

        self._items = history.load()
        self._build_rows()
        self._say(f"Kept for {history.KEEP_HOURS} hours - star one to keep it. "
                  "Stored only on this PC.")

    def _build_rows(self) -> None:
        for child in self._rows_frame.winfo_children():
            if child is not self._empty:
                child.destroy()
        self._texts = []
        self._empty.pack_forget()
        if not self._items:
            self._empty.pack(fill="x", pady=px(16))
            return
        now = datetime.now().astimezone()
        for gi, (label, entries) in enumerate(group_by_day(self._items, now)):
            eyebrow = tk.Label(self._rows_frame, text=label.upper(), font=theme.F.eyebrow,
                               anchor="w", bg=P.PAPER, fg=P.MUTED)
            eyebrow.pack(fill="x", pady=(0 if gi == 0 else px(18), px(8)))
            card = RoundCard(self._rows_frame, width=px(100))
            card.pack(fill="x")
            for i, entry in enumerate(entries):
                if i:
                    tk.Frame(card.body, height=px(1), bg=P.LINE).pack(fill="x")
                self._row(card.body, entry)
        self._canvas.yview_moveto(0)

    def _row(self, parent: tk.Widget, entry: dict) -> None:
        text = entry.get("text", "")
        row = tk.Frame(parent, cursor="hand2", bg=P.CARD)
        row.pack(fill="x")
        when = tk.Label(row, text=clock(entry), font=theme.F.small, width=8, anchor="w",
                        bg=P.CARD, fg=P.MUTED)
        when.pack(side="left", padx=(px(18), px(6)), pady=px(13))
        # packed BEFORE the body: pack hands the leftover width to the expanding
        # widget, so a star packed after it would be squeezed to nothing
        star = Star(row, on=bool(entry.get("star")),
                    command=lambda on, e=entry: self._set_star(e, on))
        star.pack(side="right", padx=(0, px(16)))
        body = tk.Label(row, text=history.preview(text, PREVIEW), font=theme.F.body,
                        anchor="w", justify="left", bg=P.CARD, fg=P.INK,
                        wraplength=self._wrap_width())
        body.pack(side="left", fill="x", expand=True, pady=px(13), padx=(0, px(96)))
        self._texts.append(body)
        # both pills in one frame so a single place() positions them: placing them
        # separately means measuring Copy to know where Delete starts
        tools = tk.Frame(row, bg=P.MIST)
        drop = PillButton(tools, "Delete", kind="ghost", ground="MIST", padx=12, pady=5,
                          font=theme.F.small,
                          command=lambda e=entry: self._delete(e))
        drop.pack(side="left", padx=(0, px(6)))
        pill = PillButton(tools, "Copy", kind="ghost", ground="MIST", padx=12, pady=5,
                          font=theme.F.small, command=lambda: self._copy(text, pill))
        pill.pack(side="left")

        def show_pill(on: bool, tools=tools, star=star) -> None:
            # the star stands on the row ground, which the hover fade is changing
            # underneath it; a canvas cannot inherit a background, so tell it
            if star.winfo_exists():
                star.set_ground("MIST" if on else "CARD")
            if not tools.winfo_exists():
                return
            if on:
                tools.place(relx=1.0, x=-px(PILL_INSET), rely=0.5, anchor="e")
            else:
                tools.place_forget()

        hover([row, when, body], rest="CARD", lit="MIST", on_change=show_pill)
        for w in (row, when, body):
            w.bind("<ButtonRelease-1>", lambda e, t=text, p=pill: self._copy(t, p))

    def _set_star(self, entry: dict, on: bool) -> None:
        """Persist, and update the in-memory entry so the next refresh agrees."""
        try:
            history.set_star(entry, on)
        except OSError:
            log.exception("could not save the starred state")
            self._say("Could not save that - the history file is not writable.",
                      bad=True)
            return
        if on:
            entry["star"] = True
        else:
            entry.pop("star", None)
        self._say("Starred. This one is kept until you unstar it." if on
                  else f"Unstarred. Kept for {history.KEEP_HOURS} hours like the rest.")

    def _delete(self, entry: dict) -> None:
        """One row, gone. No confirm: Clear all next to it takes the whole list
        with no confirm either, and the pill only exists while the pointer is on
        that row, so there is nothing here to hit by accident."""
        try:
            self._items = history.delete(entry)
        except OSError:
            log.exception("could not delete a history entry")
            self._say("Could not delete that - the history file is not writable.",
                      bad=True)
            return
        self._build_rows()
        self._say("Deleted.")

    @staticmethod
    def _wrap_for(width: int) -> int:
        # the row loses the time column (~90 px), its paddings, the Delete + Copy
        # pills and the star column on the right
        return max(px(200), width - px(340))

    def _wrap_width(self) -> int:
        return self._wrap_for(self._canvas.winfo_width())

    def _on_canvas_resize(self, e) -> None:
        self._canvas.itemconfigure(self._window_id, width=e.width)
        for t in self._texts:
            t.configure(wraplength=self._wrap_for(e.width))

    def _on_wheel(self, e) -> None:
        if not (self.winfo_exists() and self.winfo_ismapped()):
            return
        if self._rows_frame.winfo_height() > self._canvas.winfo_height():
            self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    # ---- actions ----
    def _copy(self, text: str, pill: PillButton) -> None:
        try:
            pyperclip.copy(text)
        except Exception:
            log.exception("clipboard copy failed")
            self._say("Could not reach the clipboard - try again.", bad=True)
            return
        if pill.winfo_exists():
            pill.set_kind("ok")
            pill.configure_text("Copied")
            pill.place(relx=1.0, x=-px(PILL_INSET), rely=0.5, anchor="e")

            def back() -> None:
                if pill.winfo_exists():
                    pill.set_kind("ghost")
                    pill.configure_text("Copy")
            self.after(1200, back)
        self._say("Copied. Paste it wherever you need it.")

    def _clear_all(self) -> None:
        history.clear()
        self._items = []
        self._build_rows()
        self._say("History cleared.")

    def _say(self, text: str, bad: bool = False) -> None:
        self._status.configure(text=text, fg=P.DANGER if bad else P.MUTED)

    # ---- theme ----
    def restyle(self) -> None:
        self.configure(bg=P.PAPER)
        for w, fg in self._paper:
            w.configure(bg=P.PAPER)
            if fg:
                w.configure(fg=getattr(P, fg))
        for w, fg in self._card:
            w.configure(bg=P.CARD)
            if fg:
                w.configure(fg=getattr(P, fg))
        self._scroll.pack_forget()
        for w in self._widgets:
            w.restyle()
        self._draw_bar()
        # rows carry their colours from build time, so rebuild them
        self._refresh(animate=False)
