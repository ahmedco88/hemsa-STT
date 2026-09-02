"""Meetings window - record a call, import a file, read the transcript.

The whole UI is a Frame (MeetingsFrame) inside a thin Toplevel, so Phase 2 can
rehouse it in the Home window unchanged. Two views live in that frame and swap by
pack/pack_forget: the list of meetings, and one meeting's detail.

Nothing here runs on the worker thread. MeetingJobs.on_change fires from BOTH the
UI thread and the job thread, so App wraps it in post() and this window only ever
refreshes on the Tk main thread (see __main__.App._meetings_changed).

Cards are plain tk widgets, exactly as history_win does it: each row needs its own
background for hover, which ttk styles cannot give per widget. Colours are therefore
applied by hand and restyle() re-does them after a live theme switch.

An unreadable store is NOT an empty one (meetings.MeetingsUnreadable): the window
says so and stops, rather than showing "no meetings yet" over a database that is
still sitting there.
"""

import logging
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pyperclip

from .. import config, history, meetings, palette as P, winutil
from . import theme

log = logging.getLogger("hemsa.meetings_win")

W, H = 780, 640
FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 8)
FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 9)
ROW_GAP = 8
# Statuses where the worker still owns the meeting's folder and rows.
BUSY_STATUSES = ("recording", "transcribing", "summarising")

# Not etiquette. Hemsa captures the OTHER side of the call through WASAPI
# loopback, and in several Australian states recording a private conversation
# without every party's consent is an offence, whether or not you are in it.
# "Remember to tell them" reads as optional, and telling is not consent.
COURTESY = "Recording is silent - get everyone's consent before you record."
EMPTY = "No meetings yet. Press Record, or import a file."

# config value -> the words a human reads in the dropdown
TREATMENTS = (("ai", "Transcript + summary"), ("fast", "Transcript only"))
TREATMENT_LABELS = dict(TREATMENTS)

AUDIO_TYPES = [
    ("Audio/video", "*.m4a *.mp4 *.mp3 *.wav *.flac *.ogg *.opus *.webm"),
    ("All files", "*.*"),
]

STATUS_LABELS = {"recording": "Recording", "transcribing": "Transcribing",
                 "summarising": "Summarising", "done": "Done", "error": "Error"}
BUSY = ("recording", "transcribing", "summarising")


def _clock(seconds) -> str:
    """[MM:SS], and minutes keep counting past an hour - [75:30] is unambiguous
    where [15:30] on a 75 minute call is not."""
    total = int(seconds or 0)
    return f"[{total // 60:02d}:{total % 60:02d}]"


def _minutes(seconds) -> str:
    return f"{round((seconds or 0) / 60)} min"


def transcript_text(meeting: dict) -> str:
    """The transcript as it is shown and copied. Imports have no Me/Them split
    (one decoded file, every segment on one channel), so the speaker label is
    dropped entirely rather than labelling everything "Me"."""
    labelled = meeting.get("source") != "import"
    lines = []
    for seg in meeting.get("segments", []):
        who = "Me" if seg.get("channel") == "me" else "Them"
        head = f"{_clock(seg.get('start'))} {who}: " if labelled \
            else f"{_clock(seg.get('start'))} "
        lines.append(head + (seg.get("text") or ""))
    return "\n".join(lines)


