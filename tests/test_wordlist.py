"""The single-column word list: storage, migration, and the fuzzy pass.

tests/test_dictionary.py still runs murmur's vectors against `correct` unchanged
- that is the exact-match contract. This file covers the layer above it, and in
particular the thing that layer can get catastrophically wrong: rewriting an
ordinary English word because it happened to look like a list entry.
"""

import json

import pytest

from hemsa import dictionary


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(dictionary, "PATH", tmp_path / "dictionary.json")
    monkeypatch.setattr(dictionary.config, "DATA_DIR", tmp_path)
    return tmp_path / "dictionary.json"


# ---- storage -------------------------------------------------------------

def test_missing_file_seeds(tmp_store):
    assert dictionary.load() == dictionary.SEED


def test_unreadable_file_raises_when_strict(tmp_store):
    tmp_store.write_text("{not json", encoding="utf-8")
    with pytest.raises(dictionary.WordListUnreadable):
        dictionary.load(strict=True)


def test_unreadable_file_is_never_silently_the_seed(tmp_store):
    """The 2026-08-28 scare: a bad read looked exactly like a fresh install, and
    the next Save would have written the two seed words over the real list."""
    tmp_store.write_text("{not json", encoding="utf-8")
    words = dictionary.load()                       # non-strict callers still cope
    assert words == dictionary.SEED
    assert tmp_store.read_text(encoding="utf-8") == "{not json"   # nothing written back


def test_migrates_the_old_two_column_shape(tmp_store):
    tmp_store.write_text(json.dumps([
        {"hear": "g p", "write": "GP", "enabled": True},
        {"hear": "open scribe", "write": "OpenScribe", "enabled": True},
        {"hear": "claud", "write": "Claude", "enabled": True},
        {"hear": "switched off", "write": "Ignored", "enabled": False},
    ]), encoding="utf-8")
    assert dictionary.load() == ["GP", "OpenScribe", "Claude"]
    # and the file is rewritten in the new shape, so it migrates once not every load
    assert json.loads(tmp_store.read_text(encoding="utf-8")) == ["GP", "OpenScribe", "Claude"]


def test_save_dedupes_case_insensitively(tmp_store):
    dictionary.save(["Claude", "claude", "  ", "GP"])
    assert json.loads(tmp_store.read_text(encoding="utf-8")) == ["Claude", "GP"]


# ---- matching ------------------------------------------------------------

@pytest.mark.parametrize("text, words, expected", [
    # case fixed on an otherwise correct word (pass 1)
    ("i use openscribe daily", ["OpenScribe"], "i use OpenScribe daily"),
    # split across tokens - the old "g p" -> "GP" row, now with no row
    ("the g p reviewed it", ["GP"], "the GP reviewed it"),
    # hyphenated and glued forms of a two-word entry
    ("we ship claude-code", ["Claude Code"], "we ship Claude Code"),
    ("we ship claudecode", ["Claude Code"], "we ship Claude Code"),
    # a genuine mishearing
    ("ask claud about it", ["Claude"], "ask Claude about it"),
    ("deployed on vercell today", ["Vercel"], "deployed on Vercel today"),
    ("booked at riverbend street medical centre",
     ["Riverbend St Medical Centre"], "booked at Riverbend St Medical Centre"),
    # punctuation is a boundary, and possessives survive
    ("(claud), then claud's turn", ["Claude"], "(Claude), then Claude's turn"),
    # blocking a word from the FUZZY pass never blocks an exact match, so a list
    # entry that looks like ordinary English still works when said correctly
    ("the goodwe inverter", ["Goodwe"], "the Goodwe inverter"),
    ("we use super base", ["Supabase"], "we use Supabase"),
])
def test_corrects(text, words, expected):
    assert dictionary.apply(text, words)[0] == expected


@pytest.mark.parametrize("text, words", [
    # THE case this whole design is built around: an entry of "Claude" must not
    # eat the ordinary word "cloud" (0.73 similarity, and it is in COMMON too).
    ("the cloud is fine", ["Claude"]),
    ("clouds gathered", ["Claude"]),
    # a shorter span never fuzzy-matches at all - too little signal
    ("the cat sat", ["Cap"]),
    # a different onset is a different word, however close the rest looks
    ("he was loud", ["Claude"]),
    # a full stop between tokens means they are not one term
    ("that is the end. Street lights on", ["Riverbend St Medical Centre"]),
    # ordinary English is protected even against a close list entry
    ("check the practice records", ["Practise Recorder"]),
    # found by hand on 2026-08-28 against the real list: a two-word span of short
    # ordinary words was captured by a one-word entry, because the common-word
    # guard only listed words of 5 letters or more.
    ("i had a good week", ["Goodwe"]),
    ("we had a good weekend", ["Goodwe"]),
    # a span that merely CONTAINS the entry must not be replaced - doing so
    # deletes the surrounding words
    ("i use OpenScribe daily", ["OpenScribe"]),
])
def test_leaves_ordinary_words_alone(text, words):
    assert dictionary.apply(text, words) == (text, [])


def test_longest_entry_wins_over_a_shorter_overlapping_one():
    text, applied = dictionary.apply("we ship cloud code", ["Claude", "Claude Code"])
    assert text == "we ship Claude Code"
    assert applied == ["Claude Code"]


def test_already_correct_text_is_not_reported_as_a_correction():
    text, applied = dictionary.apply("I use OpenScribe", ["OpenScribe"])
    assert text == "I use OpenScribe"
    assert applied == []


def test_repeated_hits_are_all_counted():
    text, applied = dictionary.apply("claud, then claud, and claud.", ["Claude"])
    assert text == "Claude, then Claude, and Claude."
    assert applied == ["Claude"] * 3


def test_empty_inputs():
    assert dictionary.apply("", ["Claude"]) == ("", [])
    assert dictionary.apply("anything", []) == ("anything", [])
