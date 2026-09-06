"""The three converted pages build against fakes and keep their one guarantee
each: the word list saves without closing anything, settings never touch the
real config, about lists the fonts it ships."""

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def root(tk_root):
    """The session-wide interpreter (tests/conftest.py). Nothing is
    destroyed here: a fresh tk.Tk() after a destroy fails on Windows."""
    return tk_root


@pytest.fixture()
def no_ollama(monkeypatch):
    """A UI test must never reach localhost: it would pass or fail on whether the
    developer happens to have Ollama running. Settings polls it from on_show now."""
    from hemsa import cleanup
    monkeypatch.setattr(cleanup, "probe", lambda cfg: ("down", []))


@pytest.fixture()
def data(monkeypatch, tmp_path):
    import hemsa.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    import hemsa.dictionary as d
    monkeypatch.setattr(d, "PATH", tmp_path / "dictionary.json")
    return tmp_path


def test_dictionary_page_saves_without_closing(root, data):
    from hemsa import dictionary
    from hemsa.ui.dictionary_win import DictionaryPage
    hits = []
    p = DictionaryPage(root, on_change=lambda: hits.append(1))
    p.on_show()
    p.text.delete("1.0", "end")
    p.text.insert("1.0", "Parakeet\n\nOllama\n")
    p._save()
    assert hits == [1] and dictionary.load() == ["Parakeet", "Ollama"]
    assert p.winfo_exists()
    p.on_show()                                      # Cancel path: reloads from disk
    assert p.text.get("1.0", "end").split() == ["Parakeet", "Ollama"]
    p.destroy()


def test_dictionary_page_unreadable_file_disables_save(root, data):
    from hemsa import palette as P
    from hemsa.ui.dictionary_win import DictionaryPage
    (data / "dictionary.json").write_text("{not json", encoding="utf-8")
    p = DictionaryPage(root, on_change=lambda: None)
    p.on_show()
    assert p.save_btn.fill() == P.PAPER                  # disabled
    assert "NOT changed" in p._status.cget("text")
    p.destroy()


class _Engine:
    state = "loaded"
    error = ""


class _App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.engine = _Engine()
        self.ctl = type("Ctl", (), {"_recorder": type("R", (), {"reopen": staticmethod(lambda: None)})()})()
        self.orb = type("Orb", (), {"show": staticmethod(lambda v: None)})()
        self.calls = []

    def rebind_hotkey(self):
        self.calls.append("rebind")

    def set_cleanup_mode(self, m):
        self.cfg["cleanup_mode"] = m

    def set_theme(self, n):
        self.calls.append(("theme", n))


def test_settings_page_builds_and_writes_only_the_temp_config(root, data, no_ollama):
    from hemsa import config
    from hemsa.ui.settings import SettingsPage
    app = _App(dict(config.DEFAULTS))
    p = SettingsPage(root, app)
    p.on_show()
    p.on_hide()
    p._set("sounds", False)
    assert config.load()["sounds"] is False
    assert (data / "config.json").exists()
    p.restyle()
    p.destroy()


def test_about_page_names_the_fonts(root, data):
    from hemsa import config
    from hemsa.ui.about import AboutPage
    p = AboutPage(root, _App(dict(config.DEFAULTS)))
    def labels(w):
        for c in w.winfo_children():
            if "label" in c.winfo_class().lower():
                yield c.cget("text")
            yield from labels(c)
    texts = list(labels(p))
    assert any("Instrument Serif" in str(t) for t in texts)
    p.destroy()


def test_the_model_selector_cannot_be_typed_into(root, data, no_ollama):
    """A typed model name is a model that silently does not exist: Full mode then
    falls back to raw paste with nothing on screen saying why. The control offers
    what Ollama actually has, and always keeps the configured model in the list
    even when Ollama is down - dropping it would rewrite the setting by itself."""
    from hemsa import config
    from hemsa.ui.settings import SettingsPage
    app = _App(dict(config.DEFAULTS))
    p = SettingsPage(root, app)
    p.on_show()
    p.on_hide()                                   # stop the poll this test started
    assert str(p.model_box.cget("state")) == "readonly"
    assert app.cfg["cleanup_model"] in p.model_box.cget("values")

    p._fill_models(["gemma3:4b", "qwen3.5:2b"])
    assert set(p.model_box.cget("values")) >= {"gemma3:4b", "qwen3.5:2b",
                                               app.cfg["cleanup_model"]}
    p.destroy()


def test_a_configured_model_that_is_not_installed_says_so(root, data):
    """A value left over from the old free-text box is kept, because dropping it
    would rewrite the setting by itself. Unlabelled it reads as a field that ignored
    you, so once we have actually seen the installed list it is marked. Picking a real
    model must store the BARE name, never the decorated label."""
    from hemsa import config
    from hemsa.ui.settings import SettingsPage
    app = _App({**config.DEFAULTS, "cleanup_model": "free text ?"})
    p = SettingsPage(root, app)

    p._fill_models(["gemma3:4b", "qwen3.5:2b"])
    assert p.model_var.get() == "free text ?   (not installed)"
    assert "free text ?   (not installed)" in p.model_box.cget("values")

    p.model_var.set("qwen3.5:2b")
    p._pick_model()
    assert app.cfg["cleanup_model"] == "qwen3.5:2b"
    assert config.load()["cleanup_model"] == "qwen3.5:2b"
    p.destroy()


