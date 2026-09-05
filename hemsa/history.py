r"""Local-only dictation history. %LOCALAPPDATA%\Hemsa\history.json, newest first,
capped. Can hold real clinical content - it must never leave this machine and the
file is never committed anywhere.

Entries are {"ts": local "YYYY-MM-DD HH:MM", "iso": aware ISO-8601, "text": str}
plus an optional "star". "ts" is what the old format stored and is kept for display;
"iso" was added so the viewer can show "12 min ago" without guessing a timezone.
All three are optional on read - an entry written by an older build still renders.

An unstarred entry is dropped once it is KEEP_HOURS old: this file can hold real
clinical content and there is no reason for last week's dictation to sit on disk.
Starring is the escape hatch, and an entry whose timestamp cannot be read is kept
rather than deleted - a parse failure must never be a delete.
"""

import json
from datetime import datetime, timedelta

from . import config

PATH = config.DATA_DIR / "history.json"
PREVIEW_MAX_LEN = 96
KEEP_HOURS = 24


def entry_id(entry: dict) -> tuple[str, str]:
    """Identity for starring. The timestamp alone is not enough: it is written to
    the second, so two quick dictations can share one, and starring would then hit
    both. Two with the same second AND the same text are indistinguishable anyway."""
    return (str(entry.get("iso") or entry.get("ts") or ""), entry.get("text") or "")


def prune(items: list[dict], now: datetime) -> list[dict]:
    """Drop unstarred entries older than KEEP_HOURS. Pure, so it is testable
    without touching the clock or the disk."""
    cutoff = now - timedelta(hours=KEEP_HOURS)
    out = []
    for entry in items:
        if entry.get("star"):
            out.append(entry)
            continue
        then = entry_time(entry)
        # None = unreadable timestamp. Keeping it costs one row; deleting it
        # would throw away content because a FIELD was malformed.
        if then is None or then > cutoff:
            out.append(entry)
    return out


def _save(items: list[dict]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # temp + replace, never a plain write: a truncating write means a reader
    # (or a crash) at that instant sees an empty file and the history is gone.
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def load() -> list[dict]:
    """Always pruned, so nothing expired is ever shown even if the app has been
    closed for days. The prune only reaches DISK on the next append or star."""
    try:
        items = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return prune(items, datetime.now().astimezone())


def append(text: str, cfg: dict) -> None:
    items = load()
    now = datetime.now().astimezone()
    items.insert(0, {"ts": now.strftime("%Y-%m-%d %H:%M"),
                     "iso": now.isoformat(timespec="seconds"),
                     "text": text})
    del items[cfg.get("history_cap", 200):]
    _save(items)


def set_star(entry: dict, on: bool) -> list[dict]:
    """Star or unstar the matching entry and write the list back. Returns the new
    list so the caller does not have to re-read what it just wrote."""
    target = entry_id(entry)
    items = load()
    for item in items:
        if entry_id(item) == target:
            if on:
                item["star"] = True
            else:
                item.pop("star", None)
    _save(items)
    return items


def delete(entry: dict) -> list[dict]:
    """Drop the matching entry and write the list back. Returns the new list.

    Keyed on entry_id like set_star, not on the timestamp: two dictations in the
    same second share an iso, so deleting by time alone takes the innocent one
    with it. Missing is fine - the row is gone either way, which is what the user
    asked for."""
    target = entry_id(entry)
    items = [item for item in load() if entry_id(item) != target]
    _save(items)
    return items


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
