"""Hemsa entry point: hidden Tk root owns the main thread; hotkey/tray/audio threads
post callables into a queue the root drains. `py -3.12 -m hemsa` to run,
`--selftest` for a headless wiring check.
"""

import logging
import queue
import sys
import tkinter as tk
from pathlib import Path

from . import config, controller, hotkey as hotkey_mod, meeting_jobs, meetings, \
    model_manifest, palette, winutil
from .engine import Engine
from .ui import copy_chip as chip_mod, hud as hud_mod, meetings_win as meetings_win_mod, \
    orb as orb_mod, orb_menu as orb_menu_mod, shell as shell_mod, theme as theme_mod, \
    tray as tray_mod
from .ui.about import AboutPage
from .ui.dictionary_win import DictionaryPage
from .ui.home import HomePage
from .ui.settings import SettingsPage


def _setup_logging() -> Path:
    """Set up file logging, falling back to %TEMP% if the normal location cannot
    be written.

    Under pythonw a failing log handler is swallowed silently (stderr is None),
    so the app runs on with no record at all - which is exactly what made a
    spurious first-run prompt impossible to diagnose on 2026-08-23. A fallback
    log is worth far more than a tidy one.
    """
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    primary = config.DATA_DIR / "hemsa.log"
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(primary, "a", encoding="utf-8"):    # prove it is writable NOW
            pass
        logging.basicConfig(filename=primary, level=logging.INFO, format=fmt)
        return primary
    except OSError:
        import tempfile
        fallback = Path(tempfile.gettempdir()) / "hemsa-fallback.log"
        logging.basicConfig(filename=fallback, level=logging.INFO, format=fmt)
        logging.getLogger("hemsa").error(
            "could not write %s - logging here instead", primary)
        return fallback


