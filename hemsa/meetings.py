"""Meetings store: %LOCALAPPDATA%\\Hemsa\\meetings.db (stdlib sqlite3, WAL) plus one
folder per meeting for the WAVs. Can hold real clinical/meeting content - local
only, never committed, never uploaded. An unreadable DB is NOT an empty one: it is
renamed .bad.db and raised, same rule as config.ConfigUnreadable.
"""

import logging
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from . import config

log = logging.getLogger("hemsa.meetings")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings(
  id TEXT PRIMARY KEY, created_iso TEXT NOT NULL, title TEXT NOT NULL,
  duration_s REAL DEFAULT 0, source TEXT NOT NULL, status TEXT NOT NULL,
  summary TEXT DEFAULT '', actions TEXT DEFAULT '', error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS segments(
  meeting_id TEXT NOT NULL, start_s REAL, end_s REAL, channel TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS seg_meeting ON segments(meeting_id);
"""


class MeetingsUnreadable(Exception):
    """meetings.db exists but cannot be opened/queried."""


def _db_path():
    return config.DATA_DIR / "meetings.db"


def _open() -> sqlite3.Connection:
    """Connect, set WAL, and apply the schema. On DatabaseError (corrupt file)
    the connection is closed - an open handle keeps the file locked on Windows,
    so a rename attempted while it is still open silently fails - and the
    original exception is re-raised. No quarantine logic here; connect() owns
    that decision."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_db_path(), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        return con
    except sqlite3.DatabaseError:
        con.close()
        raise


def connect(strict: bool = False) -> sqlite3.Connection:
    """Open (creating if needed) the meetings DB with WAL and the schema applied.

    A corrupt DB is never silently recreated over real data. On DatabaseError,
    the bad file is quarantined to meetings.bad.db first. If that rename fails
    (file locked, permission denied, whatever), the corrupt file is still
    sitting at meetings.db, so recreating over it would be exactly the silent
    overwrite this whole scheme exists to prevent - MeetingsUnreadable is
    raised regardless of strict. Only once the rename has actually succeeded
    does strict=False get a fresh DB, opened once (no recursion): if that
    second open also fails, the DatabaseError propagates as MeetingsUnreadable
    rather than looping forever on a file that keeps failing to open.
    """
    try:
        return _open()
    except sqlite3.DatabaseError as exc:
        log.error("meetings.db unreadable: %s", exc)
        bad = _db_path().with_name("meetings.bad.db")
        try:
            _db_path().replace(bad)
        except OSError as rename_exc:
            log.error("could not quarantine meetings.db: %s", rename_exc)
            raise MeetingsUnreadable(str(exc)) from rename_exc
        if strict:
            raise MeetingsUnreadable(str(exc)) from exc
        try:
            return _open()                   # fresh DB only after quarantine
        except sqlite3.DatabaseError as second_exc:
            raise MeetingsUnreadable(str(second_exc)) from second_exc


@contextmanager
def _session():
    """Like `with connect() as con:` (commit on success, rollback on error) but
    also closes the connection - plain sqlite3 context managers commit/rollback
    only, never close, which leaves the file handle open. On Windows that keeps
    the temp DB locked past the test, and leaks a ResourceWarning besides."""
    con = connect()
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def folder(meeting_id: str):
    return config.DATA_DIR / "meetings" / meeting_id


def create(source: str) -> str:
    mid = uuid.uuid4().hex[:12]
    now = datetime.now().astimezone()
    title = now.strftime("Meeting - %a %d %b, %H:%M")
    status = "recording" if source == "record" else "transcribing"
    with _session() as con:
        con.execute("INSERT INTO meetings(id, created_iso, title, source, status)"
                    " VALUES(?,?,?,?,?)",
                    (mid, now.isoformat(timespec="seconds"), title, source, status))
    return mid


def set_status(mid, status, error=None):
    with _session() as con:
        con.execute("UPDATE meetings SET status=?, error=? WHERE id=?",
                    (status, error or "", mid))


def set_duration(mid, seconds):
    with _session() as con:
        con.execute("UPDATE meetings SET duration_s=? WHERE id=?", (seconds, mid))


def save_segments(mid, segments):
    with _session() as con:
        if con.execute("SELECT 1 FROM meetings WHERE id=?", (mid,)).fetchone() is None:
            # The meeting was deleted while the worker was still transcribing it.
            # Inserting now would leave orphan segment rows no screen can ever
            # show and no delete can ever reach.
            log.info("meeting %s is gone - dropping %d segments", mid, len(segments))
            return
        con.execute("DELETE FROM segments WHERE meeting_id=?", (mid,))
        con.executemany(
            "INSERT INTO segments VALUES(?,?,?,?,?)",
            [(mid, s["start"], s["end"], s["channel"], s["text"])
             for s in segments])


def save_summary(mid, summary, actions):
    with _session() as con:
        con.execute("UPDATE meetings SET summary=?, actions=? WHERE id=?",
                    (summary, actions, mid))


def rename(mid, title):
    with _session() as con:
        con.execute("UPDATE meetings SET title=? WHERE id=?", (title, mid))


def delete(mid):
    """Remove a meeting's audio folder AND its rows, audio first.

    The confirm dialog promises the recording is gone from this PC, so a folder
    that survives must raise rather than pass silently: on Windows a WAV still
    open for transcription cannot be removed, and ignoring that error used to
    leave the whole recording on disk under a meeting the user believed deleted.
    Audio goes first for the same reason - rows deleted before a failed rmtree
    would leave the audio with nothing left to point at it.
    """
    d = folder(mid)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        if d.exists():
            raise OSError(f"the audio folder is still in use: {d}")
    with _session() as con:
        con.execute("DELETE FROM segments WHERE meeting_id=?", (mid,))
        con.execute("DELETE FROM meetings WHERE id=?", (mid,))


def list_meetings():
    with _session() as con:
        rows = con.execute("SELECT * FROM meetings ORDER BY created_iso DESC")
        return [dict(r) for r in rows]


def get(mid):
    with _session() as con:
        m = con.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if m is None:
            return None
        segs = con.execute("SELECT start_s AS start, end_s AS end, channel, text"
                           " FROM segments WHERE meeting_id=? ORDER BY start_s",
                           (mid,)).fetchall()
        out = dict(m)
        out["segments"] = [dict(s) for s in segs]
        return out


def unfinished():
    with _session() as con:
        rows = con.execute("SELECT * FROM meetings WHERE status IN"
                           " ('recording','transcribing','summarising')")
        return [dict(r) for r in rows]
