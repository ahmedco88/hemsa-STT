"""Runs the correction contract vectors against hemsa.dictionary.correct.

tests/fixtures/correction-vectors.json is the specification for the EXACT pass,
and it is data rather than code so the rules can be read without reading the
regex that implements them. The fuzzy pass on top of it lives in
tests/test_wordlist.py; this file deliberately says nothing about it.
"""

import json
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from hemsa.dictionary import Entry, correct

VECTORS = Path(__file__).resolve().parent / "fixtures" / "correction-vectors.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_vector(case):
    entries = [Entry(e["hear"], e["write"], e.get("enabled", True))
               for e in case["entries"]]
    got, applied = correct(case["input"], entries)
    assert got == case["expected"]

    want = Counter({c["to"]: c["count"] for c in case["corrections"]})
    assert Counter(applied) == want


def test_the_accent_case_really_is_decomposed():
    """That vector proves NFC normalization only if its input is NFD on disk. A
    tool that rewrites the file in NFC would turn it into a vacuous pass."""
    case = next(c for c in CASES if "NFC normalization" in c["name"])
    assert case["input"] != unicodedata.normalize("NFC", case["input"])
