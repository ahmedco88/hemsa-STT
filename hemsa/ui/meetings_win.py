"""Meetings page - record a call, import a file, read the transcript.

The whole UI is a Frame (MeetingsFrame) that the shell hosts as one page. Two
views live in that frame and swap by pack/pack_forget: the list of meetings, and
one meeting's detail.

Nothing here runs on the worker thread. MeetingJobs.on_change fires from BOTH the
UI thread and the job thread, so App wraps it in post() and this page only ever
refreshes on the Tk main thread (see __main__.App._meetings_changed).

Rows are plain tk widgets, exactly as home.py does it: each row needs its own
background for hover, which ttk styles cannot give per widget. Colours are therefore
applied by hand and restyle() re-does them after a live theme switch.

An unreadable store is NOT an empty one (meetings.MeetingsUnreadable): the page
says so and stops, rather than showing "no meetings yet" over a database that is
still sitting there.
"""

import logging
import time
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pyperclip

from .. import cleanup, config, history, meetings, palette as P
from . import theme
from .scale import px
from .activity import ActivityCard, FRAME_MS
from .widgets import PillButton, RoundCard, hover

log = logging.getLogger("hemsa.meetings_win")

PAD = 40                 # logical px, through px() at use time
# Statuses where the worker still owns the meeting's folder and rows.
BUSY_STATUSES = ("recording", "transcribing", "summarising")

SUBTITLE = "Your mic and the other side, transcribed on this PC."
# Not etiquette. Hemsa captures the OTHER side of the call through WASAPI
# loopback, and in several Australian states recording a private conversation
# without every party's consent is an offence, whether or not you are in it.
# "Remember to tell them" reads as optional, and telling is not consent.
COURTESY = "Recording is silent - get everyone's consent before you record."
EMPTY = "No meetings yet. Press Record, or import a file."
# Recording and transcription are self-contained; only the SUMMARY needs Ollama.
# Saying that in the warning matters - otherwise it reads as "do not record".
OLLAMA_DOWN = ("Ollama is not running, so meetings will be transcribed but not "
               "summarised. Start Ollama, or switch to Transcript only in Settings.")
OLLAMA_NO_MODEL = ("Ollama is running but {model} is not pulled, so meetings will "
                   "be transcribed but not summarised. Run: ollama pull {model}")
# Cold start here is 1-3 s. Ten tries at 1.2 s gives it 12 s before we stop
# saying "starting", by which point the warning is honest again.
OLLAMA_WAIT_TRIES = 10
OLLAMA_WAIT_MS = 1200

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


def _stretch(card: RoundCard) -> None:
    """RoundCard sizes its height from the body. A detail pane must fill its
    column instead, so the body window follows the canvas height."""
    card.bind("<Configure>",
              lambda e: card.itemconfigure(card._win, height=max(1, e.height - 2)),
              add="+")


