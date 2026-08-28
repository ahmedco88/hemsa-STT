"""First-run setup: download the speech model, pick a mic and a key, then start.

Without this a new user has nothing - the 661 MB model is not in the installer,
so a fresh install has no way to transcribe anything until it is fetched.

Threading rule: the download runs on a worker thread and NEVER touches tkinter.
It only mutates self._prog; the UI polls that with after(), the same discipline
Engine._load() uses. Cross-thread widget calls in tkinter fail rarely and weirdly,
which is the worst kind of bug to ship to strangers.
"""

import logging
import threading
import time
import tkinter as tk
from tkinter import ttk

from .. import audio, config, download, hotkey, model_manifest, palette as P, winutil
from . import theme

POLL_MS = 120


def _mb(n: float) -> str:
    return f"{n / (1 << 20):,.0f} MB"


class OnboardingWindow:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.root = root
        self.cfg = cfg
        self.completed = False
        self._prog = {"done": 0, "total": 0, "state": "idle", "error": "", "started": 0.0}
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None

        self.win = tk.Toplevel(root)
        self.win.title("Welcome to Hemsa")
        self.win.resizable(False, False)
        theme.apply(self.win)
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        # a real setup window, so unlike the orb/HUD it SHOULD take focus
        self.win.attributes("-topmost", True)
        self.win.after(300, lambda: self.win.attributes("-topmost", False))

        ttk.Label(self.win, text="Hemsa", font=("Segoe UI", 20, "bold")).pack(
            anchor="w", padx=22, pady=(18, 0))
        ttk.Label(self.win, style="Muted.TLabel", text=(
            "Hold a key, speak, and your words are typed wherever your cursor is.\n"
            "Speech recognition runs on this PC. Nothing you say is sent anywhere.")
        ).pack(anchor="w", padx=22, pady=(2, 0))

        self._model_section()
        self._settings_section()

        self.start_btn = ttk.Button(self.win, text="Start Hemsa", command=self._finish)
        self.start_btn.pack(side="bottom", pady=(0, 18))
        self._refresh()

    # ---- sections ----
    def _model_section(self) -> None:
        ttk.Label(self.win, text="SPEECH MODEL", style="Section.TLabel").pack(
            anchor="w", padx=22, pady=(18, 2))
        self.model_lbl = ttk.Label(self.win, style="Muted.TLabel", wraplength=440,
                                   justify="left")
        self.model_lbl.pack(anchor="w", padx=22)
        self.bar = ttk.Progressbar(self.win, length=440, mode="determinate", maximum=1000)
        self.bar.pack(anchor="w", padx=22, pady=(8, 4))
        # show WHERE, so "why is it asking me to download again?" is answerable at
        # a glance instead of needing the log
        self.path_lbl = ttk.Label(self.win, style="Muted.TLabel", wraplength=440,
                                  justify="left", font=("Consolas", 8))
        self.path_lbl.pack(anchor="w", padx=22, pady=(0, 4))
        row = ttk.Frame(self.win)
        row.pack(fill="x", padx=22)
        self.dl_btn = ttk.Button(row, text="Download model", command=self._start_download)
        self.dl_btn.pack(side="left")
        self.detail_lbl = ttk.Label(row, style="Muted.TLabel")
        self.detail_lbl.pack(side="left", padx=10)

    def _settings_section(self) -> None:
        ttk.Label(self.win, text="SETUP", style="Section.TLabel").pack(
            anchor="w", padx=22, pady=(18, 2))
        pad = {"padx": 22, "pady": 3}

        row = ttk.Frame(self.win); row.pack(fill="x", **pad)
        ttk.Label(row, text="Push-to-talk key").pack(side="left")
        self.key_var = tk.StringVar(value=self.cfg["hotkey"])
        combo = ttk.Combobox(row, textvariable=self.key_var, values=hotkey.CHOICES,
                             state="readonly", width=16)
        combo.pack(side="right")

        row = ttk.Frame(self.win); row.pack(fill="x", **pad)
        ttk.Label(row, text="Microphone").pack(side="left")
        mics = ["System default"] + audio.device_names()
        self.mic_var = tk.StringVar(value=self.cfg.get("mic_device") or "System default")
        ttk.Combobox(row, textvariable=self.mic_var, values=mics, state="readonly",
                     width=28).pack(side="right")

        self.autostart_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.win, text="Start Hemsa when Windows starts",
                        variable=self.autostart_var).pack(anchor="w", padx=22, pady=3)
        self.update_var = tk.BooleanVar(value=bool(self.cfg.get("update_check")))
        ttk.Checkbutton(self.win, variable=self.update_var,
                        text="Check GitHub for new versions on start (optional)").pack(
            anchor="w", padx=22, pady=3)
        ttk.Label(self.win, style="Muted.TLabel", wraplength=440, justify="left",
                  text=("The update check is the only time Hemsa uses the internet after "
                        "setup. It sends nothing about you.")).pack(anchor="w", padx=22)

    # ---- download ----
    def _start_download(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._cancel.clear()
        self._prog.update(state="running", error="", done=0,
                          total=download.bytes_needed(self._dest()), started=time.time())

        def work() -> None:
            try:
                download.run(self._dest(), self._on_progress, self._cancel)
                self._prog["state"] = "verifying"
                self._prog["state"] = "done"
            except download.Cancelled:
                logging.getLogger("hemsa.onboarding").info("download cancelled")
                self._prog["state"] = "idle"
            except Exception as exc:
                logging.getLogger("hemsa.onboarding").exception("model download failed")
                self._prog["error"] = str(exc)
                self._prog["state"] = "error"

        self._worker = threading.Thread(target=work, daemon=True, name="model-download")
        self._worker.start()
        self._refresh()

    def _on_progress(self, done: int, total: int, _label: str) -> None:
        """Worker thread. Assignment only - no tkinter calls from here."""
        self._prog["done"] = done
        self._prog["total"] = total

    def _dest(self):
        return config.models_dir(self.cfg)

    # ---- UI polling ----
    def _refresh(self) -> None:
        if not self.win.winfo_exists():
            return
        state = self._prog["state"]
        ready = config.models_present(self.cfg)
        self.path_lbl.config(text=str(self._dest()))

        if ready:
            self.model_lbl.config(
                text=f"{model_manifest.MODEL_NAME} installed and ready.",
                foreground=P.OK)
            self.bar["value"] = 1000
            self.dl_btn.state(["disabled"])
            self.detail_lbl.config(text="")
        elif state == "running":
            done, total = self._prog["done"], max(1, self._prog["total"])
            self.bar["value"] = min(1000, done * 1000 // total)
            secs = max(0.001, time.time() - self._prog["started"])
            speed = done / secs
            eta = (total - done) / speed if speed > 1000 else 0
            self.model_lbl.config(text="Downloading the speech model, one time only…",
                                  foreground=P.MUTED)
            self.detail_lbl.config(
                text=f"{_mb(done)} of {_mb(total)}"
                     + (f" · {_mb(speed)}/s · about {eta / 60:.0f} min left" if eta else ""))
            self.dl_btn.config(text="Cancel", command=self._cancel_download)
            self.dl_btn.state(["!disabled"])
        elif state == "verifying":
            self.model_lbl.config(text="Checking the download…", foreground=P.MUTED)
            self.detail_lbl.config(text="Windows may scan the file, this takes a moment.")
        elif state == "error":
            # requests exceptions run to hundreds of characters; at wraplength 440
            # an untruncated one grows the label until it pushes the Start button
            # out of this fixed-size window. The full text is in hemsa.log.
            msg = self._prog["error"]
            if len(msg) > 140:
                msg = msg[:140] + "…"
            self.model_lbl.config(text=f"Download failed: {msg}", foreground=P.DANGER)
            self.detail_lbl.config(text="Your progress is kept - trying again resumes.")
            self.dl_btn.config(text="Try again", command=self._start_download)
            self.dl_btn.state(["!disabled"])
        else:
            self.model_lbl.config(
                text=(f"{model_manifest.MODEL_NAME} "
                      f"({model_manifest.MODEL_DETAIL}), {_mb(download.TOTAL_BYTES)}. "
                      "Downloaded once, then works offline forever."),
                foreground=P.MUTED)
            self.bar["value"] = 0
            self.dl_btn.config(text="Download model", command=self._start_download)
            self.dl_btn.state(["!disabled"])

        self.start_btn.state(["!disabled"] if ready else ["disabled"])
        self.win.after(POLL_MS, self._refresh)

    def _cancel_download(self) -> None:
        self._cancel.set()

    # ---- exit paths ----
    def _finish(self) -> None:
        self.cfg["hotkey"] = self.key_var.get()
        mic = self.mic_var.get()
        self.cfg["mic_device"] = None if mic == "System default" else mic
        self.cfg["autostart"] = bool(self.autostart_var.get())
        self.cfg["update_check"] = bool(self.update_var.get())
        self.cfg["onboarded"] = True
        config.save(self.cfg)
        try:
            winutil.set_autostart(self.cfg["autostart"])
        except OSError:
            pass
        self.completed = True
        self.win.destroy()

    def _close(self) -> None:
        self._cancel.set()
        self.win.destroy()

    def run(self) -> bool:
        """Block until the window closes. True if setup was completed."""
        winutil.place_near_tray(self.win, 500, 560)
        self.win.deiconify()
        self.win.focus_force()
        self.root.wait_window(self.win)
        return self.completed
