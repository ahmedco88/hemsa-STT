"""MeetingsFrame guards: deleting a live recording, and store errors surfacing
from the rename/delete/retry callbacks instead of escaping a Tk callback where
nothing would ever show them (see task-9-report.md, Fix round 1).

A real (withdrawn) Tk root is used - this repo has no UI test harness, but these
three paths are pure "does the callback blow up / does it refuse correctly"
checks, not visual ones.
"""

import tkinter as tk

import pytest


@pytest.fixture()
def store(monkeypatch, tmp_path):
    import hemsa.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    from hemsa import meetings
    return meetings


@pytest.fixture(scope="module")
def root():
    """One Tk interpreter for the whole module - creating a fresh tk.Tk() per
    test crashes on Windows ("invalid command name tcl_findLibrary") once the
    previous one has been destroyed."""
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


class FakeJobs:
    def __init__(self):
        self.recording_id = None
        self._retry_calls = []

    def retry_summary(self, mid):
        self._retry_calls.append(mid)


class FakeApp:
    def __init__(self):
        self.cfg = {"meeting_treatment": "ai"}
        self.jobs = FakeJobs()


@pytest.fixture()
def frame(root, store):
    from hemsa.ui import meetings_win
    app = FakeApp()
    f = meetings_win.MeetingsFrame(root, app)
    yield f
    f.destroy()


def test_delete_refuses_a_live_recording(frame, store, monkeypatch):
    mid = store.create("record")
    frame._jobs().recording_id = mid          # capture is still writing this one
    frame._open_id = mid

    def _must_not_confirm(*a, **k):
        raise AssertionError("delete must refuse before asking to confirm")
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", _must_not_confirm)

    frame._delete.invoke()

    assert store.get(mid) is not None          # row survives
    assert "recording" in frame._msg.cget("text").lower()


def test_delete_of_a_finished_meeting_still_works(frame, store, monkeypatch):
    """Sanity check the guard is scoped to the live recording only."""
    mid = store.create("import")
    store.set_status(mid, "done")
    frame._open_id = mid
    frame._jobs().recording_id = None
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)

    frame._delete.invoke()

    assert store.get(mid) is None


def test_rename_store_error_surfaces_via_say(frame, store, monkeypatch):
    mid = store.create("import")
    frame._open_id = mid
    from tkinter import simpledialog
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: "New title")

    def _boom(mid, title):
        raise OSError("db is locked")
    monkeypatch.setattr(store, "rename", _boom)

    frame._rename()                            # must not raise

    assert "rename" in frame._msg.cget("text").lower()
    assert store.get(mid)["title"] != "New title"


def test_delete_store_error_surfaces_via_say(frame, store, monkeypatch):
    mid = store.create("import")
    store.set_status(mid, "done")              # past the busy guard, so delete runs
    frame._open_id = mid
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)

    def _boom(mid):
        raise OSError("db is locked")
    monkeypatch.setattr(store, "delete", _boom)

    frame._delete.invoke()                            # must not raise

    assert "delete" in frame._msg.cget("text").lower()
    assert store.get(mid) is not None


def test_retry_summary_store_error_surfaces_via_say(frame, store):
    mid = store.create("import")
    frame._open_id = mid

    def _boom(mid):
        raise OSError("db is locked")
    frame._jobs().retry_summary = _boom

    frame._retry_summary()                     # must not raise

    assert "retry" in frame._msg.cget("text").lower()


def test_rename_unreadable_store_shows_unreadable_message(frame, store, monkeypatch):
    mid = store.create("import")
    frame._open_id = mid
    from tkinter import simpledialog
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: "New title")

    def _unreadable(mid, title):
        raise store.MeetingsUnreadable("meetings.db unreadable")
    monkeypatch.setattr(store, "rename", _unreadable)

    frame._rename()                            # must not raise

    assert "could not be read" in frame._empty.cget("text").lower()


def test_delete_refuses_a_meeting_the_worker_is_still_processing(frame, store,
                                                                 monkeypatch):
    """Not recording, but the worker holds me.wav open: rmtree fails on Windows
    and save_segments would write rows back after the delete."""
    mid = store.create("import")               # created in "transcribing"
    frame._jobs().recording_id = None
    frame._open_id = mid

    def _must_not_confirm(*a, **k):
        raise AssertionError("delete must refuse before asking to confirm")
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", _must_not_confirm)

    frame._delete.invoke()

    assert store.get(mid) is not None
    assert "processed" in frame._msg.cget("text").lower()
