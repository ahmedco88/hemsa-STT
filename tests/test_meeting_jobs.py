import time
import pytest


@pytest.fixture()
def env(monkeypatch, tmp_path):
    import hemsa.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    from hemsa import meetings
    return meetings


class FakeController:
    state = "idle"


class FakeEngine:
    busy = False


def wait_done(meetings, mid, timeout=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        status = meetings.get(mid)["status"]
        if status in ("done", "error"):
            return status
        time.sleep(0.02)
    raise TimeoutError(status)


def test_import_pipeline_reaches_done(env, monkeypatch, tmp_path):
    meetings = env
    from hemsa import meeting_jobs
    monkeypatch.setattr(meeting_jobs.dictionary, "load", lambda: [])
    monkeypatch.setattr(meeting_jobs.importer, "to_wav",
                        lambda src, dest: (dest.write_bytes(b"RIFF"), 42.0)[1])
    monkeypatch.setattr(meeting_jobs.longform, "transcribe_wav",
                        lambda path, ch, eng, words, wait_idle:
                        [{"start": 0.0, "end": 4.0, "channel": ch, "text": "hi"}])
    monkeypatch.setattr(meeting_jobs.summarize, "summarize",
                        lambda segs, cfg: ("- talked", "- none"))
    changes = []
    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai"}, FakeEngine(),
                                    FakeController(), on_change=changes.append)
    src = tmp_path / "call.m4a"
    src.write_bytes(b"fake")
    mid = jobs.import_file(src)
    assert wait_done(meetings, mid) == "done"
    m = meetings.get(mid)
    assert m["summary"] == "- talked" and m["duration_s"] == 42.0
    assert m["segments"][0]["text"] == "hi"
    assert mid in changes


def test_summary_failure_is_done_with_retry_state(env, monkeypatch, tmp_path):
    meetings = env
    from hemsa import meeting_jobs
    monkeypatch.setattr(meeting_jobs.dictionary, "load", lambda: [])
    monkeypatch.setattr(meeting_jobs.importer, "to_wav",
                        lambda src, dest: (dest.write_bytes(b"RIFF"), 10.0)[1])
    monkeypatch.setattr(meeting_jobs.longform, "transcribe_wav",
                        lambda *a, **k: [{"start": 0, "end": 1,
                                          "channel": "me", "text": "hi"}])
    monkeypatch.setattr(meeting_jobs.summarize, "summarize", lambda s, c: None)
    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai"}, FakeEngine(),
                                    FakeController(), on_change=lambda mid: None)
    src = tmp_path / "x.mp3"
    src.write_bytes(b"fake")
    mid = jobs.import_file(src)
    assert wait_done(meetings, mid) == "done"
    m = meetings.get(mid)
    assert m["segments"] and m["summary"] == ""      # transcript kept, no summary


def test_an_empty_recording_never_calls_the_summariser(env, monkeypatch):
    """Nothing captured means nothing to summarise: waking Ollama for seconds to
    compress an empty transcript is pure cost."""
    meetings = env
    from hemsa import meeting_jobs
    calls = []
    monkeypatch.setattr(meeting_jobs.dictionary, "load", lambda: [])
    monkeypatch.setattr(meeting_jobs.summarize, "summarize",
                        lambda segs, cfg: calls.append(segs))
    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai"}, FakeEngine(),
                                    FakeController(), on_change=lambda mid: None)
    mid = meetings.create("record")              # no audio was ever written
    jobs.retry_summary(mid)
    assert wait_done(meetings, mid) == "done"
    assert calls == []


def test_recover_marks_crashed_recording_as_error(env):
    meetings = env
    from hemsa import meeting_jobs
    mid = meetings.create("record")                  # simulates a crash mid-meeting
    jobs = meeting_jobs.MeetingJobs({}, FakeEngine(), FakeController(),
                                    on_change=lambda m: None)
    jobs.recover()
    m = meetings.get(mid)
    assert m["status"] == "error" and "interrupted" in m["error"]


def test_import_failure_sets_error_and_cleans_up(env, monkeypatch, tmp_path):
    meetings = env
    from hemsa import meeting_jobs

    def _fail(src, dest):
        raise meeting_jobs.importer.ImportUnsupported(
            "Couldn't read x.mp3 as audio")

    monkeypatch.setattr(meeting_jobs.dictionary, "load", lambda: [])
    monkeypatch.setattr(meeting_jobs.importer, "to_wav", _fail)
    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai"}, FakeEngine(),
                                    FakeController(), on_change=lambda mid: None)
    src = tmp_path / "x.mp3"
    src.write_bytes(b"fake")
    mid = jobs.import_file(src)
    assert wait_done(meetings, mid) == "error"
    m = meetings.get(mid)
    assert "x.mp3" in m["error"]
    d = meetings.folder(mid)
    assert not (d / "import.wav").exists()
    assert not (d / "pending_import").exists()


class FakeRecorder:
    """Stands in for MeetingRecorder: writes a token WAV, reports an abort."""

    def __init__(self, cfg, dest):
        self.dest = dest
        self.error = None
        self.level = 0.0

    def start(self):
        self.dest.mkdir(parents=True, exist_ok=True)
        (self.dest / "me.wav").write_bytes(b"RIFF")

    def stop(self):
        return 12.0


def test_a_capture_abort_ends_in_error_with_the_audio_kept(env, monkeypatch):
    """Headset unplugged mid-call: the partial audio is kept AND transcribed, but
    the meeting must not sit there saying "Done" over half a call."""
    meetings = env
    from hemsa import meeting_jobs
    monkeypatch.setattr(meeting_jobs.meeting_audio, "MeetingRecorder", FakeRecorder)
    monkeypatch.setattr(meeting_jobs.dictionary, "load", lambda: [])
    monkeypatch.setattr(meeting_jobs.longform, "transcribe_wav",
                        lambda path, ch, eng, words, wait_idle:
                        [{"start": 0.0, "end": 1.0, "channel": ch,
                          "text": "half a call"}])
    monkeypatch.setattr(meeting_jobs.summarize, "summarize",
                        lambda segs, cfg: ("- half", "- none"))
    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai"}, FakeEngine(),
                                    FakeController(), on_change=lambda mid: None)
    mid = jobs.start_recording()
    jobs._recorder.error = "mic stream failed: device disappeared"
    jobs.stop_recording()

    assert wait_done(meetings, mid) == "error"
    m = meetings.get(mid)
    assert "stopped early" in m["error"] and "device disappeared" in m["error"]
    assert m["segments"][0]["text"] == "half a call"     # transcribed anyway
    assert m["duration_s"] == 12.0
    assert (meetings.folder(mid) / "me.wav").exists()    # audio never discarded


def test_a_clean_stop_still_reaches_done(env, monkeypatch):
    """The abort path must not make every recording end in error."""
    meetings = env
    from hemsa import meeting_jobs
    monkeypatch.setattr(meeting_jobs.meeting_audio, "MeetingRecorder", FakeRecorder)
    monkeypatch.setattr(meeting_jobs.dictionary, "load", lambda: [])
    monkeypatch.setattr(meeting_jobs.longform, "transcribe_wav",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0,
                                          "channel": "me", "text": "all of it"}])
    monkeypatch.setattr(meeting_jobs.summarize, "summarize", lambda s, c: None)
    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai"}, FakeEngine(),
                                    FakeController(), on_change=lambda mid: None)
    mid = jobs.start_recording()
    jobs.stop_recording()

    assert wait_done(meetings, mid) == "done"
    assert meetings.get(mid)["error"] == ""