class MeetingsFrame(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent)
        self._app = app
        self._rows: list[tuple[tk.Widget, ...]] = []
        self._items: list[dict] = []
        self._open_id: str | None = None
        self._build_card()
        self._build_list()
        self._build_detail()
        self._show_list()
        self.restyle()                 # restyle() ends in a refresh()
        self._tick()

    def _jobs(self):
        return getattr(self._app, "jobs", None)

    # ---- record card ----
    def _build_card(self) -> None:
        self._card = tk.Frame(self)
        self._card.pack(fill="x", padx=16, pady=(14, 0))
        row = self._card_row = tk.Frame(self._card)
        row.pack(fill="x", padx=12, pady=12)

        self._rec = tk.Button(row, text="Record", font=FONT, relief="flat", bd=0,
                              cursor="hand2", padx=20, pady=7, highlightthickness=0,
                              command=self._toggle_record)
        self._rec.pack(side="left")

        self._dot = tk.Canvas(row, width=18, height=18, highlightthickness=0, bd=0)
        self._dot_id = self._dot.create_oval(6, 6, 12, 12, width=0)
        self._dot.pack(side="left", padx=(10, 12))

        self._chips = []
        for text in ("Microphone", "System audio"):
            chip = tk.Label(row, text=text, font=FONT_SMALL, padx=9, pady=4)
            chip.pack(side="left", padx=(0, 6))
            self._chips.append(chip)

        current = self._app.cfg.get("meeting_treatment", "ai")
        self._treat = tk.StringVar(
            value=TREATMENT_LABELS.get(current, TREATMENT_LABELS["ai"]))
        combo = ttk.Combobox(row, textvariable=self._treat, state="readonly",
                             width=19, values=[label for _, label in TREATMENTS])
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", lambda e: self._save_treatment())
        ttk.Button(row, text="Import audio…", command=self._import_file).pack(
            side="right", padx=(0, 8))

        self._note = tk.Label(self, text=COURTESY, font=FONT_SMALL, anchor="w")
        self._note.pack(fill="x", padx=18, pady=(6, 0))
        self._msg = tk.Label(self, text="", font=FONT_SMALL, anchor="w",
                             justify="left", wraplength=W - 40)
        self._msg.pack(fill="x", padx=18, pady=(2, 0))

    def _save_treatment(self) -> None:
        for key, label in TREATMENTS:
            if label == self._treat.get():
                self._app.cfg["meeting_treatment"] = key
                config.save(self._app.cfg)
                return

    def _toggle_record(self) -> None:
        jobs = self._jobs()
        if jobs is None:
            return
        stopping = bool(jobs.recording_id)
        try:
            self._say("")
            if stopping:
                jobs.stop_recording()
            else:
                jobs.start_recording()
        except Exception as exc:                       # capture device, or the store
            log.exception("record toggle failed")
            verb = "stop" if stopping else "start"
            self._say(f"Could not {verb} recording: {exc}", bad=True)
        self.refresh()

    def _import_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(),
                                          title="Import audio", filetypes=AUDIO_TYPES)
        if not path or self._jobs() is None:
            return
        try:
            self._jobs().import_file(Path(path))
        except Exception as exc:
            log.exception("import failed to queue")
            self._say(f"Could not import that file: {exc}", bad=True)
        self.refresh()

    def _say(self, text: str, bad: bool = False) -> None:
        self._msg.configure(text=text, fg=P.DANGER if bad else P.MUTED)

    # ---- level dot ----
    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        jobs = self._jobs()
        rec = getattr(jobs, "_recorder", None)
        level = 0.0
        if jobs is not None and jobs.recording_id and rec is not None:
            try:
                level = float(rec.level or 0.0)
            except (TypeError, ValueError):
                level = 0.0
        self._paint_dot(level)
        self.after(200, self._tick)

    def _paint_dot(self, level: float) -> None:
        jobs = self._jobs()
        recording = bool(jobs is not None and jobs.recording_id)
        r = 3 + (min(1.0, level * 12) * 4 if recording else 0)
        self._dot.coords(self._dot_id, 9 - r, 9 - r, 9 + r, 9 + r)
        self._dot.itemconfigure(self._dot_id, fill=P.REC if recording else P.LINE)

    # ---- list view ----
    def _build_list(self) -> None:
        self._list = tk.Frame(self)
        self._canvas = tk.Canvas(self._list, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(self._list, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=bar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self._rows_frame = tk.Frame(self._canvas)
        self._window_id = self._canvas.create_window((0, 0), window=self._rows_frame,
                                                     anchor="nw", width=W - 44)
        self._rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._window_id, width=e.width))
        # bound on the toplevel, not per card: Tk does not propagate an event to
        # intermediate frames, so a card added later would otherwise be dead. The
        # handler no-ops unless the list is the view on screen, so the detail
        # Texts keep their own wheel behaviour.
        self.winfo_toplevel().bind("<MouseWheel>", self._on_wheel, add="+")
        self._empty = tk.Label(self._rows_frame, font=FONT, anchor="w", text=EMPTY)

    def _on_wheel(self, e) -> None:
        # winfo_exists first: the binding lives on the toplevel, which in Phase 2
        # will outlive this frame.
        if self.winfo_exists() and self._list.winfo_ismapped():
            self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _build_rows(self) -> None:
        for widgets in self._rows:
            widgets[0].destroy()
        self._rows = []
        self._empty.pack_forget()
        if not self._items:
            self._empty.pack(fill="x", pady=16)
            return
        now = datetime.now().astimezone()
        for i, m in enumerate(self._items):
            self._rows.append(self._make_row(m, now, first=i == 0))

    def _make_row(self, m: dict, now: datetime, first: bool):
        row = tk.Frame(self._rows_frame, cursor="hand2")
        inner = tk.Frame(row)
        inner.pack(fill="both", expand=True, padx=12, pady=9)
        title = tk.Label(inner, text=m["title"], font=FONT, anchor="w")
        title.pack(fill="x")
        when = tk.Label(
            inner, font=FONT_SMALL, anchor="w",
            text=f"{history.relative({'iso': m['created_iso']}, now)}"
                 f"  ·  {_minutes(m['duration_s'])}")
        when.pack(fill="x", pady=(3, 0))
        pill = tk.Label(row, text=STATUS_LABELS.get(m["status"], m["status"]),
                        font=FONT_SMALL, padx=9, pady=3)
        pill.place(relx=1.0, rely=0.5, anchor="e", x=-12)
        row.pack(fill="x", pady=(0 if first else ROW_GAP, 0))

        widgets = (row, inner, title, when, pill)

        def paint(colour: str) -> None:
            for w in widgets:
                w.configure(bg=colour)

        paint(P.CARD)
        title.configure(fg=P.INK)
        when.configure(fg=P.MUTED)
        pill.configure(fg=self._status_colour(m["status"]))
        for w in widgets:
            w.bind("<Enter>", lambda e: paint(P.MIST))
            w.bind("<Leave>", lambda e: paint(P.CARD))
            w.bind("<ButtonRelease-1>", lambda e, mid=m["id"]: self._open_detail(mid))
        return widgets

    def _status_colour(self, status: str) -> str:
        if status == "error":
            return P.DANGER
        return P.ACCENT if status in BUSY else P.MUTED

    # ---- detail view ----
    def _build_detail(self) -> None:
        self._detail = tk.Frame(self)
        top = self._detail_top = tk.Frame(self._detail)
        top.pack(fill="x")
        ttk.Button(top, text="Back", command=self._show_list).pack(side="left")
        self._title = tk.Label(top, font=FONT_TITLE, anchor="w", cursor="hand2")
        self._title.pack(side="left", padx=10)
        self._title.bind("<Double-Button-1>", lambda e: self._rename())
        self._meta = tk.Label(top, font=FONT_SMALL, anchor="e")
        self._meta.pack(side="right")

        cols = self._cols = tk.Frame(self._detail)
        cols.pack(fill="both", expand=True, pady=(10, 0))
        left = self._left = tk.Frame(cols)
        right = self._right = tk.Frame(cols)
        left.pack(side="left", fill="both", expand=True)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self._sum_head = tk.Label(left, text="Summary and actions", font=FONT_SMALL,
                                  anchor="w")
        self._sum_head.pack(fill="x", pady=(0, 4))
        self._summary = self._text_box(left)
        self._tr_head = tk.Label(right, text="Transcript", font=FONT_SMALL, anchor="w")
        self._tr_head.pack(fill="x", pady=(0, 4))
        self._transcript = self._text_box(right)

        bar = self._detail_bar = tk.Frame(self._detail)
        bar.pack(fill="x", pady=(10, 0))
        self._copy_sum = ttk.Button(bar, text="Copy summary",
                                    command=lambda: self._copy(self._summary_text()))
        self._copy_sum.pack(side="left")
        self._copy_tr = ttk.Button(bar, text="Copy transcript",
                                   command=lambda: self._copy(self._transcript_text()))
        self._copy_tr.pack(side="left", padx=6)
        self._retry = ttk.Button(bar, text="Retry summary", command=self._retry_summary)
        self._folder = ttk.Button(bar, text="Open folder", command=self._open_folder)
        self._folder.pack(side="left", padx=6)
        self._delete = ttk.Button(bar, text="Delete", command=self._delete)
        self._delete.pack(side="right")

    def _text_box(self, parent: tk.Widget) -> tk.Text:
        box = tk.Frame(parent)
        box.pack(fill="both", expand=True)
        # width/height in CHARACTERS, and deliberately small: a tk.Text defaults to
        # 80x24, and two of those side by side ask for ~1200 px, which grows the
        # whole window the first time a meeting is opened. Both boxes expand to fill.
        text = tk.Text(box, wrap="word", width=30, height=10, relief="solid",
                       borderwidth=1)
        scroll = tk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        return text

    def _write(self, widget: tk.Text, lines) -> None:
        """lines: [(text, tag or None), ...]. The box is read-only, so it is
        unlocked only for the length of the write."""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for chunk, tag in lines:
            widget.insert("end", chunk, tag or ())
        widget.configure(state="disabled")

    def _show_list(self) -> None:
        self._open_id = None
        self._detail.pack_forget()
        self._list.pack(fill="both", expand=True, padx=16, pady=(12, 14))

    def _open_detail(self, mid: str) -> None:
        self._open_id = mid
        self._list.pack_forget()
        self._detail.pack(fill="both", expand=True, padx=16, pady=(12, 14))
        self._render_detail()

    def _current(self) -> dict | None:
        if self._open_id is None:
            return None
        try:
            return meetings.get(self._open_id)
        except meetings.MeetingsUnreadable as exc:
            self._unreadable(exc)
            return None

    def _render_detail(self) -> None:
        m = self._current()
        if m is None:
            self._show_list()
            return
        self._title.configure(text=m["title"])
        self._meta.configure(
            text=f"{STATUS_LABELS.get(m['status'], m['status'])}  ·  "
                 f"{_minutes(m['duration_s'])}",
            fg=self._status_colour(m["status"]))

        body = []
        if m["status"] == "error" and m["error"]:
            body.append((f"{m['error']}\n\n", "bad"))
        if m["summary"]:
            body.append((m["summary"].strip() + "\n", None))
        if m["actions"]:
            body.append(("\nAction items\n", "head"))
            body.append((m["actions"].strip() + "\n", None))
        if not m["summary"] and m["status"] == "done":
            body.append(("No summary for this meeting.\n", "muted"))
        self._write(self._summary, body or [("", None)])

        labelled = m["source"] != "import"
        lines = []
        for seg in m["segments"]:
            who = "Me" if seg["channel"] == "me" else "Them"
            if labelled:
                lines.append((f"{_clock(seg['start'])} {who}: ",
                              "me" if who == "Me" else "them"))
            else:
                lines.append((f"{_clock(seg['start'])} ", "them"))
            lines.append(((seg["text"] or "") + "\n", None))
        if not lines:
            lines = [("Nothing transcribed yet.\n", "muted")]
        self._write(self._transcript, lines)

        show_retry = (m["status"] == "done" and not m["summary"]
                      and self._app.cfg.get("meeting_treatment", "ai") == "ai")
        if show_retry:
            self._retry.pack(side="left", padx=(0, 6), before=self._folder)
        else:
            self._retry.pack_forget()

    def _summary_text(self) -> str:
        m = self._current()
        if m is None:
            return ""
        parts = [p for p in (m["summary"].strip(), m["actions"].strip()) if p]
        return "\n\n".join(parts)

    def _transcript_text(self) -> str:
        m = self._current()
        return transcript_text(m) if m else ""

    def _copy(self, text: str) -> None:
        if not text:
            self._say("Nothing to copy yet.")
            return
        try:
            pyperclip.copy(text)
        except Exception:
            self._say("Could not reach the clipboard - try again.", bad=True)
            return
        self._say("Copied. Paste it wherever you need it.")

    def _rename(self) -> None:
        m = self._current()
        if m is None:
            return
        new = simpledialog.askstring("Hemsa - Rename meeting", "Title:",
                                     initialvalue=m["title"],
                                     parent=self.winfo_toplevel())
        if not (new and new.strip()):
            return
        try:
            meetings.rename(m["id"], new.strip())
        except meetings.MeetingsUnreadable as exc:
            self._unreadable(exc)
            return
        except Exception as exc:
            log.exception("rename failed")
            self._say(f"Could not rename that meeting: {exc}", bad=True)
            return
        self.refresh()

    def _retry_summary(self) -> None:
        if not (self._open_id and self._jobs() is not None):
            return
        try:
            self._jobs().retry_summary(self._open_id)
        except meetings.MeetingsUnreadable as exc:
            self._unreadable(exc)
            return
        except Exception as exc:
            log.exception("retry summary failed")
            self._say(f"Could not retry the summary: {exc}", bad=True)
            return
        self.refresh()

    def _open_folder(self) -> None:
        if self._open_id is None:
            return
        d = meetings.folder(self._open_id)
        if not d.exists():
            self._say("This meeting has no audio folder on disk.")
            return
        try:
            os.startfile(d)
        except OSError as exc:
            self._say(f"Could not open the folder: {exc}", bad=True)

    def _delete(self) -> None:
        m = self._current()
        if m is None:
            return
        jobs = self._jobs()
        if jobs is not None and jobs.recording_id == m["id"]:
            self._say("This meeting is still recording - stop it before deleting.",
                      bad=True)
            return
        if m["status"] in BUSY_STATUSES:
            # The worker holds the WAV open, so the folder cannot be removed and
            # segments would be written back after the rows had gone.
            self._say("This meeting is still being processed - wait for it to "
                      "finish before deleting.", bad=True)
            return
        if not messagebox.askyesno(
                "Hemsa - Delete meeting",
                f"Delete \"{m['title']}\"?\n\nThe recording, the transcript and the "
                "summary are all removed from this PC. This cannot be undone.",
                parent=self.winfo_toplevel()):
            return
        try:
            meetings.delete(m["id"])
        except meetings.MeetingsUnreadable as exc:
            self._unreadable(exc)
            return
        except Exception as exc:
            log.exception("delete failed")
            self._say(f"Could not delete that meeting: {exc}", bad=True)
            return
        self._show_list()
        self.refresh()

    # ---- refresh ----
    def refresh(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self._items = meetings.list_meetings()
        except meetings.MeetingsUnreadable as exc:
            self._unreadable(exc)
            return
        jobs = self._jobs()
        recording = bool(jobs is not None and jobs.recording_id)
        self._rec.configure(text="Stop" if recording else "Record",
                            bg=P.REC if recording else P.ACCENT,
                            activebackground=P.REC if recording else P.ACCENT_LIT,
                            fg=P.TEXT_ON_ACCENT, activeforeground=P.TEXT_ON_ACCENT)
        self._empty.configure(text=EMPTY)
        self._build_rows()
        if self._open_id is not None:
            self._render_detail()

    def _unreadable(self, exc: Exception) -> None:
        """Never recreate the store, and never show an empty list over it."""
        log.error("meetings store unreadable: %s", exc)
        self._items = []
        self._build_rows()
        self._empty.configure(text="Your meetings could not be read. Hemsa has NOT "
                                   "changed anything.")
        self._say(str(exc), bad=True)

    # ---- theme ----
    def restyle(self) -> None:
        self.configure(bg=P.PAPER)
        for w in (self._note, self._msg, self._list, self._canvas, self._rows_frame,
                  self._empty, self._detail, self._detail_top, self._detail_bar,
                  self._cols, self._left, self._right, self._title, self._meta,
                  self._sum_head, self._tr_head):
            w.configure(bg=P.PAPER)
        for w in (self._note, self._msg, self._empty, self._meta, self._sum_head,
                  self._tr_head):
            w.configure(fg=P.MUTED)
        self._title.configure(fg=P.INK)
        for w in (self._card, self._card_row, self._dot):
            w.configure(bg=P.CARD)
        for chip in self._chips:
            chip.configure(bg=P.MIST, fg=P.DEEP)
        for box in (self._summary, self._transcript):
            theme.apply_text(box)
            box.master.configure(bg=P.PAPER)
            box.tag_configure("me", foreground=P.ACCENT)
            box.tag_configure("them", foreground=P.MUTED)
            box.tag_configure("head", foreground=P.ACCENT, font=FONT_MONO)
            box.tag_configure("muted", foreground=P.MUTED)
            box.tag_configure("bad", foreground=P.DANGER)
        self.refresh()


class MeetingsWindow:
    """Thin host for MeetingsFrame - App tracks it like every other window
    (it needs .win, and .restyle() for a live theme switch)."""

    def __init__(self, root: tk.Tk, app):
        self.win = tk.Toplevel(root)
        self.win.title("Hemsa - Meetings")
        winutil.place_near_tray(self.win, W, H)
        theme.apply(self.win)
        self.frame = MeetingsFrame(self.win, app)
        self.frame.pack(fill="both", expand=True)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    def refresh(self) -> None:
        self.frame.refresh()

    def restyle(self) -> None:
        theme.apply(self.win)
        self.win.configure(bg=P.PAPER)
        self.frame.restyle()


def open_meetings(app) -> None:
    """Open the window, or raise the one already open."""
    app._open("meetings", lambda: MeetingsWindow(app.root, app))
