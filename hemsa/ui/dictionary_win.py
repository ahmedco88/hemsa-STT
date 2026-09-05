"""Word list page - one word per line, the way it should be typed.

Deliberately a plain text box rather than a table: the user supplies only the
correct spelling, and dictionary.apply finds the near-miss itself. There is
nothing to put in a second column and nothing to toggle per row.

Cancel is real - edits only reach disk on Save, and Cancel reloads what is on
disk. The list is loaded strictly, so an unreadable file shows an error and
disables Save instead of showing the seed words; showing the seed here once let
a Save wipe the real list (see dictionary.WordListUnreadable).
"""

import tkinter as tk
from tkinter import ttk

from .. import dictionary, palette as P
from . import theme
from .scale import px
from .widgets import PillButton, RoundCard

PAD = 40                 # logical px, through px() at use time
HINT = "One per line. Close spellings are corrected to these, in every app you dictate into."


class DictionaryPage(tk.Frame):
    def __init__(self, parent: tk.Misc, on_change):
        self._on_change = on_change
        super().__init__(parent)
        self._paper: list[tk.Widget] = []
        self._widgets: list = []
        self._build()
        self.restyle()

    def _build(self) -> None:
        head = tk.Frame(self)
        head.pack(fill="x", padx=px(PAD), pady=(px(30), px(16)))
        self._paper.append(head)
        left = tk.Frame(head)
        left.pack(side="left", fill="x", expand=True)
        self._paper.append(left)
        ttk.Label(left, text="Word list", font=theme.F.display).pack(anchor="w")
        ttk.Label(left, text="Names, places and terms Hemsa should always get right.",
                  style="Muted.TLabel").pack(anchor="w", pady=(px(4), 0))
        self.save_btn = PillButton(head, "Save", kind="primary", command=self._save)
        self.save_btn.pack(side="right")
        self.cancel_btn = PillButton(head, "Cancel", kind="ghost", command=self.on_show)
        self.cancel_btn.pack(side="right", padx=(0, px(8)))
        self._widgets += [self.save_btn, self.cancel_btn]

        card = RoundCard(self, width=px(100), stretch=True)
        card.pack(fill="both", expand=True, padx=px(PAD))
        self._widgets.append(card)
        box = tk.Frame(card.body)
        box.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._box = box
        self.text = tk.Text(box, wrap="none", height=16, undo=True, relief="flat", bd=0)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        self._status = ttk.Label(self, text=HINT, style="Muted.TLabel",
                                 wraplength=px(640),
                                 justify="left")
        self._status.pack(anchor="w", padx=px(PAD), pady=(px(10), px(20)))

    # ---- page contract ----
    def on_show(self) -> None:
        try:
            words = dictionary.load(strict=True)
        except dictionary.WordListUnreadable as exc:
            self._status.configure(
                text=(f"Your word list could not be read: {dictionary.PATH} ({exc}). "
                      "Hemsa has NOT changed it. Close anything else using that file "
                      "and open this page again."),
                foreground=P.DANGER)
            self.text.delete("1.0", "end")
            self.save_btn.set_enabled(False)
            return
        self.save_btn.set_enabled(True)
        self._status.configure(text=HINT, foreground=P.MUTED)
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(words))
        self.text.edit_reset()
        self.text.focus_set()

    def _save(self) -> None:
        words = [line.strip() for line in self.text.get("1.0", "end").splitlines()]
        dictionary.save([w for w in words if w])
        self._on_change()
        self._status.configure(text="Saved.", foreground=P.OK_INK)
        self.after(1500, lambda: self.winfo_exists()
                   and self._status.configure(text=HINT, foreground=P.MUTED))

    # ---- theme ----
    def restyle(self) -> None:
        self.configure(bg=P.PAPER)
        for w in self._paper:
            w.configure(bg=P.PAPER)
        self._box.configure(bg=P.CARD)
        theme.apply_text(self.text)
        self.text.configure(highlightthickness=0)
        for w in self._widgets:
            w.restyle()
