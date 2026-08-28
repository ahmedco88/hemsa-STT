r"""Local-only dictation history. %LOCALAPPDATA%\Hemsa\history.json, newest first,
capped. Can hold real clinical content - it must never leave this machine and the
file is never committed anywhere.

Entries are {"ts": local "YYYY-MM-DD HH:MM", "iso": aware ISO-8601, "text": str}.
"ts" is what the old format stored and is kept for display; "iso" was added so the
viewer can show "12 min ago" without guessing a timezone. Both are optional on read
- an entry written by an older build still renders.
"""

import json
from datetime import datetime

from . import config

PATH = config.DATA_DIR / "history.json"
PREVIEW_MAX_LEN = 96


def load() -> list[dict]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def append(text: str, cfg: dict) -> None:
    items = load()
    now = datetime.now().astimezone()
    items.insert(0, {"ts": now.strftime("%Y-%m-%d %H:%M"),
                     "iso": now.isoformat(timespec="seconds"),
                     "text": text})
    del items[cfg.get("history_cap", 200):]
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # temp + replace, never a plain write: a truncating write means a reader
    # (or a crash) at that instant sees an empty file and the history is gone.
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def clear() -> None:
    try:
        PATH.unlink()
    except OSError:
        pass


# ---- pure display helpers (no tkinter, so they are unit-testable) ----

def entry_time(entry: dict) -> datetime | None:
    """Best available timestamp as an aware datetime, or None if unusable.
    Falls back to the old local "YYYY-MM-DD HH:MM" field, read as local time."""
    raw = entry.get("iso")
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo else parsed.astimezone()
        except (TypeError, ValueError):
            pass
    try:
        return datetime.strptime(entry["ts"], "%Y-%m-%d %H:%M").astimezone()
    except (KeyError, TypeError, ValueError):
        return None


def relative(entry: dict, now: datetime) -> str:
    """"just now" / "12 min ago" / "2h 5m ago" / "Mon 14:03" past a day.
    Returns the raw ts (or "") when the timestamp cannot be read."""
    then = entry_time(entry)
    if then is None:
        return str(entry.get("ts", ""))
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 0:
        return then.strftime("%a %H:%M")          # clock changed under us
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins}m ago"
    return then.strftime("%a %d %b, %H:%M")


def preview(text: str, max_len: int = PREVIEW_MAX_LEN) -> str:
    """Whitespace collapsed and truncated, for a tidy card."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[:max_len].rstrip() + "…"
