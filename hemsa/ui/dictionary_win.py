"""Word list window - one word per line, the way it should be typed.

Deliberately a plain text box rather than a table: the user supplies only the
correct spelling, and dictionary.apply finds the near-miss itself. There is
nothing to put in a second column and nothing to toggle per row.

Cancel is real - edits only reach disk on Save. The list is loaded strictly, so
an unreadable file shows an error instead of the seed words; showing the seed
here once let a Save wipe the real list (see dictionary.WordListUnreadable).
"""

import tkinter as tk
from tkinter import messagebox, ttk

from .. import dictionary, winutil
from . import theme


class DictionaryWindow:
    def __init__(self, root: tk.Tk, on_change):
        self._on_change = on_change
        self.win = tk.Toplevel(root)
        self.win.title("Hemsa - Word list")
        winutil.place_near_tray(self.win, 420, 460)
        theme.apply(self.win)

        ttk.Label(self.win, wraplength=380, justify="left",
                  text="Names, places or terms Hemsa should always get right:"
                  ).pack(anchor="w", padx=14, pady=(14, 0))
        ttk.Label(self.win, text="One per line. Close spellings are corrected to these.",
                  style="Muted.TLabel", wraplength=380, justify="left"
                  ).pack(anchor="w", padx=14, pady=(2, 8))

        box = ttk.Frame(self.win)
        box.pack(fill="both", expand=True, padx=14)
        self.text = tk.Text(box, wrap="none", height=14, undo=True,
                            relief="solid", borderwidth=1)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        theme.apply_text(self.text)

        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=14, pady=12)
        ttk.Button(bar, text="Save", command=self._save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.win.destroy).pack(side="right", padx=6)

        try:
            words = dictionary.load(strict=True)
        except dictionary.WordListUnreadable as exc:
            messagebox.showerror("Hemsa", (
                f"Your word list could not be read:\n\n{dictionary.PATH}\n\n{exc}\n\n"
                "Hemsa has NOT changed it. Close anything else using that file and "
                "open the word list again."), parent=self.win)
            self.win.destroy()
            return

        self.text.insert("1.0", "\n".join(words))
        self.text.focus_set()

    def _save(self) -> None:
        words = [line.strip() for line in self.text.get("1.0", "end").splitlines()]
        dictionary.save([w for w in words if w])
        self._on_change()
        self.win.destroy()
