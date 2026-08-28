"""History window - local-only past dictations, one card each, click to copy.

Replaces the earlier Treeview (select a row, then find the Copy button): the
whole point of this window is "grab the last thing I said", so the row IS the
button, exactly as it works in a sibling project. Newest first, relative times, and
the window stays open after a copy so several can be grabbed in one visit.

Built from plain tk widgets rather than ttk because each card needs its own
background colour for hover and copy feedback, which ttk styles do not give per
widget. That means colours are applied by hand here - restyle() re-does them
after a live theme switch.
"""

import tkinter as tk
from datetime import datetime

import pyperclip

from .. import history, palette as P, winutil
from . import theme

W, H = 520, 470
LIST_W = W - 44
ROW_GAP = 8
FONT = ("Segoe UI", 10)
FONT_TIME = ("Segoe UI", 8)


class HistoryWindow:
    def __init__(self, root: tk.Tk):
        self.win = tk.Toplevel(root)
        self.win.title("Hemsa - History")
        winutil.place_near_tray(self.win, W, H)
        theme.apply(self.win)

        self._items = history.load()
        self._rows: list[tuple[tk.Widget, ...]] = []

        self._head = tk.Label(self.win, font=FONT, anchor="w", justify="left",
                              text="Click any dictation to copy it. Newest first.")
        self._head.pack(fill="x", padx=16, pady=(14, 8))

        body = tk.Frame(self.win, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=16)
        self._body = body
        self._canvas = tk.Canvas(body, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(body, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=bar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self._rows_frame = tk.Frame(self._canvas)
        self._window_id = self._canvas.create_window((0, 0), window=self._rows_frame,
                                                     anchor="nw", width=LIST_W)
        self._rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        # keep the cards as wide as the canvas when the window is resized
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfigure(self._window_id, width=e.width))
        # bound on the TOPLEVEL, not bind_all and not per-card: Tk propagates the
        # wheel event up to the toplevel, so every card (including ones added
        # later) is covered, and no other Hemsa window is hijacked.
        self.win.bind("<MouseWheel>", self._on_wheel)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        self._empty = tk.Label(self._rows_frame, font=FONT, anchor="w",
                               text="Nothing dictated yet.")

        bar_row = self._bar_row = tk.Frame(self.win)
        bar_row.pack(fill="x", padx=16, pady=(10, 12))
        self._note = tk.Label(bar_row, text="Stored only on this PC", font=FONT_TIME)
        self._note.pack(side="left")
        self._clear = tk.Button(bar_row, text="Clear all", font=FONT, relief="flat",
                                bd=0, cursor="hand2", padx=14, pady=5,
                                highlightthickness=0, command=self._clear_all)
        self._clear.pack(side="right")

        self._build_rows()
        self.restyle()

    # ---- rows ----
    def _build_rows(self) -> None:
        for widgets in self._rows:
            widgets[0].destroy()
        self._rows = []
        self._empty.pack_forget()
        if not self._items:
            self._empty.pack(fill="x", pady=16)
            return
        now = datetime.now().astimezone()
        for i, entry in enumerate(self._items):
            self._rows.append(self._make_row(entry, now, first=i == 0))

    def _make_row(self, entry: dict, now: datetime, first: bool):
        row = tk.Frame(self._rows_frame, cursor="hand2")
        inner = tk.Frame(row)
        inner.pack(fill="both", expand=True, padx=12, pady=9)
        when = tk.Label(inner, text=history.relative(entry, now), font=FONT_TIME,
                        anchor="w")
        when.pack(fill="x")
        body = tk.Label(inner, text=history.preview(entry.get("text", "")), font=FONT,
                        anchor="w", justify="left", wraplength=LIST_W - 30)
        body.pack(fill="x", pady=(3, 0))
        row.pack(fill="x", pady=(0 if first else ROW_GAP, 0))

        widgets = (row, inner, when, body)

        def paint(colour: str) -> None:
            for w in widgets:
                w.configure(bg=colour)

        for w in widgets:
            w.bind("<Enter>", lambda e: paint(P.MIST))
            w.bind("<Leave>", lambda e: paint(P.CARD))
            w.bind("<ButtonRelease-1>", lambda e, t=entry.get("text", ""),
                   lbl=body: self._copy(t, lbl, paint))
        return widgets

    def _copy(self, text: str, label: tk.Label, paint) -> None:
        try:
            pyperclip.copy(text)
        except Exception:
            self._head.configure(text="Could not reach the clipboard - try again.")
            return
        paint(P.LINE)
        self._head.configure(text="Copied. Paste it wherever you need it.")
        label.after(160, lambda: paint(P.MIST))

    def _on_wheel(self, e) -> None:
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _clear_all(self) -> None:
        history.clear()
        self._items = []
        self._build_rows()
        self.restyle()

    # ---- theme ----
    def restyle(self) -> None:
        """Re-apply colours. Called after a live theme switch from the tray."""
        theme.apply(self.win)
        self.win.configure(bg=P.PAPER)
        for w in (self._head, self._note, self._body, self._bar_row, self._canvas,
                  self._rows_frame, self._empty):
            w.configure(bg=P.PAPER)
        self._head.configure(fg=P.INK)
        self._note.configure(fg=P.MUTED)
        self._empty.configure(fg=P.MUTED)
        self._clear.configure(bg=P.MIST, fg=P.DEEP, activebackground=P.LINE,
                              activeforeground=P.DEEP)
        for row, inner, when, body in self._rows:
            for w in (row, inner, when, body):
                w.configure(bg=P.CARD)
            when.configure(fg=P.MUTED)
            body.configure(fg=P.INK)
