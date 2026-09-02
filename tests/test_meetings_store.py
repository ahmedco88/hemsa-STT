import pytest


@pytest.fixture()
def store(monkeypatch, tmp_path):
    import hemsa.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    import importlib
    import hemsa.meetings as meetings
    importlib.reload(meetings)          # rebind module-level DB path to tmp_path
    return meetings


def test_create_get_roundtrip(store):
    mid = store.create("record")
    m = store.get(mid)
    assert m["status"] == "recording" and m["source"] == "record"
    assert "Meeting" in m["title"]
    store.set_duration(mid, 61.5)
    store.save_segments(mid, [
        {"start": 0.0, "end": 5.0, "channel": "me", "text": "hello"},
        {"start": 5.0, "end": 9.0, "channel": "them", "text": "hi"}])
    store.save_summary(mid, "- greeted each other", "- nothing")
    store.set_status(mid, "done")
    m = store.get(mid)
    assert m["duration_s"] == 61.5 and len(m["segments"]) == 2
    assert m["segments"][0]["channel"] == "me"
    assert store.list_meetings()[0]["id"] == mid


def test_delete_removes_row_segments_and_folder(store):
    mid = store.create("import")
    d = store.folder(mid)
    d.mkdir(parents=True)
    (d / "import.wav").write_bytes(b"x")
    store.save_segments(mid, [{"start": 0, "end": 1, "channel": "me", "text": "t"}])
    store.delete(mid)
    assert store.list_meetings() == [] and not d.exists()


def test_unfinished_lists_interrupted_jobs(store):
    a = store.create("record")                 # recording (crashed mid-meeting)
    b = store.create("import")                 # transcribing
    c = store.create("import")
    store.set_status(c, "done")
    ids = {m["id"]: m["status"] for m in store.unfinished()}
    assert set(ids) == {a, b}


def test_corrupt_db_is_quarantined_not_recreated(store, tmp_path):
    store.connect().close()
    db = tmp_path / "meetings.db"
    db.write_bytes(b"this is not sqlite at all" * 10)
    with pytest.raises(store.MeetingsUnreadable):
        store.connect(strict=True)
    assert (tmp_path / "meetings.bad.db").exists()


def test_corrupt_db_quarantine_failure_raises_and_leaves_file_untouched(
        store, tmp_path, monkeypatch):
    store.connect().close()
    db = tmp_path / "meetings.db"
    corrupt = b"this is not sqlite at all" * 10
    db.write_bytes(corrupt)

    import pathlib

    def _boom(self, target):
        raise PermissionError("file in use")

    monkeypatch.setattr(pathlib.Path, "replace", _boom)

    with pytest.raises(store.MeetingsUnreadable):
        store.connect(strict=False)             # even non-strict must not recreate
    assert db.read_bytes() == corrupt            # untouched, never overwritten
    assert not (tmp_path / "meetings.bad.db").exists()


def test_corrupt_db_quarantine_success_yields_working_fresh_db(store, tmp_path):
    store.connect().close()
    db = tmp_path / "meetings.db"
    corrupt = b"this is not sqlite at all" * 10
    db.write_bytes(corrupt)

    con = store.connect(strict=False)
    con.close()

    bad = tmp_path / "meetings.bad.db"
    assert bad.exists() and bad.read_bytes() == corrupt

    mid = store.create("record")                 # fresh DB actually works
    assert store.get(mid)["status"] == "recording"


def test_delete_raises_rather_than_leaving_the_audio_behind(store, monkeypatch):
    """The confirm dialog promises the recording is gone from this PC. On Windows
    a WAV still open for transcription cannot be removed, and swallowing that used
    to leave the whole recording on disk under a meeting the user believed gone."""
    mid = store.create("record")
    d = store.folder(mid)
    d.mkdir(parents=True)
    (d / "me.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(store.shutil, "rmtree", lambda *a, **k: None)   # locked

    with pytest.raises(OSError):
        store.delete(mid)

    assert store.get(mid) is not None          # rows stay with the audio
    assert (d / "me.wav").exists()


def test_save_segments_drops_rows_for_a_meeting_that_is_gone(store):
    """The worker can still be transcribing when the meeting is deleted. Inserting
    then would leave orphan rows no screen can show and no delete can reach."""
    mid = store.create("import")
    store.delete(mid)

    store.save_segments(mid, [{"start": 0, "end": 1, "channel": "me",
                               "text": "orphan"}])

    con = store.connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM segments WHERE meeting_id=?",
                        (mid,)).fetchone()[0]
    finally:
        con.close()
    assert n == 0
