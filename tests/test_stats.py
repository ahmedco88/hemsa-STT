"""Stats aggregation: record accumulates per day, summary buckets correctly."""

import datetime as dt
import json

from hemsa import stats


def test_record_accumulates_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "PATH", tmp_path / "stats.json")
    stats.record(10, 5.0, 800)
    stats.record(5, 2.5, 400)
    data = stats.load()
    day = data["days"][dt.date.today().isoformat()]
    assert day == {"n": 2, "words": 15, "audio_s": 7.5, "proc_ms": 1200}


def test_load_survives_corrupt_file(tmp_path, monkeypatch):
    p = tmp_path / "stats.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(stats, "PATH", p)
    assert stats.load() == {"days": {}}


def test_summary_buckets():
    today = dt.date(2026, 8, 23)
    day = lambda n: {"n": n, "words": n * 10, "audio_s": n * 1.0, "proc_ms": n * 100.0}
    data = {"days": {
        "2026-08-23": day(2),   # today: in all three buckets
        "2026-08-17": day(3),   # 6 days ago: week + all
        "2026-08-16": day(5),   # 7 days ago: all only
        "2026-01-01": day(7),   # ancient: all only
    }}
    s = stats.summary(data, today)
    assert s["today"]["n"] == 2
    assert s["week"]["n"] == 5
    assert s["all"]["n"] == 17
    assert s["all"]["words"] == 170
    assert s["first"] == "2026-01-01"


def test_summary_empty():
    s = stats.summary({"days": {}}, dt.date(2026, 8, 23))
    assert s["today"]["n"] == s["week"]["n"] == s["all"]["n"] == 0
    assert s["first"] is None
