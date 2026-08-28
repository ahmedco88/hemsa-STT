"""The history viewer's display contract: relative times and card previews.

Pure helpers only - no tkinter here. The window itself is checked by eye; these
are the bits that quietly go wrong (a naive timestamp, an old-format entry, a
clock that moved backwards) and would show a blank or a crash in the list.
"""

from datetime import datetime, timedelta

from hemsa import history


NOW = datetime(2026, 8, 23, 21, 0).astimezone()


def _iso(**delta):
    return {"iso": (NOW - timedelta(**delta)).isoformat(timespec="seconds")}


def test_relative_buckets():
    assert history.relative(_iso(seconds=20), NOW) == "just now"
    assert history.relative(_iso(minutes=12), NOW) == "12 min ago"
    assert history.relative(_iso(hours=2, minutes=5), NOW) == "2h 5m ago"
    assert history.relative(_iso(hours=23, minutes=59), NOW) == "23h 59m ago"


def test_relative_past_a_day_switches_to_a_date():
    out = history.relative(_iso(days=3), NOW)
    assert "ago" not in out and "Aug" in out


def test_old_entries_without_iso_still_render():
    """Entries written before the "iso" field existed must not fall back to a
    blank or an exception - they carry a naive local "ts" only."""
    assert history.relative({"ts": "2026-08-23 20:00"}, NOW) == "1h 0m ago"


def test_unreadable_timestamp_degrades_to_the_raw_value():
    assert history.relative({"ts": "not a date"}, NOW) == "not a date"
    assert history.relative({}, NOW) == ""


def test_clock_moved_backwards_does_not_produce_a_negative_age():
    out = history.relative({"iso": (NOW + timedelta(hours=2)).isoformat()}, NOW)
    assert "-" not in out and "ago" not in out


def test_preview_collapses_whitespace_and_truncates():
    assert history.preview("  two\n\n words  ") == "two words"
    long = "word " * 60
    out = history.preview(long)
    assert len(out) <= history.PREVIEW_MAX_LEN + 1 and out.endswith("…")
    assert history.preview("") == ""


def test_append_then_load_round_trips_and_respects_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    for i in range(5):
        history.append(f"line {i}", {"history_cap": 3})
    items = history.load()
    assert [it["text"] for it in items] == ["line 4", "line 3", "line 2"]   # newest first
    assert history.entry_time(items[0]) is not None                          # iso is written
