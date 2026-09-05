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


def test_settings_page_builds_and_writes_only_the_temp_config(root, data):
    from hemsa import config
    from hemsa.ui.settings import SettingsPage
    app = _App(dict(config.DEFAULTS))
    p = SettingsPage(root, app)
    p.on_show()
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
