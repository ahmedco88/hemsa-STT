"""Runs the dictionary contract vectors against hemsa.dictionary.

The vectors (tests/fixtures/dictionary-test-vectors.json) originate from the
murmur-youtube project (github.com/per-simmons/murmur-youtube, MIT-adjacent Swift/C#
dictation app) as its cross-implementation test contract - copied here as data so
this suite doesn't depend on that repo staying cloned on disk. If the correction
rules ever need to change, murmur-youtube's copy is the one to diff against first.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from hemsa.dictionary import Entry, correct

VECTORS = Path(__file__).resolve().parent / "fixtures" / "dictionary-test-vectors.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_vector(case):
    entries = [
        Entry(e.get("hear", ""), e["write"], e.get("isEnabled", True))
        for e in case["entries"]
        if e["kind"] == "correction"      # 'term' entries feed engine biasing only
    ]
    got, applied = correct(case["input"], entries)
    assert got == case["expected"]

    if "expectedCorrections" in case:
        want = Counter({c["to"]: c["count"] for c in case["expectedCorrections"]})
        assert Counter(applied) == +want
