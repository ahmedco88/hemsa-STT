"""The real App builds the real Shell with the real pages, with only the heavy
parts stubbed (engine, controller, recorder, hotkey hook, tray thread). This is
the one place the tray / orb entry points, the theme switch and the meetings
change notification meet the shell, and none of it is reachable from the page
tests. Runs against a temp data folder, never the real one."""

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def root(tk_root):
    """The session-wide interpreter (tests/conftest.py). Nothing is
    destroyed here: a fresh tk.Tk() after a destroy fails on Windows."""
    return tk_root


@pytest.fixture()
def app(root, monkeypatch, tmp_path):
    import hemsa.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    from hemsa import history, stats, dictionary
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(stats, "PATH", tmp_path / "stats.json")
    monkeypatch.setattr(dictionary, "PATH", tmp_path / "dictionary.json")

    import hemsa.__main__ as main_mod

    class Engine:
        state = "loaded"
        error = ""

        def __init__(self, cfg):
            pass

    class Recorder:
        level = 0.0

        def reopen(self):
            pass

    class Ctl:
        def __init__(self, cfg, engine, post):
            self._recorder = Recorder()
            self.state = "idle"
            self.last_text = ""
            self.on_state = None
            self.on_paste_risk = None

        def orb_click(self):
            pass

        def reload_dictionary(self):
            pass

        def hotkey_press(self):
            pass

        def hotkey_release(self):
            pass

    class Jobs:
        recording_id = None

        def __init__(self, cfg, engine, ctl, on_change):
            pass

        def recover(self):
            pass

    class Hotkey:
        def __init__(self, press, release):
            pass

        def bind(self, key):
            pass

        def unbind(self):
            pass

    class Tray:
        title = icon = None

        def run_detached(self):
            pass

        def update_menu(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(main_mod, "Engine", Engine)
    monkeypatch.setattr(main_mod.controller, "Controller", Ctl)
    monkeypatch.setattr(main_mod.meeting_jobs, "MeetingJobs", Jobs)
    monkeypatch.setattr(main_mod.hotkey_mod, "Hotkey", Hotkey)
    monkeypatch.setattr(main_mod.tray_mod, "build", lambda app: Tray())
    cfg = config.load()
    a = main_mod.App(root, cfg)
    yield a
    a.shell.win.destroy()


def test_every_entry_point_opens_a_page(app):
    app.open_home()
    assert app.shell.visible and app.shell.current == "home"
    app.open_meetings()
    assert app.shell.current == "meetings"
    app.open_dictionary()
    assert app.shell.current == "words"
    app.open_settings()
    assert app.shell.current == "settings"
    app.open_about()
    assert app.shell.current == "about"
    app.shell.hide()
    assert not app.shell.visible


def test_theme_switch_and_meetings_change_reach_the_shell(app):
    from hemsa import palette as P
    app.open_meetings()
    app.set_theme("navy")
    try:
        assert P.current() == "navy" and app.cfg["theme"] == "navy"
        app.jobs.recording_id = "abc"
        app._meetings_changed()
        assert app.shell._recording is True
        app.jobs.recording_id = None
        app._meetings_changed()
        assert app.shell._recording is False
    finally:
        app.set_theme(P.DEFAULT)
