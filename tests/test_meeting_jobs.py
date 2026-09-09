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
                        lambda path, ch, eng, words, wait_idle, on_progress=None:
                        [{"start": 0.0, "end": 4.0, "channel": ch, "text": "hi"}])
    monkeypatch.setattr(meeting_jobs.summarize, "summarize",
                        lambda segs, cfg, labelled=True: ("- talked", "- none"))
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
    monkeypatch.setattr(meeting_jobs.summarize, "summarize", lambda s, c, labelled=True: None)
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
                        lambda segs, cfg, labelled=True: calls.append(segs))
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
                        lambda path, ch, eng, words, wait_idle, on_progress=None:
                        [{"start": 0.0, "end": 1.0, "channel": ch,
                          "text": "half a call"}])
    monkeypatch.setattr(meeting_jobs.summarize, "summarize",
                        lambda segs, cfg, labelled=True: ("- half", "- none"))
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
    monkeypatch.setattr(meeting_jobs.summarize, "summarize", lambda s, c, labelled=True: None)
    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai"}, FakeEngine(),
                                    FakeController(), on_change=lambda mid: None)
    mid = jobs.start_recording()
    jobs.stop_recording()

    assert wait_done(meetings, mid) == "done"
    assert meetings.get(mid)["error"] == ""


def test_mic_only_stamps_the_source_and_drops_the_speaker_labels(env, monkeypatch):
    """The source is stamped on the meeting at start, and a mic-only meeting has
    no Me/Them split: every segment is on channel "me", so labelling it "Me:"
    would hand one speaker the whole room - in the transcript AND in the text
    the summariser reads."""
    meetings = env
    from hemsa import meeting_jobs
    from hemsa.ui.meetings_win import transcript_text

    class FakeRecorder:
        error = None

        def __init__(self, cfg, dest):
            self.dest = dest

        def start(self): pass
        def stop(self): return 3.0

    monkeypatch.setattr(meeting_jobs.meeting_audio, "MeetingRecorder", FakeRecorder)
    monkeypatch.setattr(meeting_jobs.dictionary, "load", lambda: [])
    monkeypatch.setattr(meeting_jobs.longform, "transcribe_wav",
                        lambda *a, **k: [])
    seen = {}

    def _summarize(segs, cfg, labelled=True):
        seen["labelled"] = labelled
        return ("- talked", "- none")

    monkeypatch.setattr(meeting_jobs.summarize, "summarize", _summarize)

    jobs = meeting_jobs.MeetingJobs({"meeting_treatment": "ai",
                                     "meeting_source": "mic"},
                                    FakeEngine(), FakeController(),
                                    on_change=lambda mid: None)
    mid = jobs.start_recording()
    assert meetings.get(mid)["source"] == "mic"
    assert meetings.get(mid)["status"] == "recording"   # not "transcribing"
    jobs.stop_recording()
    assert wait_done(meetings, mid) == "done"

    # nothing was captured by the fake, so give it a transcript and run the
    # summary leg on its own - that is the call whose labelling matters
    meetings.save_segments(mid, [{"start": 0.0, "end": 2.0, "channel": "me",
                                  "text": "how are you"}])
    jobs.retry_summary(mid)
    assert wait_done(meetings, mid) == "done"
    assert seen["labelled"] is False

    m = dict(meetings.get(mid))
    assert "Me:" not in transcript_text(m)
    assert "how are you" in transcript_text(m)

    m["source"] = "record"
    assert "Me:" in transcript_text(m)
