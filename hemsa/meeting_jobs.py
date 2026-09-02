"""The meetings pipeline worker. One daemon thread walks each meeting through
transcribing -> summarising -> done, writing status to the store at every step so a
crash can never lose more than the step in flight. Dictation always wins: the
wait_idle hook passed to longform blocks between chunks while the controller is
mid-dictation or the engine is decoding.
"""

import logging
import queue
import shutil
import threading
import time
from pathlib import Path

from . import dictionary, importer, longform, meeting_audio, meetings, summarize

log = logging.getLogger("hemsa.meeting_jobs")

# Written beside the WAVs when capture aborted mid-recording; read back at the end
# of _run() so a partial meeting finishes in "error", not in "done".
CAPTURE_ERROR = "capture_error.txt"


class MeetingJobs:
    def __init__(self, cfg, engine, controller, on_change):
        self.cfg = cfg
        self.engine = engine
        self.controller = controller
        self.on_change = on_change
        self.recording_id: str | None = None
        self._recorder: meeting_audio.MeetingRecorder | None = None
        self._q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._worker, daemon=True,
                         name="meeting-jobs").start()

    # ---- public API (UI thread) ----
    def start_recording(self) -> str:
        mid = meetings.create("record")
        rec = meeting_audio.MeetingRecorder(self.cfg, meetings.folder(mid))
        try:
            rec.start()
        except Exception as exc:
            log.exception("meeting capture failed to start")
            meetings.set_status(mid, "error", f"couldn't start capture: {exc}")
            self.on_change(mid)
            raise
        self._recorder, self.recording_id = rec, mid
        self.on_change(mid)
        return mid

    def stop_recording(self) -> None:
        rec, mid = self._recorder, self.recording_id
        self._recorder = self.recording_id = None
        if rec is None or mid is None:
            return
        seconds = rec.stop()
        meetings.set_duration(mid, seconds)
        if rec.error:
            # A stream aborted mid-meeting (headset unplugged, format change), so
            # what is on disk is a PARTIAL call. It still gets transcribed - never
            # lose captured audio - but the meeting must not end up saying "Done"
            # over half a call, so the reason is parked next to the audio and read
            # back at the end of _run(). A file, not an attribute: it has to
            # survive a crash and the next start's recover().
            d = meetings.folder(mid)
            d.mkdir(parents=True, exist_ok=True)
            (d / CAPTURE_ERROR).write_text(
                f"Recording stopped early: {rec.error}. What was captured up to "
                "that point has been kept.", encoding="utf-8")
        meetings.set_status(mid, "transcribing")
        self.on_change(mid)
        self._q.put(mid)

    def import_file(self, src: Path) -> str:
        mid = meetings.create("import")
        d = meetings.folder(mid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "source_name.txt").write_text(Path(src).name, encoding="utf-8")
        (d / "pending_import").write_text(str(src), encoding="utf-8")
        self.on_change(mid)
        self._q.put(mid)
        return mid

    def retry_summary(self, mid: str) -> None:
        meetings.set_status(mid, "summarising")
        self.on_change(mid)
        self._q.put(mid)

    def recover(self) -> None:
        for m in meetings.unfinished():
            if m["status"] == "recording":
                meetings.set_status(m["id"], "error",
                                    "interrupted - audio kept")
                self.on_change(m["id"])
            else:
                self._q.put(m["id"])

    # ---- worker thread ----
    def _wait_idle(self) -> None:
        while self.controller.state != "idle" or self.engine.busy:
            time.sleep(0.2)

    def _worker(self) -> None:
        while True:
            mid = self._q.get()
            try:
                self._run(mid)
            except Exception as exc:
                log.exception("meeting %s failed", mid)
                meetings.set_status(mid, "error", str(exc))
            self.on_change(mid)

    def _run(self, mid: str) -> None:
        m = meetings.get(mid)
        if m is None:
            return
        d = meetings.folder(mid)
        pending = d / "pending_import"
        if pending.exists():
            src = Path(pending.read_text(encoding="utf-8"))
            try:
                seconds = importer.to_wav(src, d / "import.wav")
            except importer.ImportUnsupported as exc:
                (d / "import.wav").unlink(missing_ok=True)
                pending.unlink(missing_ok=True)
                meetings.set_status(mid, "error", str(exc))
                return
            meetings.set_duration(mid, seconds)
            pending.unlink()
        if not m["segments"]:
            meetings.set_status(mid, "transcribing")
            self.on_change(mid)
            words = dictionary.load()
            segs = []
            for name, channel in (("me.wav", "me"), ("them.wav", "them"),
                                  ("import.wav", "me")):
                path = d / name
                if path.exists():
                    segs.extend(longform.transcribe_wav(
                        path, channel, self.engine, words, self._wait_idle))
            segs = longform.merge(segs)
            meetings.save_segments(mid, segs)
            m = meetings.get(mid)
        # no segments = nothing was said (or nothing was captured). Summarising an
        # empty transcript wakes Ollama for seconds to produce nothing.
        if (self.cfg.get("meeting_treatment", "ai") == "ai" and m["segments"]
                and not m["summary"]):
            meetings.set_status(mid, "summarising")
            self.on_change(mid)
            result = summarize.summarize(m["segments"], self.cfg)
            if result is not None:
                meetings.save_summary(mid, *result)
        # Everything that could be salvaged has been: transcript and summary are
        # saved. Only now does a mid-recording capture failure decide the status.
        aborted = d / CAPTURE_ERROR
        if aborted.exists():
            meetings.set_status(mid, "error", aborted.read_text(encoding="utf-8"))
            return
        meetings.set_status(mid, "done")
