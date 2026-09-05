"""Aggregate usage stats - counts and durations only, NEVER dictated text.
%LOCALAPPDATA%\\Hemsa\\stats.json, one small record per day, local-only like
everything else. history.py keeps the text; this file deliberately does not.
"""

import datetime as dt
import json

from . import config

PATH = config.DATA_DIR / "stats.json"

_EMPTY_DAY = {"n": 0, "words": 0, "audio_s": 0.0, "proc_ms": 0.0}


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        if isinstance(data.get("days"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"days": {}}


def record(words: int, audio_s: float, proc_ms: float) -> None:
    data = load()
    day = data["days"].setdefault(dt.date.today().isoformat(), dict(_EMPTY_DAY))
    day["n"] += 1
    day["words"] += words
    day["audio_s"] = round(day["audio_s"] + audio_s, 1)
    day["proc_ms"] = round(day["proc_ms"] + proc_ms)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")


def summary(data: dict | None = None, today: dt.date | None = None) -> dict:
    """Totals for 'today', the trailing 7 days ('week'), and 'all' time.
    Each is {n, words, audio_s, proc_ms}; 'first' is the earliest recorded day."""
    data = load() if data is None else data
    today = today or dt.date.today()
    week_floor = (today - dt.timedelta(days=6)).isoformat()
    out = {"today": dict(_EMPTY_DAY), "week": dict(_EMPTY_DAY), "all": dict(_EMPTY_DAY),
           "first": None}
    for iso, day in sorted(data["days"].items()):
        if out["first"] is None:
            out["first"] = iso
        buckets = ["all"]
        if iso >= week_floor:
            buckets.append("week")
        if iso == today.isoformat():
            buckets.append("today")
        for b in buckets:
            for k in _EMPTY_DAY:
                out[b][k] += day.get(k, 0)
    return out


def last_days(n: int, data: dict | None = None, today: dt.date | None = None) -> list[dict]:
    """The trailing n days, oldest first, zero-filled - for the Home day dots."""
    data = load() if data is None else data
    today = today or dt.date.today()
    out = []
    for back in range(n - 1, -1, -1):
        iso = (today - dt.timedelta(days=back)).isoformat()
        day = data["days"].get(iso, _EMPTY_DAY)
        out.append({"date": iso, **{k: day.get(k, 0) for k in _EMPTY_DAY}})
    return out
