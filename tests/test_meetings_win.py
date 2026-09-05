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


@pytest.fixture(scope="session")
def root(tk_root):
    """The session-wide interpreter (tests/conftest.py). Nothing is
    destroyed here: a fresh tk.Tk() after a destroy fails on Windows."""
    return tk_root


class FakeJobs:
    def __init__(self):
        self.recording_id = None
        self._retry_calls = []
        self._started = 0

    def retry_summary(self, mid):
        self._retry_calls.append(mid)

    def start_recording(self):
        self._started += 1


class FakeApp:
    def __init__(self):
        self.cfg = {"meeting_treatment": "ai",
                    "cleanup_model": "qwen3.5:2b",
                    "ollama_url": "http://localhost:11434"}
        self.jobs = FakeJobs()


@pytest.fixture()
def frame(root, store, monkeypatch):
    """cleanup.status is stubbed for every test in this module: it is an HTTP
    call, and a UI test that reaches localhost passes or fails on whether the
    developer happens to have Ollama running."""
    from hemsa.ui import meetings_win
    monkeypatch.setattr(meetings_win.cleanup, "status", lambda cfg: OLLAMA[0])
    OLLAMA[0] = "ready"
    app = FakeApp()
    f = meetings_win.MeetingsFrame(root, app)
    yield f
    f.destroy()


OLLAMA = ["ready"]          # what the stubbed cleanup.status returns


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


# --- the summariser needs Ollama, and silence about that is the bug ----------

def test_record_refuses_and_explains_when_ollama_is_down(frame):
    """Found out AFTER recording an hour is too late, so the check is at the
    press. Transcription would still work, so the message has to say that the
    SUMMARY is what is lost, not that recording is impossible."""
    OLLAMA[0] = "down"
    frame._toggle_record()
    assert frame._jobs()._started == 0
    msg = frame._msg.cget("text").lower()
    assert "ollama is not running" in msg
    assert "transcribed but not summarised" in msg


def test_record_proceeds_when_ollama_is_ready(frame):
    OLLAMA[0] = "ready"
    frame._toggle_record()
    assert frame._jobs()._started == 1


def test_record_is_not_blocked_when_no_summary_was_wanted(frame):
    """Transcript-only never calls the summariser, so a dead Ollama is not its
    problem and must not stop a recording."""
    frame._app.cfg["meeting_treatment"] = "transcript"
    OLLAMA[0] = "down"
    frame._toggle_record()
    assert frame._jobs()._started == 1
    assert "ollama" not in frame._msg.cget("text").lower()


def test_retry_summary_refuses_into_a_dead_ollama(frame, store):
    mid = store.create("import")
    frame._open_id = mid
    OLLAMA[0] = "down"
    frame._retry_summary()
    assert frame._jobs()._retry_calls == []
    assert "ollama is not running" in frame._msg.cget("text").lower()


def test_missing_model_names_the_pull_command(frame):
    OLLAMA[0] = "no model"
    frame._toggle_record()
    msg = frame._msg.cget("text")
    assert "ollama pull qwen3.5:2b" in msg


def test_finished_meeting_with_no_summary_says_why(frame, store):
    """"No summary for this meeting" reads as "there was nothing to say"."""
    mid = store.create("import")
    store.save_segments(mid, [{"start": 0.0, "end": 1.0, "channel": "me",
                               "text": "hello"}])
    store.set_status(mid, "done")
    frame._open_id = mid
    OLLAMA[0] = "down"
    frame._check_ollama()
    frame._render_detail()
    shown = frame._summary.get("1.0", "end").lower()
    assert "ollama was not running" in shown and "retry summary" in shown


def test_the_fix_row_appears_only_while_the_warning_is_true(frame):
    """Telling someone Ollama is down and leaving them to find it themselves is
    most of the annoyance of it being down."""
    OLLAMA[0] = "down"
    frame._check_ollama()
    assert frame._fix.winfo_manager()                    # packed
    assert frame._start_ollama.winfo_manager()

    OLLAMA[0] = "ready"
    frame._check_ollama()
    assert not frame._fix.winfo_manager()                # gone


def test_no_model_hides_start_but_keeps_check_again(frame):
    """The server is already up, so starting it again fixes nothing. The button
    that WOULD help pulls over a gigabyte, which is not a no-progress button."""
    OLLAMA[0] = "no model"
    frame._check_ollama()
    assert frame._fix.winfo_manager()
    assert frame._recheck.winfo_manager()
    assert not frame._start_ollama.winfo_manager()


def test_start_ollama_says_why_when_it_cannot(frame, monkeypatch):
    from hemsa.ui import meetings_win
    OLLAMA[0] = "down"
    frame._check_ollama()
    monkeypatch.setattr(meetings_win.cleanup, "start_server",
                        lambda: "Could not find Ollama on this PC.")
    frame._on_start_ollama()
    assert "could not find ollama" in frame._msg.cget("text").lower()


def test_start_ollama_confirms_once_the_server_answers(frame, monkeypatch):
    from hemsa.ui import meetings_win
    OLLAMA[0] = "down"
    frame._check_ollama()
    monkeypatch.setattr(meetings_win.cleanup, "start_server", lambda: "")
    OLLAMA[0] = "ready"                     # as if it came up straight away
    frame._on_start_ollama()
    assert "summaries are back on" in frame._msg.cget("text").lower()
    assert not frame._fix.winfo_manager()


def test_switching_to_transcript_only_clears_a_stale_ollama_warning(frame):
    """The warning stops being TRUE the moment nothing will ask for a summary.
    Leaving it up reads as "recording is broken", which it is not."""
    OLLAMA[0] = "down"
    frame._check_ollama()
    assert "ollama is not running" in frame._msg.cget("text").lower()

    from hemsa.ui import meetings_win
    frame._treat.set(meetings_win.TREATMENT_LABELS["fast"])
    frame._save_treatment()

    assert frame._msg.cget("text") == ""
    assert not frame._fix.winfo_manager()
