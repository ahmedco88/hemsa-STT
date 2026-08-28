"""About window - what Hemsa is, what it runs on, and who made it.

Deliberately concrete: the exact model, the exact stack, and where the data
lives. "Runs locally" is a claim; naming the model and the folder is evidence.
"""

import sys
import tkinter as tk
import webbrowser
from tkinter import ttk

import hemsa

from .. import cleanup, config, model_manifest, palette as P, winutil
from . import theme

REPO_URL = "https://github.com/ahmedco88/hemsa"
WIDTH = 480


class AboutWindow:
    def __init__(self, root: tk.Tk, app):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.title("About Hemsa")
        self.win.resizable(False, False)
        theme.apply(self.win)

        cfg = app.cfg
        ttk.Label(self.win, text="Hemsa", font=("Segoe UI", 20, "bold")).pack(
            anchor="w", padx=20, pady=(16, 0))
        ttk.Label(self.win, style="Muted.TLabel", text=(
            f"Version {hemsa.__version__}  ·  hemsa = \"whisper\" in Arabic")).pack(
            anchor="w", padx=20)

        self._para(
            "Hold a key, speak, and your words are typed wherever your cursor is. Built "
            "because dictation tools either live in the cloud or cost a subscription, and "
            "neither suits notes you would rather keep to yourself.")

        self._section("HOW IT WORKS")
        self._para(
            "The hotkey gates a microphone stream kept open for the life of the app, "
            "because opening a device per keypress cost a quarter of a second every time. "
            "On release the clip goes to a speech model on this CPU, your word list "
            "corrections are applied, and the text is pasted at the cursor.")

        self._section("SPEECH RECOGNITION")
        self._kv("Model", model_manifest.MODEL_NAME)
        self._kv("Details", f"{model_manifest.MODEL_DETAIL}, ~40x real time")
        self._kv("Runtime", "sherpa-onnx (ONNX Runtime)")
        self._kv("Licence", model_manifest.MODEL_LICENCE)

        self._section("CLEANUP (OPTIONAL)")
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

        self._section("BUILT WITH")
        self._kv("Language", f"Python {sys.version_info.major}.{sys.version_info.minor}")
        self._kv("Interface", "tkinter, pystray, PyInstaller")
        self._kv("Audio", "sounddevice, numpy")
        self._kv("Input", "keyboard, pyperclip, Win32 via ctypes")

        self._section("YOUR DATA")
        self._para(
            "Nothing you dictate leaves this PC. No account, no telemetry, no analytics. "
            "The only network use is the one-time model download and the optional update "
            f"check. Settings, history and your word list live in {config.DATA_DIR}.")

        bar = ttk.Frame(self.win)
        bar.pack(side="bottom", fill="x", padx=20, pady=(0, 14))
        ttk.Button(bar, text="View source on GitHub",
                   command=lambda: webbrowser.open(REPO_URL)).pack(side="left")
        ttk.Label(bar, style="Muted.TLabel",
                  text="MIT licence").pack(side="right", pady=4)
        ttk.Label(self.win, style="Muted.TLabel",
                  text="Built by Ahmed Al-Obaidi").pack(side="bottom", anchor="w", padx=20)

        # Size to the content rather than a guessed constant: the text reflows with
        # theme fonts and DPI, and a fixed height silently clips the GitHub button.
        self.win.update_idletasks()
        _l, top, _r, bottom = winutil.work_area()
        height = min(self.win.winfo_reqheight(), bottom - top - 60)
        winutil.place_near_tray(self.win, max(WIDTH, self.win.winfo_reqwidth()), height)

    # ---- little layout helpers ----
    def _section(self, text: str) -> None:
        ttk.Label(self.win, text=text, style="Section.TLabel").pack(
            anchor="w", padx=20, pady=(14, 2))

    def _para(self, text: str) -> None:
        ttk.Label(self.win, style="Muted.TLabel", wraplength=420, justify="left",
                  text=text).pack(anchor="w", padx=20, pady=(6, 0))

    def _kv(self, key: str, value: str) -> None:
        row = ttk.Frame(self.win)
        row.pack(fill="x", padx=20, pady=1)
        ttk.Label(row, text=key, style="Muted.TLabel", width=10).pack(side="left")
        ttk.Label(row, text=value, wraplength=330, justify="left").pack(side="left")