class App:
    def __init__(self, root: tk.Tk, cfg: dict):
        # root and cfg come from main(): onboarding may already have used them,
        # and Engine must not be constructed until the model is on disk (it has
        # no reload path - a load failure is permanent until restart).
        self.cfg = cfg
        self._q: queue.Queue = queue.Queue()
        self.root = root
        self.root.withdraw()

        if self.cfg["hotkey"] not in hotkey_mod.CHOICES:   # e.g. a removed "right ctrl"
            self.cfg["hotkey"] = "ctrl+win"
            config.save(self.cfg)

        self.engine = Engine(self.cfg)
        self.ctl = controller.Controller(self.cfg, self.engine, self.post)

        # on_change fires from the job thread as well as the UI thread, so it is
        # wrapped in post(): every Tk call below happens on the main thread only.
        self.jobs = meeting_jobs.MeetingJobs(
            self.cfg, self.engine, self.ctl,
            on_change=lambda mid: self.post(self._meetings_changed))

        self.hud = hud_mod.Hud(self.root, self.ctl._recorder)
        self.orb = orb_mod.Orb(self.root, self.cfg, self.ctl.orb_click,
                               menu=orb_menu_mod.OrbMenu(self.root, self))
        self.chip = chip_mod.CopyChip(self.root, self.orb, lambda: self.ctl.last_text)
        # the one window; pages build on first open. History and Stats live on Home.
        self.shell = shell_mod.Shell(self.root, self, pages={
            "home": lambda parent: HomePage(parent, self),
            "meetings": lambda parent: meetings_win_mod.MeetingsFrame(parent, self),
            "words": lambda parent: DictionaryPage(parent, self.ctl.reload_dictionary),
            "settings": lambda parent: SettingsPage(parent, self),
            "about": lambda parent: AboutPage(parent, self),
        })
        self.ctl.on_state = self._on_state
        self.ctl.on_paste_risk = self.chip.flash

        self.hotkey = hotkey_mod.Hotkey(
            lambda: self.post(self.ctl.hotkey_press),
            lambda: self.post(self.ctl.hotkey_release))
        if self.cfg["hotkey_enabled"]:
            self.hotkey.bind(self.cfg["hotkey"])

        self.tray = tray_mod.build(self)
        self.tray.run_detached()

        # 15 ms, not 30: this pump is the only path from the hotkey thread to the
        # recorder gate, so its interval is a direct floor on press-to-listening lag.
        self.root.after(15, self._pump)
        # after the window system is up: recover() re-queues anything the last run
        # left mid-flight, and marks a meeting killed while recording as an error.
        self.root.after(400, self._recover_meetings)
        if self.cfg.get("update_check"):
            self.root.after(4000, lambda: self.check_updates(quiet=True))

    # ---- queue plumbing (any thread -> main thread) ----
    def post(self, fn) -> None:
        self._q.put(fn)

    def _pump(self) -> None:
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            logging.getLogger("hemsa").exception("queued action failed")
        self.root.after(15, self._pump)

    # ---- meetings ----
    def _recover_meetings(self) -> None:
        try:
            self.jobs.recover()
        except meetings.MeetingsUnreadable:
            # a broken store must not stop dictation, which is the daily job
            logging.getLogger("hemsa").exception("meetings store unreadable at start")

    def _meetings_changed(self) -> None:
        """Runs on the main thread (App.post). The tray and the sidebar always
        learn about it; the page only refreshes while it is on screen."""
        self._refresh_tray()
        self.shell.set_recording(bool(self.jobs.recording_id))
        if self.shell.visible and self.shell.current == "meetings":
            self.shell.page("meetings").refresh()

    def open_home(self) -> None:
        self.shell.show("home")

    def open_meetings(self) -> None:
        self.shell.show("meetings")

    # ---- controller -> UI ----
    def _on_state(self, state: str) -> None:
        self.hud.set_state(state, cleanup_on=self.cfg.get("cleanup_mode") == "ai")
        if state == "recording":
            import time
            wait = (time.perf_counter() - hotkey_mod.LAST_PRESS) * 1000
            if 0 < wait < 2000:
                logging.getLogger("hemsa").info("press -> HUD visible in %.0f ms", wait)
        self.orb.set_state(state)
        self._refresh_tray()

    def _refresh_tray(self) -> None:
        """The tray reflects BOTH jobs: dictation state and whether a meeting is
        being recorded. Called from _on_state and from _meetings_changed, both on
        the main thread."""
        meeting = bool(getattr(self, "jobs", None) and self.jobs.recording_id)
        state = self.ctl.state
        self.tray.title = tray_mod.title_for(state, meeting)
        self.tray.icon = tray_mod._icon_image(meeting)

    # ---- tray / settings actions (main thread) ----
    def set_cleanup_mode(self, mode: str) -> None:
        if mode not in config.CLEANUP_MODES:
            return
        self.cfg["cleanup_mode"] = mode
        config.save(self.cfg)
        self.tray.update_menu()

    def toggle_orb(self) -> None:
        self.cfg["show_orb"] = not self.cfg["show_orb"]
        config.save(self.cfg)
        self.orb.show(self.cfg["show_orb"])
        self.tray.update_menu()

    def toggle_hotkey(self) -> None:
        self.cfg["hotkey_enabled"] = not self.cfg["hotkey_enabled"]
        config.save(self.cfg)
        if self.cfg["hotkey_enabled"]:
            self.hotkey.bind(self.cfg["hotkey"])
        else:
            self.hotkey.unbind()
        self.tray.update_menu()

    def set_theme(self, name: str) -> None:
        """Re-skin live: rebind the palette, then refresh every colour surface -
        orb canvas, tray icon + menu, and any open ttk window."""
        self.cfg["theme"] = palette.set_theme(name)
        config.save(self.cfg)
        self.orb.set_state(self.ctl.state)
        self._refresh_tray()
        self.tray.update_menu()
        self.shell.restyle()

    def rebind_hotkey(self) -> None:
        if self.cfg["hotkey_enabled"]:
            self.hotkey.bind(self.cfg["hotkey"])

    def open_settings(self) -> None:
        self.shell.show("settings")

    def check_updates(self, quiet: bool = False) -> None:
        """Ask GitHub whether a newer release exists. quiet=True is the on-start
        check: it stays silent unless there is genuinely something newer, so a
        flaky connection never nags. Runs off the main thread and reports back
        through the app queue like every other worker."""
        import threading
        from . import updates

        def work() -> None:
            found = updates.check(__import__("hemsa").__version__)
            self.post(lambda: self._show_update(found, quiet))

        threading.Thread(target=work, daemon=True, name="update-check").start()

    def _show_update(self, found: dict | None, quiet: bool) -> None:
        from tkinter import messagebox

        from . import updates
        if found is None:
            if not quiet:
                messagebox.showinfo("Hemsa", "You are on the latest version.")
            return
        # found['version'] came from a strict digits-and-dots regex, so it is safe
        # to display; nothing else from the response is used at all.
        if messagebox.askyesno("Hemsa", f"Hemsa {found['version']} is available.\n\n"
                                        "Open the download page?"):
            updates.open_page(found["url"])

    def open_about(self) -> None:
        self.shell.show("about")

    def open_dictionary(self) -> None:
        self.shell.show("words")

    def quit(self) -> None:
        try:
            self.hotkey.unbind()
            self.ctl._recorder.close()
            # a meeting still recording: close the WAVs cleanly and leave the row
            # in transcribing, so the next start's recover() picks it up instead
            # of finding a half-written file and an "interrupted" error.
            if self.jobs.recording_id:
                try:
                    self.jobs.stop_recording()
                except Exception:
                    logging.getLogger("hemsa").exception("stopping the meeting recorder")
            self.tray.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def selftest() -> int:
    """Headless wiring check: config, models, dictionary contract, engine load."""
    from . import cleanup, dictionary
    cfg = config.load()
    print(f"config dir : {config.DATA_DIR}")
    print(f"models dir : {config.models_dir(cfg)} ({'found' if config.models_present(cfg) else 'MISSING'})")
    text, applied = dictionary.apply("the g p reviewed it", ["GP"])
    print(f"word list  : {'ok' if text == 'the GP reviewed it' else 'FAIL'} ({text!r})")
    # against the REAL store, then cleaned up: the point is to prove this PC can
    # create, read back and delete a meeting row, not to test sqlite.
    try:
        mid = meetings.create("import")
        try:
            row = meetings.get(mid)
        finally:
            # a diagnostic must leave no residue in the live store: a row left in
            # "transcribing" is picked up by the next start's recover() and
            # finished as an empty meeting.
            meetings.delete(mid)
        ok = row is not None and row["status"] == "transcribing" \
            and meetings.get(mid) is None
        print(f"meetings   : {'ok' if ok else 'FAIL'} (created, read back and "
              f"deleted {mid})")
    except meetings.MeetingsUnreadable as exc:
        print(f"meetings   : FAIL ({exc})")
    print(f"ollama     : {cleanup.status(cfg)}")
    if config.models_present(cfg):
        eng = Engine(cfg)
        eng._ready.wait(timeout=120)
        print(f"engine     : {eng.state}{' - ' + str(eng.error) if eng.error else ''}")
        if eng.state == "loaded":
            import numpy as np
            out = eng.transcribe(np.zeros(16000, dtype=np.float32))
            print(f"transcribe : ok (1 s silence -> {out!r})")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    _setup_logging()
    if not winutil.single_instance():
        print("Hemsa is already running.")
        return 1
    log = logging.getLogger("hemsa")
    log.info("starting v%s", __import__("hemsa").__version__)

    # BEFORE the root exists, both of them: GDI enumerates fonts when Tk starts,
    # so a font added later is invisible to every widget, and a process that
    # claims DPI awareness after Tk has read the screen is stretched anyway.
    from .ui import fonts as fonts_mod, scale as scale_mod
    scale_mod.set_dpi_aware()
    theme_mod.set_fonts(fonts_mod.load_private_fonts())

    root = tk.Tk()
    root.withdraw()
    scale_mod.init(root)

    # strict: an existing-but-unreadable config must NOT be mistaken for a first
    # run. Doing so silently resets every setting, offers to re-download the
    # model, and then save() writes the defaults back over the real file.
    try:
        cfg = config.load(strict=True)
    except config.ConfigUnreadable as exc:
        from tkinter import messagebox
        log.error("refusing to start with default settings: %s", exc)
        messagebox.showerror("Hemsa", (
            f"Your settings file could not be read:\n\n{config.CONFIG_PATH}\n\n"
            f"{exc}\n\nHemsa has NOT changed it. Close anything else using that "
            "folder and start Hemsa again."))
        return 1

    # which model folder is actually in use is the single most useful startup
    # fact: it decides whether the setup screen appears, and it is the one thing
    # that differs between "works on my machine" and a fresh install.
    log.info("models dir: %s", config.models_dir(cfg))

    # theme binds before any window exists, or the first paint flashes Plum
    cfg["theme"] = palette.set_theme(cfg.get("theme", palette.DEFAULT))

    # First run, or a model that went missing/truncated: set up before the App is
    # built. Engine loads at App construction and has no reload path, so it must
    # never be constructed while the model is absent.
    model_dir = config.models_dir(cfg)
    gaps = model_manifest.missing(model_dir)
    if not cfg.get("onboarded") or gaps:
        from .ui.onboarding import OnboardingWindow
        # log the WHY: a setup screen appearing on a working install is otherwise
        # impossible to diagnose after the fact (it happened once, 2026-08-23).
        log.info("first-run setup: onboarded=%s models_dir=%s missing=%s",
                 cfg.get("onboarded"), model_dir, [f.name for f in gaps])
        if not OnboardingWindow(root, cfg).run():
            log.info("setup closed before finishing - exiting")
            return 0
        cfg = config.load()
        cfg["theme"] = palette.set_theme(cfg.get("theme", palette.DEFAULT))

    # An install used to delete the Run value (Inno's deletevalue, fixed 2026-09-03),
    # so a machine upgraded by an older installer has autostart off while Settings
    # says on. Repair it here rather than leaving the toggle lying.
    try:
        if winutil.reconcile_autostart(cfg):
            log.info("autostart was missing from the Run key - restored")
    except OSError:
        log.exception("could not restore the autostart entry")

    App(root, cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