class MeetingsFrame(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent)
        self._app = app
        self._items: list[dict] = []
        self._open_id: str | None = None
        self._rows_card: RoundCard | None = None
        self._paper: list[tuple[tk.Widget, str | None]] = []    # (widget, fg slot)
        self._on_card: list[tuple[tk.Widget, str | None]] = []
        self._widgets: list = []                                 # things with restyle()
        self._build_header()
        self._build_activity()
        self._build_list()
        self._build_detail()
        self._show_list()
        self.restyle()                 # restyle() ends in a refresh()
        self._tick()

    def _jobs(self):
        return getattr(self._app, "jobs", None)

    _ollama = "ready"          # class default: no warning until something checks

    def on_show(self) -> None:
        self._check_ollama()
        self.refresh()

    def _wants_summary(self) -> bool:
        return self._app.cfg.get("meeting_treatment", "ai") == "ai"

    def _check_ollama(self) -> str:
        """Cache the summariser's availability for this page view, and warn.

        Checked when the page opens and again when the user presses Record or
        Retry - the two moments where it changes what they get - rather than on a
        timer: cleanup.status() is an HTTP call and this page now lives for the
        whole session. Down is the fast case (connection refused on localhost is
        immediate), which is the case worth being quick about."""
        if not self._wants_summary():
            self._ollama = "ready"        # nothing is going to ask for a summary
            self._clear_warning()
            self._show_fix(self._ollama)  # and take the fix buttons with it
            return self._ollama
        self._ollama = cleanup.status(self._app.cfg)
        if self._ollama == "ready":
            self._clear_warning()
        elif self._ollama == "down":
            self._say(OLLAMA_DOWN, bad=True)
        elif self._ollama == "no model":
            self._say(OLLAMA_NO_MODEL.format(
                model=self._app.cfg.get("cleanup_model", "the model")), bad=True)
        self._show_fix(self._ollama)
        return self._ollama

    def _clear_warning(self) -> None:
        """Only wipe OUR message. Anything else on that line (a failed import, a
        copy confirmation) belongs to whoever put it there."""
        current = self._msg.cget("text")
        if current.startswith("Ollama") or current in ("Checking…", "Starting Ollama…"):
            self._say("")

    def _show_fix(self, state: str) -> None:
        """The fix row follows the warning. Start Ollama is hidden on 'no model'
        on purpose: the server is already up, so starting it again fixes nothing,
        and the button that would help pulls a download of over a gigabyte, which
        is not something to set off from a button with no progress anywhere."""
        if not getattr(self, "_fix", None) or not self._fix.winfo_exists():
            return
        if state == "ready":
            self._fix.pack_forget()
            return
        if state == "down":
            self._start_ollama.pack(side="left")
        else:
            self._start_ollama.pack_forget()
        self._fix.pack(fill="x", padx=px(PAD), pady=(px(8), 0), after=self._msg)

    def _on_recheck(self) -> None:
        self._say("Checking…")
        self.after(50, self._check_ollama)      # let the label paint first

    def _on_start_ollama(self) -> None:
        problem = cleanup.start_server()
        if problem:
            self._say(problem, bad=True)
            return
        self._say("Starting Ollama…")
        self._await_ollama(OLLAMA_WAIT_TRIES)

    def _await_ollama(self, tries: int) -> None:
        """Poll until the server answers. It takes a second or two to come up, and
        a status() against a port nobody is listening on returns immediately, so
        this costs nothing while we wait."""
        if not self.winfo_exists():
            return
        if self._check_ollama() == "ready":
            self._say("Ollama is running. Summaries are back on.")
        elif tries > 0:
            self._say("Starting Ollama…")
            self.after(OLLAMA_WAIT_MS, lambda: self._await_ollama(tries - 1))

    def _build_activity(self) -> None:
        """Packed once, here, so it sits between the header and BOTH views and
        the page never jumps when it appears."""
        self._activity = ActivityCard(self)
        self._widgets.append(self._activity)
        self._active_since = 0.0
        self._active_state = "idle"

    # ---- header and consent line ----
    def _build_header(self) -> None:
        head = tk.Frame(self)
        head.pack(fill="x", padx=px(PAD), pady=(px(30), px(2)))
        title = tk.Label(head, text="Meetings", font=theme.F.display, anchor="w")
        title.pack(side="left")
        # below the row, not beside the title: inside the row its width would
        # squeeze the pill cluster on the right
        sub = tk.Label(self, text=SUBTITLE, font=theme.F.small, anchor="w")
        sub.pack(fill="x", padx=px(PAD), pady=(0, px(16)))
        self._paper += [(head, None), (title, "INK"), (sub, "MUTED")]

        self._rec = PillButton(head, "Record", kind="primary", command=self._toggle_record)
        self._rec.pack(side="right")
        self._widgets.append(self._rec)

        current = self._app.cfg.get("meeting_treatment", "ai")
        self._treat = tk.StringVar(
            value=TREATMENT_LABELS.get(current, TREATMENT_LABELS["ai"]))
        combo = ttk.Combobox(head, textvariable=self._treat, state="readonly", width=19,
                             style="Hemsa.TCombobox",
                             values=[label for _, label in TREATMENTS])
        combo.pack(side="right", padx=(0, px(10)))
        combo.bind("<<ComboboxSelected>>", lambda e: self._save_treatment())
        self._import = PillButton(head, "Import audio…", kind="ghost",
                                  command=self._import_file)
        self._import.pack(side="right", padx=(0, px(10)))
        self._widgets.append(self._import)

        consent = tk.Frame(self)
        consent.pack(fill="x", padx=px(PAD))
        self._paper.append((consent, None))
        self._dot = tk.Canvas(consent, width=px(18), height=px(18),
                              highlightthickness=0, bd=0)
        self._dot_id = self._dot.create_oval(px(6), px(6), px(12), px(12), width=0)
        self._dot.pack(side="left", padx=(0, px(8)))
        self._paper.append((self._dot, None))

        self._chips = []
        for text in ("Microphone", "System audio"):
            chip = tk.Label(consent, text=text, font=theme.F.small,
                            padx=px(9), pady=px(2))
            chip.pack(side="left", padx=(0, px(6)))
            self._chips.append(chip)

        self._note = tk.Label(consent, text=COURTESY, font=theme.F.small, anchor="w")
        self._note.pack(side="left", padx=(px(6), 0))
        self._paper.append((self._note, "MUTED"))
        self._msg = tk.Label(self, text="", font=theme.F.small, anchor="w",
                             justify="left", wraplength=px(640))
        self._msg.pack(fill="x", padx=px(PAD), pady=(px(4), 0))
        self._paper.append((self._msg, "MUTED"))

        # Built here so it keeps its place in the pack order, shown only while
        # the warning above it is showing. Telling someone Ollama is down and
        # making them go and find it is most of the annoyance of it being down.
        self._fix = tk.Frame(self)
        self._paper.append((self._fix, None))
        self._start_ollama = PillButton(self._fix, "Start Ollama", kind="primary",
                                        padx=12, pady=5, font=theme.F.small,
                                        command=self._on_start_ollama)
        self._start_ollama.pack(side="left")
        self._recheck = PillButton(self._fix, "Check again", kind="ghost",
                                   padx=12, pady=5, font=theme.F.small,
                                   command=self._on_recheck)
        self._recheck.pack(side="left", padx=(px(8), 0))
        self._widgets += [self._start_ollama, self._recheck]

    def _save_treatment(self) -> None:
        for key, label in TREATMENTS:
            if label == self._treat.get():
                self._app.cfg["meeting_treatment"] = key
                config.save(self._app.cfg)
                self._check_ollama()   # "Transcript only" makes the warning untrue
                return

    def _toggle_record(self) -> None:
        jobs = self._jobs()
        if jobs is None:
            return
        stopping = bool(jobs.recording_id)
        try:
            self._say("")
            if not stopping and self._check_ollama() != "ready":
                return        # _check_ollama has already said why, in red
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

    # ---- level dot and the activity card ----
    def _busy_state(self) -> str:
        """What the card should be showing. Recording is known from jobs; the
        two processing states come from the row being worked on, so the card
        follows a meeting the user is not looking at."""
        jobs = self._jobs()
        if jobs is not None and jobs.recording_id:
            return "recording"
        for m in self._items:
            if m["status"] in ("transcribing", "summarising"):
                return m["status"]
        return "idle"

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
        self._paint_activity(level)
        self.after(FRAME_MS, self._tick)

    def _paint_activity(self, level: float) -> None:
        state = self._busy_state()
        if state != self._active_state:
            self._active_state = state
            self._active_since = time.monotonic()
            if state == "idle":
                self._activity.pack_forget()
            else:
                # before the views, so it never lands under the meeting list
                self._activity.pack(fill="x", padx=px(PAD), pady=(px(12), 0),
                                    after=self._msg)
        if state == "idle":
            return
        done, total = getattr(self._jobs(), "progress", (0, 0))
        self._activity.set(state, level=level,
                           elapsed=time.monotonic() - self._active_since,
                           done=done, total=total)

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
        self._canvas.pack(side="left", fill="both", expand=True)
        self._rows_frame = tk.Frame(self._canvas)
        self._window_id = self._canvas.create_window((0, 0), window=self._rows_frame,
                                                     anchor="nw")
        self._rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._window_id, width=e.width))
        # bound on the toplevel, not per row: Tk does not propagate an event to
        # intermediate frames, so a row added later would otherwise be dead. The
        # handler no-ops unless the list is the view on screen, so the detail
        # Texts keep their own wheel behaviour.
        self.winfo_toplevel().bind("<MouseWheel>", self._on_wheel, add="+")
        self._empty = tk.Label(self._rows_frame, font=theme.F.body, anchor="w", text=EMPTY)
        self._paper += [(self._list, None), (self._canvas, None),
                        (self._rows_frame, None), (self._empty, "MUTED")]

    def _on_wheel(self, e) -> None:
        # winfo_exists first: the binding lives on the toplevel, which outlives
        # this frame.
        if self.winfo_exists() and self._list.winfo_ismapped():
            self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _build_rows(self) -> None:
        if self._rows_card is not None:
            self._rows_card.destroy()
            self._rows_card = None
        self._empty.pack_forget()
        if not self._items:
            self._empty.pack(fill="x", pady=px(16))
            return
        now = datetime.now().astimezone()
        card = self._rows_card = RoundCard(self._rows_frame, width=px(100))
        card.pack(fill="x")
        for i, m in enumerate(self._items):
            if i:
                tk.Frame(card.body, height=px(1), bg=P.LINE).pack(fill="x")
            self._make_row(card.body, m, now)
        self._canvas.yview_moveto(0)

    def _make_row(self, parent: tk.Widget, m: dict, now: datetime) -> None:
        status = m["status"]
        row = tk.Frame(parent, cursor="hand2", bg=P.CARD)
        row.pack(fill="x")
        pill = tk.Label(row, text=STATUS_LABELS.get(status, status), font=theme.F.small,
                        padx=px(10), pady=px(2), bg=P.CARD,
                        fg=self._status_colour(status),
                        highlightthickness=1, highlightbackground=P.LINE)
        pill.pack(side="right", padx=(0, px(14)))
        inner = tk.Frame(row, bg=P.CARD)
        inner.pack(side="left", fill="x", expand=True, padx=px(18), pady=px(12))
        title = tk.Label(inner, text=m["title"], font=theme.F.medium, anchor="w",
                         bg=P.CARD, fg=P.INK)
        title.pack(fill="x")
        meta = tk.Label(
            inner, font=theme.F.small, anchor="w", bg=P.CARD, fg=P.MUTED,
            text=f"{history.relative({'iso': m['created_iso']}, now)}"
                 f"  ·  {_minutes(m['duration_s'])}")
        meta.pack(fill="x", pady=(px(3), 0))
        group = [row, inner, title, meta, pill]
        hover(group, rest="CARD", lit="MIST")
        for w in group:
            w.bind("<ButtonRelease-1>", lambda e, mid=m["id"]: self._open_detail(mid))

    def _status_colour(self, status: str) -> str:
        if status == "error":
            return P.DANGER
        return P.ACCENT if status in BUSY else P.MUTED

    # ---- detail view ----
    def _build_detail(self) -> None:
        self._detail = tk.Frame(self)
        self._paper.append((self._detail, None))
        self._back = tk.Label(self._detail, text="← All meetings", font=theme.F.small,
                              anchor="w", cursor="hand2")
        self._back.pack(fill="x")
        self._back.bind("<Button-1>", lambda e: self._show_list())
        self._paper.append((self._back, "MUTED"))

        top = tk.Frame(self._detail)
        top.pack(fill="x", pady=(px(6), 0))
        self._paper.append((top, None))
        self._meta = tk.Label(top, font=theme.F.small, anchor="e")
        self._meta.pack(side="right", padx=(px(16), 0))
        self._title = tk.Label(top, font=theme.F.title, anchor="w", justify="left",
                               cursor="hand2")
        self._title.pack(side="left", fill="x", expand=True)
        self._title.bind("<Double-Button-1>", lambda e: self._rename())
        top.bind("<Configure>",
                 lambda e: self._title.configure(
                     wraplength=max(px(200), e.width - px(140))))
        self._paper += [(self._title, "INK"), (self._meta, "MUTED")]

        # the bar packs first, at the bottom: pack squeezes the LAST widget when
        # the window is short, and that must be the panes, never the buttons
        bar = tk.Frame(self._detail)
        bar.pack(side="bottom", fill="x", pady=(px(14), 0))
        self._paper.append((bar, None))
        cols = self._cols = tk.Frame(self._detail)
        cols.pack(side="top", fill="both", expand=True, pady=(px(14), 0))
        self._paper.append((cols, None))
        self._summary = self._pane(cols, "SUMMARY AND ACTIONS", padx=(0, px(7)))
        self._transcript = self._pane(cols, "TRANSCRIPT", padx=(px(7), 0))
        self._copy_sum = PillButton(bar, "Copy summary", kind="ghost",
                                    command=lambda: self._copy(self._summary_text()))
        self._copy_sum.pack(side="left")
        self._copy_tr = PillButton(bar, "Copy transcript", kind="ghost",
                                   command=lambda: self._copy(self._transcript_text()))
        self._copy_tr.pack(side="left", padx=(px(8), 0))
        self._retry = PillButton(bar, "Retry summary", kind="ghost",
                                 command=self._retry_summary)
        self._folder = PillButton(bar, "Open folder", kind="ghost", command=self._open_folder)
        self._folder.pack(side="left", padx=(px(8), 0))
        delete_cmd = self._delete
        self._delete = PillButton(bar, "Delete", kind="danger", command=delete_cmd)
        self._delete.pack(side="right")
        self._delete.invoke = delete_cmd          # ttk.Button parity for callers
        self._widgets += [self._copy_sum, self._copy_tr, self._retry, self._folder,
                          self._delete]

    def _pane(self, parent: tk.Widget, heading: str, padx) -> tk.Text:
        card = RoundCard(parent, width=px(100), pad=0)
        card.pack(side="left", fill="both", expand=True, padx=padx)
        _stretch(card)
        self._widgets.append(card)
        head = tk.Label(card.body, text=heading, font=theme.F.eyebrow, anchor="w")
        head.pack(fill="x", padx=px(18), pady=(px(14), px(6)))
        box = tk.Frame(card.body)
        box.pack(fill="both", expand=True, padx=(px(12), px(6)), pady=(0, px(12)))
        self._on_card += [(head, "MUTED"), (box, None)]
        # width/height in CHARACTERS, and deliberately small: a tk.Text defaults to
        # 80x24, and two of those side by side ask for ~1200 px. The panes stretch
        # to the column (_stretch), so this is only the floor at the minimum height.
        text = tk.Text(box, wrap="word", width=30, height=6)
        scroll = ttk.Scrollbar(box, orient="vertical", command=text.yview)
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
        self._list.pack(fill="both", expand=True, padx=px(PAD), pady=(px(12), px(20)))

    def _open_detail(self, mid: str) -> None:
        self._open_id = mid
        self._list.pack_forget()
        self._detail.pack(fill="both", expand=True, padx=px(PAD), pady=(px(12), px(20)))
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
            # no "ACTION ITEMS" heading: the actions read straight on from the
            # summary bullets, and the heading was the one thing that had to be
            # deleted by hand before pasting the pane into a note
            body.append(("\n" + m["actions"].strip() + "\n", None))
        if not m["summary"] and m["status"] == "done":
            # "No summary" on its own reads as "there was nothing to say". Name
            # the cause when there is one the user can act on.
            why = {"down": "No summary: Ollama was not running. Start it, then "
                           "press Retry summary.\n",
                   "no model": "No summary: the cleanup model is not pulled. "
                               "Pull it, then press Retry summary.\n"}.get(
                getattr(self, "_ollama", "ready"))
            body.append((why or "No summary for this meeting.\n", "muted"))
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
            self._retry.pack(side="left", padx=(px(8), 0), before=self._folder)
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
        if self._check_ollama() != "ready":
            return            # retrying into a dead Ollama just fails again
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
        self._rec.set_kind("stop" if recording else "primary")
        self._rec.configure_text("Stop" if recording else "Record")
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
        for w, fg in self._paper:
            w.configure(bg=P.PAPER)
            if fg:
                w.configure(fg=getattr(P, fg))
        for w, fg in self._on_card:
            w.configure(bg=P.CARD)
            if fg:
                w.configure(fg=getattr(P, fg))
        for chip in self._chips:
            chip.configure(bg=P.MIST, fg=P.DEEP)
        for w in self._widgets:
            w.restyle()
        for box in (self._summary, self._transcript):
            theme.apply_text(box)
            box.configure(highlightthickness=0, bd=0, relief="flat")
            box.tag_configure("me", foreground=P.ACCENT)
            box.tag_configure("them", foreground=P.MUTED)
            box.tag_configure("head", foreground=P.ACCENT, font=theme.F.eyebrow)
            box.tag_configure("muted", foreground=P.MUTED)
            box.tag_configure("bad", foreground=P.DANGER)
        # rows carry their colours from build time, so refresh() rebuilds them
        self.refresh()
