"""Settings window. Every change applies + saves immediately - no OK/Cancel."""

import tkinter as tk
from tkinter import ttk

from .. import audio, cleanup, config, hotkey, palette as P, winutil
from . import theme


class SettingsWindow:
    def __init__(self, root: tk.Tk, app):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.title("Hemsa - Settings")
        winutil.place_near_tray(self.win, 420, 590)
        self.win.resizable(False, False)
        theme.apply(self.win)
        cfg = app.cfg
        pad = {"padx": 16, "pady": 3}

        def section(text, top=14):
            ttk.Label(self.win, text=text, style="Section.TLabel").pack(
                anchor="w", padx=16, pady=(top, 2))

        section("GENERAL", top=12)

        row = ttk.Frame(self.win); row.pack(fill="x", **pad)
        ttk.Label(row, text="Push-to-talk key").pack(side="left")
        self.key_var = tk.StringVar(value=cfg["hotkey"])
        combo = ttk.Combobox(row, textvariable=self.key_var, values=hotkey.CHOICES,
                             state="readonly", width=14)
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", lambda e: self._set("hotkey", self.key_var.get(),
                                                               then=app.rebind_hotkey))

        row = ttk.Frame(self.win); row.pack(fill="x", **pad)
        ttk.Label(row, text="Microphone").pack(side="left")
        mics = ["System default"] + audio.device_names()
        self.mic_var = tk.StringVar(value=cfg.get("mic_device") or "System default")
        mic = ttk.Combobox(row, textvariable=self.mic_var, values=mics,
                           state="readonly", width=26)
        mic.pack(side="right")
        mic.bind("<<ComboboxSelected>>", lambda e: self._set(
            "mic_device", None if self.mic_var.get() == "System default" else self.mic_var.get(),
            then=self.app.ctl._recorder.reopen))

        self._check("Start with Windows", "autostart", extra=self._apply_autostart)
        self._check("Sounds (soft tick on start / stop)", "sounds")
        self._check("Show floating orb", "show_orb", extra=lambda: app.orb.show(app.cfg["show_orb"]))
        self._check("Check GitHub for updates on start", "update_check")

        section("ENGINE - on this PC, always")
        self.engine_lbl = ttk.Label(self.win, style="Muted.TLabel")
        self.engine_lbl.pack(anchor="w", padx=16)

        section("CLEANUP - optional")
        row = ttk.Frame(self.win); row.pack(fill="x", **pad)
        ttk.Label(row, text="Tidy up dictation").pack(side="left")
        self.mode_var = tk.StringVar(
            value=config.CLEANUP_LABELS[cfg.get("cleanup_mode", "off")])
        mode = ttk.Combobox(row, textvariable=self.mode_var, state="readonly", width=17,
                            values=[config.CLEANUP_LABELS[m] for m in config.CLEANUP_MODES])
        mode.pack(side="right")
        mode.bind("<<ComboboxSelected>>", lambda e: self._set_mode())
        self.mode_hint = ttk.Label(self.win, style="Muted.TLabel", wraplength=380,
                                   justify="left")
        self.mode_hint.pack(anchor="w", padx=16, pady=(2, 0))
        self._update_hint()

        row = ttk.Frame(self.win); row.pack(fill="x", **pad)
        ttk.Label(row, text="Ollama model").pack(side="left")
        self.model_var = tk.StringVar(value=cfg["cleanup_model"])
        entry = ttk.Entry(row, textvariable=self.model_var, width=16)
        entry.pack(side="right")
        entry.bind("<FocusOut>", lambda e: self._set("cleanup_model", self.model_var.get().strip()))
        self.ollama_lbl = ttk.Label(self.win, style="Muted.TLabel")
        self.ollama_lbl.pack(anchor="w", padx=16)

        ttk.Label(self.win, style="Muted.TLabel",
                  text="Built by Ahmed Al-Obaidi").pack(side="bottom", pady=(0, 12))
        ttk.Label(self.win, style="Muted.TLabel",
                  text="Everything runs on this PC. Nothing is sent anywhere.").pack(
                  side="bottom", pady=(10, 0))
        self._refresh_status()

    _HINTS = {
        "off": "Paste exactly what was heard.",
        "fast": "Removes um/uh, fixes spacing and capitals. Instant, and it can "
                "never invent words.",
        "ai": "Also fixes real mishearings, but runs a local model - measured at "
              "2-5 s per dictation on this PC.",
    }

    def _mode_key(self) -> str:
        for m, label in config.CLEANUP_LABELS.items():
            if label == self.mode_var.get():
                return m
        return "off"

    def _set_mode(self) -> None:
        self.app.set_cleanup_mode(self._mode_key())
        self._update_hint()

    def _update_hint(self) -> None:
        self.mode_hint.config(text=self._HINTS[self._mode_key()])

    def _check(self, label: str, key: str, extra=None) -> None:
        var = tk.BooleanVar(value=bool(self.app.cfg[key]))
        cb = ttk.Checkbutton(self.win, text=label, variable=var,
                             command=lambda: (self._set(key, var.get()),
                                              extra() if extra else None))
        cb.pack(anchor="w", padx=16, pady=3)

    def _set(self, key: str, value, then=None) -> None:
        self.app.cfg[key] = value
        config.save(self.app.cfg)
        if then:
            then()

    def _apply_autostart(self) -> None:
        try:
            winutil.set_autostart(self.app.cfg["autostart"])
        except OSError:
            pass

    def _refresh_status(self) -> None:
        if not self.win.winfo_exists():
            return
        e = self.app.engine
        self.engine_lbl.config(
            text={"loading": "Parakeet v2 (English) - loading…",
                  "loaded": "Parakeet v2 (English) - loaded ✓",
                  "error": f"Engine error: {e.error}"}[e.state],
            foreground={"loading": P.WARN, "loaded": P.OK, "error": P.DANGER}[e.state])
        s = cleanup.status(self.app.cfg)
        self.ollama_lbl.config(
            text={"ready": "Ollama - ready ✓", "no model": "Ollama - model not pulled",
                  "down": "Ollama - not running (raw paste still works)"}[s],
            foreground={"ready": P.OK, "no model": P.WARN, "down": P.WARN}[s])
        self.win.after(3000, self._refresh_status)
