"""Home page helpers (pure) and one build-and-refresh smoke against a seeded
temporary data folder."""

import json
import tkinter as tk
from datetime import datetime, timedelta

import pytest

from hemsa.ui import home


def test_greeting_by_hour():
    assert home.greeting(6) == "Good morning."
    assert home.greeting(13) == "Good afternoon."
    assert home.greeting(19) == "Good evening."


def test_group_by_day_labels():
    now = datetime(2026, 9, 3, 15, 0).astimezone()
    items = [{"iso": now.isoformat(), "text": "a"},
             {"iso": (now - timedelta(days=1)).isoformat(), "text": "b"},
             {"iso": (now - timedelta(days=3)).isoformat(), "text": "c"},
             {"ts": "garbage", "text": "d"}]
    groups = home.group_by_day(items, now)
    assert [g[0] for g in groups] == ["Today", "Yesterday", "Mon 31 Aug", "Undated"]
    assert [len(g[1]) for g in groups] == [1, 1, 1, 1]


def test_ring_and_faster_label():
    assert home.ring_fraction(80) == 0.5
    assert home.ring_fraction(400) == 1.0
    assert home.faster_label(119) == "3x faster than typing"
    assert home.faster_label(0) == "not enough yet"


def test_compact_numbers():
    assert home.compact(412) == "412"
    assert home.compact(1180) == "1.2K"
    assert home.compact(15204) == "15.2K"


def test_wpm_needs_thirty_seconds_of_audio():
    assert home.wpm_of({"words": 100, "audio_s": 10}) == 0
    assert home.wpm_of({"words": 100, "audio_s": 60}) == 100


@pytest.fixture(scope="session")
def root(tk_root):
    """The session-wide interpreter (tests/conftest.py). Nothing is
    destroyed here: a fresh tk.Tk() after a destroy fails on Windows."""
    return tk_root


def test_page_builds_and_refreshes_from_seeded_data(root, tmp_path, monkeypatch):
    from hemsa import history, stats
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(stats, "PATH", tmp_path / "stats.json")
    now = datetime.now().astimezone()
    # four entries, three of which must survive: the stale one is the point
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": now.isoformat(), "text": "one"},
        {"iso": (now - timedelta(hours=20)).isoformat(), "text": "two"},
        {"iso": (now - timedelta(days=3)).isoformat(), "text": "kept", "star": True},
        {"iso": (now - timedelta(days=3)).isoformat(), "text": "stale"}]),
        encoding="utf-8")
    (tmp_path / "stats.json").write_text(json.dumps({"days": {
        now.date().isoformat(): {"n": 2, "words": 300, "audio_s": 120.0, "proc_ms": 5}}}),
        encoding="utf-8")
    page = home.HomePage(root, app=None)
    page.on_show()
    assert page.words_num.cget("text") == "300"
    assert page.wpm_num.cget("text") == "150"
    assert page.saved_sub.cget("text") == "1 of the last 7 days"
    assert len(page._texts) == 3                      # "stale" is gone, "kept" is not
    assert [lbl.cget("text") for lbl in page._texts] == ["one", "two", "kept"]
    page.restyle()
    page.destroy()


def test_starring_a_row_persists_and_survives_a_refresh(root, tmp_path, monkeypatch):
    """The star is the only escape from the 24 h rule, so it has to reach DISK -
    an in-memory tick would look right and lose the dictation the next morning."""
    from hemsa import history, stats
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(stats, "PATH", tmp_path / "stats.json")
    now = datetime.now().astimezone()
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": now.isoformat(), "text": "pin me"}]), encoding="utf-8")

    page = home.HomePage(root, app=None)
    page.on_show()
    entry = page._items[0]
    assert "star" not in entry
    page._set_star(entry, True)                       # what the row's star calls
    assert json.loads((tmp_path / "history.json").read_text())[0]["star"] is True
    assert entry["star"] is True                      # in-memory copy agrees

    # and the entry outlives the cutoff: age both past it and reload
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": (now - timedelta(days=9)).isoformat(), "text": "pin me", "star": True},
        {"iso": (now - timedelta(days=9)).isoformat(), "text": "let me go"}]),
        encoding="utf-8")
    page.on_show()
    assert [lbl.cget("text") for lbl in page._texts] == ["pin me"]
    page.destroy()


def test_deleting_one_row_reaches_disk_and_leaves_the_rest(root, tmp_path, monkeypatch):
    """A row the user deleted has to be gone after a restart, not just repainted.
    The starred neighbour is the one worth watching: star is the only escape from
    the 24 h rule, so taking it out with a sibling loses something deliberate."""
    from hemsa import history, stats
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(stats, "PATH", tmp_path / "stats.json")
    now = datetime.now().astimezone()
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": now.isoformat(), "text": "bin me"},
        {"iso": now.isoformat(), "text": "keep me", "star": True}]), encoding="utf-8")

    page = home.HomePage(root, app=None)
    page.on_show()
    assert [lbl.cget("text") for lbl in page._texts] == ["bin me", "keep me"]

    page._delete(page._items[0])               # what the row's Delete pill calls

    assert [lbl.cget("text") for lbl in page._texts] == ["keep me"]
    on_disk = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert [i["text"] for i in on_disk] == ["keep me"]
    assert "deleted" in page._status.cget("text").lower()
    page.destroy()


def test_delete_that_cannot_be_written_says_so_and_keeps_the_row(root, tmp_path,
                                                                 monkeypatch):
    """Silently failing would show the row gone and bring it back on restart."""
    from hemsa import history, stats
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(stats, "PATH", tmp_path / "stats.json")
    now = datetime.now().astimezone()
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": now.isoformat(), "text": "still here"}]), encoding="utf-8")

    page = home.HomePage(root, app=None)
    page.on_show()

    def _readonly(entry):
        raise OSError("read-only file system")

    monkeypatch.setattr(home.history, "delete", _readonly)
    page._delete(page._items[0])

    assert [lbl.cget("text") for lbl in page._texts] == ["still here"]
    assert "not writable" in page._status.cget("text").lower()
    page.destroy()
