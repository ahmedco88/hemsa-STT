"""The history viewer's display contract: relative times and card previews.

Pure helpers only - no tkinter here. The window itself is checked by eye; these
are the bits that quietly go wrong (a naive timestamp, an old-format entry, a
clock that moved backwards) and would show a blank or a crash in the list.
"""

import json

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


# ---- the 24 h rule and its escape hatch ----

def _entry(text="x", star=False, **delta):
    e = {"iso": (NOW - timedelta(**delta)).isoformat(timespec="seconds"), "text": text}
    if star:
        e["star"] = True
    return e


def test_prune_drops_only_unstarred_entries_past_the_cutoff():
    items = [_entry("fresh", minutes=5),
             _entry("edge", hours=23, minutes=59),
             _entry("stale", hours=24, minutes=1),
             _entry("pinned", star=True, days=30)]
    kept = [e["text"] for e in history.prune(items, NOW)]
    assert kept == ["fresh", "edge", "pinned"]


def test_prune_keeps_an_entry_whose_timestamp_cannot_be_read():
    """A malformed FIELD must never delete the CONTENT. Unreadable is kept."""
    items = [{"text": "no timestamp"}, {"ts": "not a date", "text": "junk ts"}]
    assert history.prune(items, NOW) == items


def test_prune_keeps_an_entry_from_the_future():
    """A clock that moved backwards leaves entries dated ahead of now. They are
    newer than the cutoff, so they stay - deleting them would be the worst read
    of an ambiguous clock."""
    future = {"iso": (NOW + timedelta(hours=3)).isoformat(), "text": "ahead"}
    assert history.prune([future], NOW) == [future]


def test_load_hides_expired_entries_without_touching_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    now = datetime.now().astimezone()
    raw = [{"iso": now.isoformat(timespec="seconds"), "text": "keep"},
           {"iso": (now - timedelta(days=2)).isoformat(timespec="seconds"),
            "text": "drop"}]
    (tmp_path / "history.json").write_text(json.dumps(raw), encoding="utf-8")
    assert [e["text"] for e in history.load()] == ["keep"]
    # reading is not a write: the expired entry is still on disk until something
    # appends or stars, so a read-only glance can never destroy history
    assert len(json.loads((tmp_path / "history.json").read_text())) == 2


def test_append_writes_the_prune_through(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    now = datetime.now().astimezone()
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": (now - timedelta(days=2)).isoformat(timespec="seconds"), "text": "old"},
        {"iso": (now - timedelta(days=2)).isoformat(timespec="seconds"),
         "text": "old pinned", "star": True}]), encoding="utf-8")
    history.append("new", {})
    on_disk = [e["text"] for e in json.loads((tmp_path / "history.json").read_text())]
    assert on_disk == ["new", "old pinned"]


def test_set_star_marks_only_the_matching_entry(tmp_path, monkeypatch):
    """Two dictations can share a timestamp (it is written to the second), so the
    text is part of the identity or one star would light both."""
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    now = datetime.now().astimezone()
    same = now.isoformat(timespec="seconds")
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": same, "text": "first"},
        {"iso": same, "text": "second"}]), encoding="utf-8")

    out = history.set_star({"iso": same, "text": "second"}, True)
    assert [e.get("star") for e in out] == [None, True]
    assert json.loads((tmp_path / "history.json").read_text())[1]["star"] is True

    history.set_star({"iso": same, "text": "second"}, False)
    assert all("star" not in e
               for e in json.loads((tmp_path / "history.json").read_text()))


def test_a_corrupt_history_file_reads_as_empty_not_as_a_crash(tmp_path, monkeypatch):
    """load() now prunes, which means it iterates - a JSON object where a list
    was expected used to be returned as-is and would explode in the caller."""
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    (tmp_path / "history.json").write_text('{"not": "a list"}', encoding="utf-8")
    assert history.load() == []


def test_delete_takes_only_the_matching_entry(tmp_path, monkeypatch):
    """Two dictations in the same second share an iso, so a delete keyed on the
    timestamp would take the innocent one with it. entry_id is (iso, text)."""
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    same = datetime.now().astimezone().isoformat()   # NOW is past the 24 h cutoff
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": same, "text": "delete me"},
        {"iso": same, "text": "keep me"},
        {"iso": same, "text": "keep me too", "star": True}]), encoding="utf-8")

    left = history.delete({"iso": same, "text": "delete me"})

    assert [i["text"] for i in left] == ["keep me", "keep me too"]
    on_disk = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert [i["text"] for i in on_disk] == ["keep me", "keep me too"]


def test_delete_of_something_already_gone_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "PATH", tmp_path / "history.json")
    monkeypatch.setattr(history.config, "DATA_DIR", tmp_path)
    fresh = datetime.now().astimezone().isoformat()
    (tmp_path / "history.json").write_text(json.dumps([
        {"iso": fresh, "text": "here"}]), encoding="utf-8")

    left = history.delete({"iso": fresh, "text": "never existed"})

    assert [i["text"] for i in left] == ["here"]
