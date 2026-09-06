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
from .widgets import PillButton, RoundCard, ScrollFrame, Toggle

PAD = 40                 # logical px, through px() at use time
POLL_MS = 3000

_MODEL_HINT = "Used only in Full mode. Choose from the models Ollama has on this PC."

_AUTOSTART_OK = "Hemsa opens in the tray when you sign in."
_AUTOSTART_BLOCKED = ("Windows has this switched off under Task Manager, Startup apps. "
                      "Turn it back on there.")

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
        self._poll_id: str | None = None
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

        self._toggle(body, "Start with Windows", _AUTOSTART_OK, "autostart",
                     extra=self._apply_autostart)
        self.autostart_hint = self._last_hint
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

        # A typed model name is a model that silently does not exist: the old free
        # text box happily held a model Ollama had never pulled, and Full mode then
        # fell back to raw paste with nothing on screen saying why. The list is
        # whatever `ollama list` would print on this PC.
        right = self._row(body, "Ollama model", _MODEL_HINT)
        self.model_hint = self._last_hint
        self._model_of: dict[str, str] = {}     # what is shown -> the real model name
        self.model_var = tk.StringVar(value=cfg["cleanup_model"])
        self.model_box = ttk.Combobox(right, textvariable=self.model_var, state="readonly",
                                      width=22, style="Hemsa.TCombobox")
        self.model_box.pack()
        self.model_box.bind("<<ComboboxSelected>>", lambda e: self._pick_model())
        # The status and the button live on THIS row, not two cards down. Ollama is
        # stopped more often than not on a machine where nothing autostarts it, and a
        # dropdown whose only entry is a stale config value reads as a dead end unless
        # the way out is beside it.
        self.ollama_dot, self.ollama_lbl = self._status_row(
            body, "Ollama", "Local model for Full cleanup")
        self.start_ollama = PillButton(self.ollama_lbl.master, "Start Ollama",
                                       kind="ghost", ground="CARD", padx=12, pady=5,
                                       command=self._on_start_ollama)
        self._widgets.append(self.start_ollama)
        self._fill_models([], known=False)

        body = self._card("On this PC")
        self.engine_dot, self.engine_lbl = self._status_row(
            body, "Speech engine", "Parakeet v2 (English)", first=True)

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
        self._update_autostart_hint()
        self._paint_swatches()
        self._refresh_status()

    def on_hide(self) -> None:
        """Stop the Ollama poll the moment the shell swaps this page out or hides the
        window. This used to be inferred from winfo_ismapped(), which is WRONG at the
        only moment that matters: the shell packs the page and calls on_show in the
        same breath, and a freshly packed frame is not mapped yet - nor is it after the
        next idle pass, because Tk runs idle callbacks before the map. So the whole
        status block was skipped on the first visit and the row stayed blank until the
        user left and came back. show/hide is a fact the shell already tells us."""
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None

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
        self._update_autostart_hint()

    def _update_autostart_hint(self) -> None:
        """Read the OS, not the config. A stored preference and the state it is
        meant to produce are two different facts, and this toggle read "on" for
        over a week while the Run value it describes had been deleted."""
        blocked = self.app.cfg["autostart"] and winutil.autostart_blocked()
        self.autostart_hint.configure(
            text=_AUTOSTART_BLOCKED if blocked else _AUTOSTART_OK,
            foreground=P.WARN if blocked else P.MUTED)

    def _fill_models(self, names: list[str], known: bool = True) -> None:
        """The configured model stays in the list even when it is not installed -
        dropping it would quietly rewrite the setting to whatever sorted first. But
        it is LABELLED once we have actually seen the installed list, because an
        unlabelled stale value reads as a free-text field that ignored you."""
        current = self.app.cfg["cleanup_model"]
        shown = {n: n for n in names}
        if current not in names:
            shown[f"{current}   (not installed)" if known else current] = current
        self._model_of = shown
        values = sorted(shown)
        if tuple(self.model_box.cget("values")) != tuple(values):
            self.model_box.configure(values=values)
        self._show_current()

    def _show_current(self) -> None:
        """Put the box back on whatever the config actually says."""
        current = self.app.cfg["cleanup_model"]
        for label, real in self._model_of.items():
            if real == current:
                self.model_var.set(label)
                return

    @staticmethod
    def _ready_hint(model: str) -> str:
        """Say plainly when the chosen model is one nobody has measured. A small model
        will ANSWER a dictated question instead of tidying it, and on 2026-09-06
        gemma3:4b turned a dictated question about metformin into a dose. The response
        guards reject that shape now, but a guard is a backstop, not a licence: the
        user picking the model is the one who should know."""
        if model in cleanup.CHECKED_MODELS:
            # Not silence. Grey-and-quiet beside amber-and-warning would read as an
            # endorsement, and what was actually done is one dictated sentence.
            return (f"{_MODEL_HINT} {model} is the one this was tried on, which is a "
                    "single test, not a guarantee.")
        checked = " or ".join(cleanup.CHECKED_MODELS)
        return (f"{model} has not been checked for this. A small model can ANSWER a "
                f"dictated question instead of tidying it, which on a clinical note "
                f"means inventing a dose. Hemsa rejects a reply it can see doing that "
                f"and pastes what you said, but {checked} is the model it is tested "
                f"with.")

    @staticmethod
    def _missing_hint(model: str, names: list[str]) -> str:
        """Name the exact command, but only when the setting looks like a model name.
        A value with a space in it was never a model name, and telling someone to run
        `ollama pull free text ?` sends them off to fail at a terminal."""
        pick = (f"Pick one of the {len(names)} model{'s' if len(names) != 1 else ''} "
                "Ollama does have" if names else "Ollama has no models pulled yet")
        if model and " " not in model:
            return f"{model} is not installed. {pick}, or run  ollama pull {model}"
        return f"{model} is not a model Ollama has. {pick}."

    def _pick_model(self) -> None:
        """Only ever store a name the list actually offered. `-values` is snapshotted
        into the popdown when it is posted, so a refresh while the dropdown is open
        leaves a stale label on screen - and the decorated "(not installed)" label is
        not a model name. An unknown label is ignored rather than written to config."""
        real = self._model_of.get(self.model_var.get())
        if real is None:
            self._show_current()
            return
        self._set("cleanup_model", real)
        self._refresh_status()

    def _on_start_ollama(self) -> None:
        problem = cleanup.start_server()
        if problem:
            self.ollama_lbl.configure(text=problem, foreground=P.DANGER)
            return
        # no second poll loop: _refresh_status is already running on this page and
        # will flip the dot the moment the server answers.
        self.ollama_lbl.configure(text="Starting Ollama…", foreground=P.WARN)

    def _refresh_status(self) -> None:
        # Idempotent on purpose: on_show and every model pick call this directly, and
        # without cancelling the pending tick each of those would start a SECOND 3 s
        # poll chain against localhost that nothing ever stops.
        if not self.winfo_exists():
            return
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        e = self.app.engine
        text, colour = {
            "loading": ("Parakeet v2 (English), loading…", P.WARN),
            "loaded": ("Parakeet v2 (English), loaded", P.OK_INK),
            "error": (f"Engine error: {e.error}", P.DANGER),
        }[e.state]
        self.engine_lbl.configure(text=text, foreground=colour)
        self.engine_dot.itemconfigure("dot", fill=P.OK if e.state == "loaded" else colour)
        # Ollama's status is an HTTP call, so this chain runs only between on_show and
        # on_hide - never for the life of the session.
        s, names = cleanup.probe(self.app.cfg)
        model = self.app.cfg["cleanup_model"]
        # short on the status row (it shares a line with the dot and the button), the
        # sentence that says what to DO on the model row's hint, which wraps
        text, colour = {
            "ready": ("Ollama, ready", P.OK_INK),
            "no model": ("Ollama, running", P.WARN),
            "down": ("Ollama, not running", P.WARN),
        }[s]
        self.model_hint.configure(text={
            "ready": self._ready_hint(model),
            "no model": self._missing_hint(model, names),
            "down": "Ollama is not running, so there is nothing to choose from yet. "
                    "Start it and the list fills in. Full mode pastes the raw text "
                    "until then.",
        }[s], foreground=P.MUTED if s == "ready" and model in cleanup.CHECKED_MODELS
              else P.WARN)
        self.ollama_lbl.configure(text=text, foreground=colour)
        self.ollama_dot.itemconfigure("dot", fill=P.OK if s == "ready" else colour)
        if s == "down":
            self.start_ollama.pack(side="left", padx=(px(12), 0))
        else:
            self.start_ollama.pack_forget()
            self._fill_models(names)         # only trust a list we actually got
        self._poll_id = self.after(POLL_MS, self._refresh_status)

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