def test_an_unreachable_ollama_does_not_accuse_the_model(root, data):
    """With the server down we do not KNOW what is installed, so the configured model
    is shown plainly. Saying "not installed" there would be a claim we cannot make."""
    from hemsa import config
    from hemsa.ui.settings import SettingsPage
    app = _App({**config.DEFAULTS, "cleanup_model": "qwen3.5:2b"})
    p = SettingsPage(root, app)
    p._fill_models([], known=False)
    assert p.model_var.get() == "qwen3.5:2b"
    p.destroy()


def test_refresh_status_never_stacks_a_second_poll_chain(root, data, no_ollama):
    """on_show and every model pick call _refresh_status directly. Without cancelling
    the pending tick first, each of those would leave ANOTHER 3 s loop hitting
    localhost for the rest of the session."""
    from hemsa import config
    from hemsa.ui.settings import SettingsPage
    p = SettingsPage(root, _App(dict(config.DEFAULTS)))
    p._poll_id = p.after(60000, lambda: None)
    stale = p._poll_id

    p._refresh_status()
    assert stale not in root.tk.call("after", "info")      # the old tick is gone
    assert p._poll_id is not None and p._poll_id != stale  # exactly one new one

    only = p._poll_id
    p.on_hide()
    assert p._poll_id is None
    assert only not in root.tk.call("after", "info")
    p.destroy()


def test_the_missing_model_hint_only_names_a_command_it_can_run():
    """"or run `ollama pull <x>`" is good advice for a real model name and a wild
    goose chase for a phrase somebody typed into the old free-text box."""
    from hemsa.ui.settings import SettingsPage
    real = SettingsPage._missing_hint("gemma3:4b", ["qwen3.5:2b", "qwen3:4b"])
    assert "ollama pull gemma3:4b" in real
    assert "2 models" in real

    junk = SettingsPage._missing_hint("free text ?", ["qwen3.5:2b"])
    assert "ollama pull" not in junk
    assert "is not a model Ollama has" in junk

    empty = SettingsPage._missing_hint("gemma3:4b", [])
    assert "no models pulled yet" in empty


def test_an_unchecked_model_says_it_can_answer_the_question(root, data):
    """gemma3:4b turned a dictated question about metformin into a dose on
    2026-09-06. The response guard rejects that shape now, but the person choosing
    the model is the one who should be told, so the hint says so and goes amber."""
    from hemsa import cleanup, palette as P
    from hemsa.ui.settings import SettingsPage
    warned = SettingsPage._ready_hint("gemma3:4b")
    assert "has not been checked" in warned
    assert "ANSWER a dictated question" in warned
    assert cleanup.CHECKED_MODELS[0] in warned
    # and the CHECKED branch is not silent either: grey-and-quiet beside
    # amber-and-warning would read as an endorsement of something never claimed
    checked = SettingsPage._ready_hint(cleanup.CHECKED_MODELS[0])
    assert "a single test, not a guarantee" in checked
    del P, root, data


def test_a_stale_dropdown_label_is_never_written_to_config(root, data):
    """Tk snapshots -values into the popdown when it is posted, so a refresh while
    the list is open leaves a stale label on screen. The decorated label is not a
    model name and must never reach config; the box goes back to what is configured."""
    from hemsa import config
    from hemsa.ui.settings import SettingsPage
    app = _App({**config.DEFAULTS, "cleanup_model": "qwen3.5:2b"})
    p = SettingsPage(root, app)
    p._fill_models(["qwen3.5:2b"])

    p.model_var.set("gemma3:4b   (not installed)")     # a label from a stale popdown
    p._pick_model()
    assert app.cfg["cleanup_model"] == "qwen3.5:2b"
    assert p.model_var.get() == "qwen3.5:2b"
    p.destroy()


def test_the_first_visit_to_settings_fills_the_ollama_row(root, data, monkeypatch):
    """The shell packs the page and calls on_show in the same breath, so the page is
    not mapped yet - and it is still not mapped after the next idle pass, because Tk
    runs idle callbacks BEFORE the map. Inferring "on screen" from winfo_ismapped()
    therefore skipped the whole status block on the first visit and left the row blank
    until the user navigated away and back. on_show/on_hide is what the shell already
    tells us, so that is what drives the poll."""
    from hemsa import cleanup, config
    from hemsa.ui.settings import SettingsPage
    monkeypatch.setattr(cleanup, "probe", lambda cfg: ("ready", ["qwen3.5:2b"]))
    p = SettingsPage(root, _App({**config.DEFAULTS, "cleanup_model": "qwen3.5:2b"}))
    p.pack(fill="both", expand=True)
    p.on_show()                                   # exactly what shell.show() does
    assert p.ollama_lbl.cget("text") == "Ollama, ready"
    assert p.model_box.cget("values") == ("qwen3.5:2b",)
    assert p._poll_id is not None

    p.on_hide()                                   # exactly what shell.show() does next
    assert p._poll_id is None
    p.pack_forget()
    p.destroy()
