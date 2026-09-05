"""Settings page. Every change applies + saves immediately - no OK/Cancel.

Three cards: General (key, microphone, theme, the four toggles), Cleanup (mode
and the Ollama model), and On this PC (engine and Ollama status). The status
rows poll only while the page is on screen: cleanup.status() is an HTTP call to
the local Ollama and the page lives for the whole session inside the shell.
"""

import tkinter as tk
from tkinter import ttk

from .. import audio, cleanup, config, hotkey, palette as P, winutil
from . import theme
from .scale import px
from .widgets import RoundCard, ScrollFrame, Toggle

PAD = 40                 # logical px, through px() at use time
POLL_MS = 3000

_HINTS = {
    "off": "Paste exactly what was heard.",
    "fast": "Removes um and uh, fixes spacing and capitals. Instant, and it can "
            "never invent words.",
    "ai": "Also fixes real mishearings, but runs a local model, measured at "
          "2-5 s per dictation on this PC.",
}


class SettingsPage(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent)
        self.app = app
        self._paper: list[tk.Widget] = []        # plain widgets on the page ground
        self._cardw: list[tk.Widget] = []        # plain widgets on a card ground
        self._lines: list[tk.Widget] = []        # hairlines
        self._widgets: list = []                 # things with restyle()
        self._swatches: dict[str, tuple[tk.Canvas, int]] = {}
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self._widgets.append(self.scroll)
        self._page = self.scroll.body
        self._build()
        self.restyle()

    # ---- build ----
    def _build(self) -> None:
        head = tk.Frame(self._page)
        head.pack(fill="x", padx=px(PAD), pady=(px(30), px(16)))
        self._paper.append(head)
        ttk.Label(head, text="Settings", font=theme.F.display).pack(anchor="w")
        ttk.Label(head, text="Every change applies straight away.",
                  style="Muted.TLabel").pack(anchor="w", pady=(px(4), 0))

        cfg = self.app.cfg

        body = self._card("General")
        right = self._row(body, "Push-to-talk key", "Hold it, speak, let go.", first=True)
        self.key_var = tk.StringVar(value=cfg["hotkey"])
        combo = ttk.Combobox(right, textvariable=self.key_var, values=hotkey.CHOICES,
                             state="readonly", width=14, style="Hemsa.TCombobox")
        combo.pack()
        combo.bind("<<ComboboxSelected>>",
                   lambda e: self._set("hotkey", self.key_var.get(), then=self.app.rebind_hotkey))

        right = self._row(body, "Microphone", "System default follows Windows.")
        mics = ["System default"] + audio.device_names()
        self.mic_var = tk.StringVar(value=cfg.get("mic_device") or "System default")
        mic = ttk.Combobox(right, textvariable=self.mic_var, values=mics,
                           state="readonly", width=26, style="Hemsa.TCombobox")
        mic.pack()
        mic.bind("<<ComboboxSelected>>", lambda e: self._set(
            "mic_device", None if self.mic_var.get() == "System default" else self.mic_var.get(),
            then=self.app.ctl._recorder.reopen))

        right = self._row(body, "Theme")
        for name in P.CHOICES:
            sw = tk.Canvas(right, width=px(22), height=px(22), highlightthickness=0,
                           bd=0, cursor="hand2")
            sw.pack(side="left", padx=(0, px(8)))
            oid = sw.create_oval(px(2), px(2), px(20), px(20), width=px(2))
            sw.bind("<Button-1>", lambda e, n=name: self.app.set_theme(n))
            self._swatches[name] = (sw, oid)
            self._cardw.append(sw)

        self._toggle(body, "Start with Windows", None, "autostart", extra=self._apply_autostart)
        self._toggle(body, "Sounds", "A soft tick on start and stop.", "sounds")
        self._toggle(body, "Floating orb", "Click it to dictate without the key.", "show_orb",
                     extra=lambda: self.app.orb.show(self.app.cfg["show_orb"]))
        self._toggle(body, "Check GitHub for updates on start",
                     "Asks GitHub for the latest version number. It sends nothing "
                     "about you.",
                     "update_check")

        body = self._card("Cleanup")
        right = self._row(body, "Tidy up dictation", _HINTS[cfg.get("cleanup_mode", "off")],
                          first=True)
        self.mode_hint = self._last_hint
        self.mode_var = tk.StringVar(
            value=config.CLEANUP_LABELS[cfg.get("cleanup_mode", "off")])
        mode = ttk.Combobox(right, textvariable=self.mode_var, state="readonly", width=17,
                            values=[config.CLEANUP_LABELS[m] for m in config.CLEANUP_MODES],
                            style="Hemsa.TCombobox")
        mode.pack()
        mode.bind("<<ComboboxSelected>>", lambda e: self._set_mode())

        right = self._row(body, "Ollama model", "Used only in Full mode.")
        self.model_var = tk.StringVar(value=cfg["cleanup_model"])
        entry = ttk.Entry(right, textvariable=self.model_var, width=16, style="Card.TEntry")
        entry.pack()
        entry.bind("<FocusOut>",
                   lambda e: self._set("cleanup_model", self.model_var.get().strip()))

        body = self._card("On this PC")
        self.engine_dot, self.engine_lbl = self._status_row(
            body, "Speech engine", "Parakeet v2 (English)", first=True)
        self.ollama_dot, self.ollama_lbl = self._status_row(
            body, "Ollama", "Local model for Full cleanup")

        foot = tk.Frame(self._page)
        foot.pack(fill="x", padx=px(PAD), pady=(px(18), px(20)))
        self._paper.append(foot)
        ttk.Label(foot, text="Everything runs on this PC. Nothing is sent anywhere.",
                  style="Muted.TLabel").pack(anchor="w")
        ttk.Label(foot, text="Built by Ahmed Al-Obaidi", style="Muted.TLabel").pack(
            anchor="w", pady=(px(2), 0))

    def _card(self, eyebrow: str) -> tk.Frame:
        ttk.Label(self._page, text=eyebrow.upper(), style="Section.TLabel").pack(
            anchor="w", padx=px(PAD), pady=(px(14), px(8)))
        card = RoundCard(self._page, width=px(100))
        card.pack(fill="x", padx=px(PAD))
        self._widgets.append(card)
        return card.body

    def _row(self, body: tk.Frame, name: str, hint: str | None = None,
             first: bool = False) -> tk.Frame:
        """A label block on the left, a container for the control on the right."""
        if not first:
            line = tk.Frame(body, height=px(1))
            line.pack(fill="x", padx=px(18))
            self._lines.append(line)
        row = tk.Frame(body)
        row.pack(fill="x", padx=px(20), pady=px(12))
        self._cardw.append(row)
        left = tk.Frame(row)
        left.pack(side="left", fill="x", expand=True)
        self._cardw.append(left)
        ttk.Label(left, text=name, style="CardName.TLabel").pack(anchor="w")
        self._last_hint = ttk.Label(left, text=hint or "", style="CardMuted.TLabel",
                                    wraplength=px(440), justify="left")
        if hint:
            self._last_hint.pack(anchor="w", pady=(px(1), 0))
        right = tk.Frame(row)
        right.pack(side="right", padx=(px(16), 0))
        self._cardw.append(right)
        return right

    def _toggle(self, body: tk.Frame, name: str, hint: str | None, key: str, extra=None) -> None:
        right = self._row(body, name, hint)
        var = tk.BooleanVar(self, value=bool(self.app.cfg[key]))
        setattr(self, f"_var_{key}", var)
        tog = Toggle(right, var, command=lambda: (self._set(key, var.get()),
                                                   extra() if extra else None))
        tog.pack()
        self._widgets.append(tog)

    def _status_row(self, body: tk.Frame, name: str, hint: str, first: bool = False):
        right = self._row(body, name, hint, first=first)
        dot = tk.Canvas(right, width=px(8), height=px(8), highlightthickness=0, bd=0)
        dot.pack(side="left", padx=(0, px(8)))
        dot.create_oval(0, 0, px(8), px(8), width=0, tags="dot")
        self._cardw.append(dot)
        lbl = ttk.Label(right, style="CardMuted.TLabel")
        lbl.pack(side="left")
        return dot, lbl

    # ---- page contract ----
    def on_show(self) -> None:
        cfg = self.app.cfg
        self.key_var.set(cfg["hotkey"])
        self.mic_var.set(cfg.get("mic_device") or "System default")
        self.mode_var.set(config.CLEANUP_LABELS[cfg.get("cleanup_mode", "off")])
        self.model_var.set(cfg["cleanup_model"])
        for key in ("autostart", "sounds", "show_orb", "update_check"):
            getattr(self, f"_var_{key}").set(bool(cfg[key]))
        self._update_hint()
        self._paint_swatches()
        self._refresh_status()

    # ---- actions ----
    def _mode_key(self) -> str:
        for m, label in config.CLEANUP_LABELS.items():
            if label == self.mode_var.get():
                return m
        return "off"

    def _set_mode(self) -> None:
        self.app.set_cleanup_mode(self._mode_key())
        self._update_hint()

    def _update_hint(self) -> None:
        self.mode_hint.configure(text=_HINTS[self._mode_key()])

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
        if not self.winfo_exists():
            return
        e = self.app.engine
        text, colour = {
            "loading": ("Parakeet v2 (English), loading…", P.WARN),
            "loaded": ("Parakeet v2 (English), loaded", P.OK_INK),
            "error": (f"Engine error: {e.error}", P.DANGER),
        }[e.state]
        self.engine_lbl.configure(text=text, foreground=colour)
        self.engine_dot.itemconfigure("dot", fill=P.OK if e.state == "loaded" else colour)
        # only poll Ollama while the page is on screen
        if self.winfo_ismapped():
            s = cleanup.status(self.app.cfg)
            text, colour = {
                "ready": ("Ollama, ready", P.OK_INK),
                "no model": ("Ollama, model not pulled", P.WARN),
                "down": ("Ollama, not running (raw paste still works)", P.WARN),
            }[s]
            self.ollama_lbl.configure(text=text, foreground=colour)
            self.ollama_dot.itemconfigure("dot", fill=P.OK if s == "ready" else colour)
            self.after(POLL_MS, self._refresh_status)

    # ---- theme ----
    def _paint_swatches(self) -> None:
        for name, (sw, oid) in self._swatches.items():
            sw.itemconfigure(oid, fill=P.THEMES[name]["ACCENT"],
                             outline=P.INK if P.current() == name else P.CARD)

    def restyle(self) -> None:
        self.configure(bg=P.PAPER)
        for w in self._paper:
            w.configure(bg=P.PAPER)
        for w in self._cardw:
            w.configure(bg=P.CARD)
        for w in self._lines:
            w.configure(bg=P.LINE)
        for w in self._widgets:
            w.restyle()
        self._paint_swatches()
