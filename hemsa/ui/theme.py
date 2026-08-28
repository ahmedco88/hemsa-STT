"""Shared ttk styling for the light windows. Theme MUST be 'clam': the Windows
default theme silently ignores background config on many elements, and ttk widgets
silently ignore bg/fg kwargs everywhere - styles only."""

import tkinter as tk
from tkinter import ttk

from .. import palette as P


def apply(win: tk.Toplevel) -> ttk.Style:
    win.configure(bg=P.PAPER)
    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure(".", background=P.PAPER, foreground=P.INK, font=("Segoe UI", 10))
    style.configure("Card.TFrame", background=P.CARD)
    style.configure("TLabel", background=P.PAPER, foreground=P.INK)
    style.configure("Muted.TLabel", background=P.PAPER, foreground=P.MUTED, font=("Segoe UI", 9))
    style.configure("Section.TLabel", background=P.PAPER, foreground=P.ACCENT,
                    font=("Consolas", 9, "bold"))
    style.configure("TButton", background=P.ACCENT, foreground=P.TEXT_ON_ACCENT,
                    borderwidth=0, focusthickness=0, padding=(10, 5))
    # disabled MUST come first - ttk takes the first matching state. Muted-on-accent
    # measured 1.0-1.6:1, i.e. an unreadable button; deep-on-mist is 7.5-9.7:1 and
    # still reads as inactive because the fill goes pale instead of solid.
    style.map("TButton",
              background=[("disabled", P.MIST), ("active", P.ACCENT_LIT),
                          ("pressed", P.DEEP)],
              foreground=[("disabled", P.DEEP)])
    style.configure("TCheckbutton", background=P.PAPER, foreground=P.INK)
    style.map("TCheckbutton", background=[("active", P.PAPER)])
    style.configure("TCombobox", fieldbackground=P.CARD, background=P.CARD,
                    foreground=P.INK, arrowcolor=P.ACCENT, selectbackground=P.MIST,
                    selectforeground=P.INK)
    style.configure("Horizontal.TProgressbar", background=P.ACCENT,
                    troughcolor=P.MIST, bordercolor=P.LINE, lightcolor=P.ACCENT,
                    darkcolor=P.ACCENT, thickness=10)
    style.configure("Treeview", background=P.CARD, fieldbackground=P.CARD,
                    foreground=P.INK, rowheight=26)
    style.configure("Treeview.Heading", background=P.MIST, foreground=P.DEEP,
                    font=("Consolas", 9))
    style.map("Treeview", background=[("selected", P.MIST)],
              foreground=[("selected", P.INK)])
    return style


def apply_text(widget: tk.Text) -> None:
    """Style a raw tk.Text. Classic Tk widgets take colours as kwargs and are not
    reached by ttk styles at all, so they need this or they render Windows-grey
    in every theme."""
    widget.configure(background=P.CARD, foreground=P.INK, insertbackground=P.ACCENT,
                     selectbackground=P.MIST, selectforeground=P.INK,
                     highlightthickness=1, highlightbackground=P.LINE,
                     highlightcolor=P.ACCENT, font=("Segoe UI", 10), padx=8, pady=6)
