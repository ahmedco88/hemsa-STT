"""Shared styling for the light windows: font ROLES and the ttk styles.

Theme MUST be 'clam': the Windows default theme silently ignores background config
on many elements, and ttk widgets silently ignore bg/fg kwargs everywhere - styles
only. Fonts are exposed as tuples by role (F.body, F.display, ...) and read at USE
time: set_fonts() rebinds them after the bundled faces load, so a module-level
FONT = theme.F.body would freeze the fallback face. Always write font=theme.F.x.
"""

import tkinter as tk
from tkinter import ttk

from .. import palette as P
from .scale import px


class _Roles:
    """Font tuples by ROLE. Tuples, not tkinter.font.Font objects: tuples need no
    root, survive a destroyed interpreter in tests, and Tk substitutes a default
    family silently if one is missing, which is the fallback we want."""

    def __init__(self) -> None:
        self.set(set())

    def set(self, available: set[str]) -> None:
        serif = "Instrument Serif" if "Instrument Serif" in available else "Cambria"
        sans = "Figtree" if "Figtree" in available else "Segoe UI"
        medium = ("Figtree Medium", 11) if "Figtree Medium" in available \
            else ("Segoe UI", 11, "bold")
        semi = ("Figtree SemiBold", 8) if "Figtree SemiBold" in available \
            else ("Segoe UI", 8, "bold")
        self.display = (serif, 30)
        self.number = (serif, 36)
        self.brand = (serif, 26)
        self.title = (serif, 20)
        self.body = (sans, 11)
        self.medium = medium
        self.eyebrow = semi
        self.small = (sans, 9)
        self.mono = ("Consolas", 9)
        # the dark surfaces (HUD, orb menu, copy chip) keep the system face
        self.dark = ("Segoe UI", 11)
        self.dark_small = ("Segoe UI", 9)
        self.dark_bold = ("Segoe UI", 10, "bold")


F = _Roles()


def set_fonts(available: set[str]) -> None:
    """Rebind the roles to whichever bundled families actually loaded."""
    F.set(available)


def apply(win: tk.Misc) -> ttk.Style:
    win.configure(bg=P.PAPER)
    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure(".", background=P.PAPER, foreground=P.INK, font=F.body)
    style.configure("Card.TFrame", background=P.CARD)
    style.configure("TLabel", background=P.PAPER, foreground=P.INK)
    style.configure("Muted.TLabel", background=P.PAPER, foreground=P.MUTED, font=F.small)
    style.configure("Section.TLabel", background=P.PAPER, foreground=P.MUTED,
                    font=F.eyebrow)
    # inside a RoundCard the ground is CARD, so ttk text there needs its own styles
    style.configure("Card.TLabel", background=P.CARD, foreground=P.INK, font=F.body)
    style.configure("CardName.TLabel", background=P.CARD, foreground=P.INK, font=F.medium)
    style.configure("CardMuted.TLabel", background=P.CARD, foreground=P.MUTED, font=F.small)
    style.configure("Card.TEntry", fieldbackground=P.CARD, foreground=P.INK,
                    bordercolor=P.LINE, lightcolor=P.CARD, darkcolor=P.CARD,
                    insertcolor=P.ACCENT, padding=(px(8), px(4)))
    style.configure("TButton", background=P.ACCENT, foreground=P.TEXT_ON_ACCENT,
                    borderwidth=0, focusthickness=0, padding=(px(10), px(5)))
    # disabled MUST come first - ttk takes the first matching state. Disabled sinks
    # into the ground (paper + muted); hover is the raised MIST fill elsewhere, so
    # the two states can never look alike.
    style.map("TButton",
              background=[("disabled", P.PAPER), ("active", P.ACCENT_LIT),
                          ("pressed", P.DEEP)],
              foreground=[("disabled", P.MUTED)])
    style.configure("Ghost.TButton", background=P.CARD, foreground=P.INK,
                    bordercolor=P.LINE, borderwidth=1)
    style.map("Ghost.TButton", background=[("disabled", P.PAPER), ("active", P.MIST)],
              foreground=[("disabled", P.MUTED)])
    style.configure("TCheckbutton", background=P.PAPER, foreground=P.INK)
    style.map("TCheckbutton", background=[("active", P.PAPER)])
    combo = dict(fieldbackground=P.CARD, background=P.CARD, foreground=P.INK,
                 arrowcolor=P.ACCENT, selectbackground=P.MIST, selectforeground=P.INK)
    style.configure("TCombobox", **combo)
    style.configure("Hemsa.TCombobox", bordercolor=P.LINE, lightcolor=P.CARD,
                    darkcolor=P.CARD, padding=(px(8), px(4)), **combo)
    style.map("Hemsa.TCombobox", fieldbackground=[("readonly", P.CARD)],
              selectbackground=[("readonly", P.CARD)],
              selectforeground=[("readonly", P.INK)])
    # arrowsize is what sets a clam scrollbar's WIDTH; without it the bar stays
    # 14 device pixels and reads as a hairline on a scaled screen
    style.configure("Vertical.TScrollbar", background=P.MIST, troughcolor=P.CARD,
                    bordercolor=P.CARD, arrowcolor=P.MUTED, lightcolor=P.MIST,
                    darkcolor=P.MIST, gripcount=0, arrowsize=px(14))
    style.map("Vertical.TScrollbar", background=[("active", P.LINE)])
    style.configure("Horizontal.TProgressbar", background=P.ACCENT,
                    troughcolor=P.MIST, bordercolor=P.LINE, lightcolor=P.ACCENT,
                    darkcolor=P.ACCENT, thickness=px(10))
    style.configure("Treeview", background=P.CARD, fieldbackground=P.CARD,
                    foreground=P.INK, rowheight=px(26))
    style.configure("Treeview.Heading", background=P.MIST, foreground=P.DEEP,
                    font=F.eyebrow)
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
                     highlightcolor=P.ACCENT, font=F.body, padx=px(12), pady=px(10))
