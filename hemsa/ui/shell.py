"""The one window: a sidebar of nav rows and a container that shows one page
Frame at a time.

The Toplevel is WITHDRAWN on close and never destroyed, so reopening keeps its
position and its built pages, and MeetingsFrame's refresh-on-change keeps working
while the window is hidden. Pages are built lazily by the factory passed in, so a
page nobody opens costs nothing. Page contract: a tk.Frame subclass with optional
on_show(), on_hide() and restyle().
"""

import logging
import tkinter as tk

import hemsa

from .. import palette as P, winutil
from . import theme
from .scale import px
from .widgets import hover

log = logging.getLogger("hemsa.shell")

# logical pixels: everything below goes through px() at use time, never here
W, H = 1000, 680
MIN_W, MIN_H = 860, 600
SIDE_W = 216
NAV = (("home", "Home"), ("meetings", "Meetings"), ("words", "Word list"),
       ("settings", "Settings"))
PRIVACY = "Everything runs on this PC.\nNothing you say is sent anywhere."


class Shell:
    def __init__(self, root: tk.Tk, app, pages: dict):
        self._app = app
        self._factories = pages
        self._pages: dict[str, tk.Frame] = {}
        self.current: str | None = None
        self.visible = False
        self._recording = False
        self.win = tk.Toplevel(root)
        self.win.title("Hemsa")
        self.win.minsize(px(MIN_W), px(MIN_H))
        winutil.place_near_tray(self.win, px(W), px(H))
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        theme.apply(self.win)
        self._build()
        self.win.withdraw()

    # ---- build ----
    def _build(self) -> None:
        self._side = tk.Frame(self.win, width=px(SIDE_W))
        self._side.pack(side="left", fill="y")
        self._side.pack_propagate(False)
        self._rule = tk.Frame(self.win, width=px(1))
        self._rule.pack(side="left", fill="y")
        self._main = tk.Frame(self.win)
        self._main.pack(side="left", fill="both", expand=True)

        self._brand_row = tk.Frame(self._side)
        self._brand_row.pack(fill="x", padx=px(22), pady=(px(22), px(18)))
        self._brand = tk.Label(self._brand_row, text="Hemsa", font=theme.F.brand)
        self._brand.pack(side="left")
        self._ver = tk.Label(self._brand_row, text=f"v{hemsa.__version__}", font=theme.F.small)
        self._ver.pack(side="left", padx=(px(8), 0), pady=(px(9), 0))

        self._nav: dict[str, dict] = {}
        for name, label in NAV:
            row = tk.Frame(self._side, cursor="hand2")
            row.pack(fill="x", padx=px(12), pady=px(1))
            dot = tk.Canvas(row, width=px(6), height=px(6), highlightthickness=0, bd=0)
            dot.pack(side="left", padx=(px(12), px(10)), pady=px(12))
            dot_id = dot.create_oval(0, 0, px(6), px(6), width=0)
            lbl = tk.Label(row, text=label, font=theme.F.medium, anchor="w")
            lbl.pack(side="left", fill="x", expand=True, pady=px(8))
            rec = tk.Canvas(row, width=px(8), height=px(8), highlightthickness=0, bd=0)
            rec_id = rec.create_oval(0, 0, px(8), px(8), width=0)
            group = [row, dot, lbl, rec]
            for w in group:
                w.bind("<Button-1>", lambda e, n=name: self.show(n))
            hover(group, rest="PAPER", lit="MIST")
            self._nav[name] = {"row": row, "dot": dot, "dot_id": dot_id, "lbl": lbl,
                               "rec": rec, "rec_id": rec_id, "group": group}

        self._foot = tk.Frame(self._side)
        self._foot.pack(side="bottom", fill="x", padx=px(24), pady=px(18))
        self._about = tk.Label(self._foot, text="About Hemsa", font=theme.F.medium,
                               anchor="w", cursor="hand2")
        self._about.pack(fill="x")
        self._about.bind("<Button-1>", lambda e: self.show("about"))
        self._privacy = tk.Label(self._foot, text=PRIVACY, font=theme.F.small, anchor="w",
                                 justify="left", wraplength=px(SIDE_W - 52))
        self._privacy.pack(fill="x", pady=(px(8), 0))
        self.restyle()

    # ---- pages ----
    def page(self, name: str) -> tk.Frame:
        if name not in self._pages:
            self._pages[name] = self._factories[name](self._main)   # KeyError = a bug
        return self._pages[name]

    def show(self, name: str) -> None:
        page = self.page(name)
        if self.current and self.current != name:
            old = self._pages[self.current]
            if hasattr(old, "on_hide"):
                old.on_hide()
            old.pack_forget()
        self.current = name
        page.pack(fill="both", expand=True)
        self._paint_nav()
        if not self.visible:
            self.win.deiconify()
            self.visible = True
        self.win.lift()
        self.win.focus_force()
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception:
                # a page that cannot load must not take the window down
                log.exception("page %s failed to show", name)

    def hide(self) -> None:
        if self.current:
            page = self._pages.get(self.current)
            if page is not None and hasattr(page, "on_hide"):
                page.on_hide()
        self.win.withdraw()
        self.visible = False

    def set_recording(self, on: bool) -> None:
        self._recording = bool(on)
        n = self._nav["meetings"]
        if self._recording:
            n["rec"].pack(side="right", padx=(0, px(12)))
        else:
            n["rec"].pack_forget()
        n["rec"].itemconfigure(n["rec_id"], fill=P.REC)

    # ---- colours ----
    def _paint_nav(self) -> None:
        for name, n in self._nav.items():
            on = name == self.current
            fill = P.MIST if on else P.PAPER
            for w in n["group"]:
                w.configure(bg=fill)
            n["row"]._hover_locked = on
            n["dot"].itemconfigure(n["dot_id"], fill=P.ACCENT if on else fill)
            n["lbl"].configure(fg=P.INK)
            n["rec"].itemconfigure(n["rec_id"], fill=P.REC)

    def restyle(self) -> None:
        theme.apply(self.win)
        for w in (self._side, self._main, self._brand_row, self._foot):
            w.configure(bg=P.PAPER)
        self._rule.configure(bg=P.LINE)
        self._brand.configure(bg=P.PAPER, fg=P.INK)
        self._ver.configure(bg=P.PAPER, fg=P.MUTED)
        self._about.configure(bg=P.PAPER, fg=P.INK)
        self._privacy.configure(bg=P.PAPER, fg=P.MUTED)
        self._paint_nav()
        for name, p in self._pages.items():
            if hasattr(p, "restyle"):
                try:
                    p.restyle()
                except Exception:
                    log.exception("page %s failed to restyle", name)
