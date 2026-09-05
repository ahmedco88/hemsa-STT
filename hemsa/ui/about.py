"""About page - what Hemsa is, what it runs on, and who made it.

Deliberately concrete: the exact model, the exact stack, and where the data
lives. "Runs locally" is a claim; naming the model and the folder is evidence.
"""

import sys
import tkinter as tk
import webbrowser
from tkinter import ttk

import hemsa

from .. import cleanup, config, model_manifest, palette as P
from . import theme
from .scale import px
from .widgets import PillButton, ScrollFrame

REPO_URL = "https://github.com/ahmedco88/hemsa-STT"
PAD = 40                 # logical px, both through px() at use time
WRAP = 620


class AboutPage(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent)
        self.app = app
        self._paper: list[tk.Widget] = []
        self._widgets: list = []
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self._widgets.append(self.scroll)
        self._page = self.scroll.body
        self._build()
        self.restyle()

    def _build(self) -> None:
        cfg = self.app.cfg
        head = tk.Frame(self._page)
        head.pack(fill="x", padx=px(PAD), pady=(px(30), px(4)))
        self._paper.append(head)
        ttk.Label(head, text="Hemsa", font=theme.F.display).pack(anchor="w")
        ttk.Label(head, style="Muted.TLabel", text=(
            f"Version {hemsa.__version__}  ·  hemsa = \"whisper\" in Arabic")).pack(anchor="w")

        self._para(
            "Hold a key, speak, and your words are typed wherever your cursor is. Built "
            "because dictation tools either live in the cloud or cost a subscription, and "
            "neither suits notes you would rather keep to yourself.")

        self._section("How it works")
        self._para(
            "The hotkey gates a microphone stream kept open for the life of the app, "
            "because opening a device per keypress cost a quarter of a second every time. "
            "On release the clip goes to a speech model on this CPU, your word list "
            "corrections are applied, and the text is pasted at the cursor.")

        self._section("Speech recognition")
        self._kv("Model", model_manifest.MODEL_NAME)
        self._kv("Details", f"{model_manifest.MODEL_DETAIL}, ~40x real time")
        self._kv("Runtime", "sherpa-onnx (ONNX Runtime)")
        self._kv("Licence", model_manifest.MODEL_LICENCE)

        self._section("Cleanup (optional)")
        mode = cfg.get("cleanup_mode", "off")
        self._kv("Mode", config.CLEANUP_LABELS.get(mode, mode))
        if mode == "ai":
            state = cleanup.status(cfg)
            self._kv("Model", cfg.get("cleanup_model", "-") +
                     ("  ✓" if state == "ready" else f"  ({state})"))
            self._kv("Runs on", "Ollama, on this PC")
        else:
            self._kv("Method", "punctuation and filler rules, no model"
                     if mode == "fast" else "none, text is pasted as heard")

        self._section("Built with")
        self._kv("Language", f"Python {sys.version_info.major}.{sys.version_info.minor}")
        self._kv("Interface", "tkinter, pystray, PyInstaller")
        self._kv("Audio", "sounddevice, PyAudioWPatch, numpy")
        self._kv("Input", "keyboard, pyperclip, Win32 via ctypes")
        self._kv("Type", "Instrument Serif and Figtree, both SIL Open Font Licence")

        self._section("Your data")
        self._para(
            "Nothing you dictate leaves this PC. No account, no telemetry, no analytics. "
            "The only network use is the one-time model download and the optional update "
            f"check. Settings, history, meetings and your word list live in {config.DATA_DIR}.")

        bar = tk.Frame(self._page)
        bar.pack(fill="x", padx=px(PAD), pady=(px(18), px(20)))
        self._paper.append(bar)
        btn = PillButton(bar, "View source on GitHub", kind="primary",
                         command=lambda: webbrowser.open(REPO_URL))
        btn.pack(side="left")
        self._widgets.append(btn)
        ttk.Label(bar, style="Muted.TLabel", text="MIT licence  ·  Built by Ahmed Al-Obaidi"
                  ).pack(side="left", padx=px(16))

    # ---- little layout helpers ----
    def _section(self, text: str) -> None:
        ttk.Label(self._page, text=text.upper(), style="Section.TLabel").pack(
            anchor="w", padx=px(PAD), pady=(px(16), px(4)))

    def _para(self, text: str) -> None:
        ttk.Label(self._page, style="Muted.TLabel", wraplength=px(WRAP),
                  justify="left", text=text, font=theme.F.body).pack(
                      anchor="w", padx=px(PAD), pady=(px(6), 0))

    def _kv(self, key: str, value: str) -> None:
        row = tk.Frame(self._page)
        row.pack(fill="x", padx=px(PAD), pady=px(1))
        self._paper.append(row)
        ttk.Label(row, text=key, style="Muted.TLabel", width=11).pack(side="left")
        ttk.Label(row, text=value, wraplength=px(WRAP - 100),
                  justify="left").pack(side="left")

    # ---- theme ----
    def restyle(self) -> None:
        self.configure(bg=P.PAPER)
        for w in self._paper:
            w.configure(bg=P.PAPER)
        for w in self._widgets:
            w.restyle()
