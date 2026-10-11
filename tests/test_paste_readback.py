"""v0.6 C: the paste read-back (verdict) and the learn-from-corrections offer."""

import pytest

from hemsa import controller, dictionary, uia
from hemsa.uia import Field

R = (42, 7)                       # one element's runtime id
OTHER = (42, 8)


# ---- verdict: not_landed needs proof, landed needs our words ----
def test_landed_when_our_words_appear():
    assert uia.verdict(Field(R, "Dear Sam, "), Field(R, "Dear Sam, thanks for that"), None,
                       "thanks for that", False) == "landed"


def test_landed_on_the_second_read_for_a_slow_app():
    assert uia.verdict(Field(R, ""), Field(R, ""), Field(R, "thanks"), "thanks",
                       False) == "landed"


def test_not_landed_only_when_unchanged_twice_and_untouched():
    b = Field(R, "hello")
    assert uia.verdict(b, Field(R, "hello"), Field(R, "hello"), "x y", False) == "not_landed"
    # the user pressed Enter / clicked: an unchanged field proves nothing
    assert uia.verdict(b, Field(R, "hello"), Field(R, "hello"), "x y", True) == "unknown"
    # only one read so far: never a verdict of loss
    assert uia.verdict(b, Field(R, "hello"), None, "x y", False) == "unknown"


@pytest.mark.parametrize("before, after", [
    (None, Field(R, "a")),
    (Field(R, None), Field(R, "a")),           # no readable text (or a password box)
    (Field(R, "a"), Field(OTHER, "a")),        # focus moved
    (Field(R, "a"), None),
])
def test_anything_unclear_is_unknown(before, after):
    assert uia.verdict(before, after, after, "words", False) == "unknown"


def test_changed_but_not_by_our_words_is_unknown():
    assert uia.verdict(Field(R, "a"), Field(R, "b"), Field(R, "c"), "words", False) == "unknown"


def test_repeated_text_counts_occurrences_not_presence():
    # the field already contained the same phrase: only a NEW copy means it landed
    b = Field(R, "ok thanks")
    assert uia.verdict(b, Field(R, "ok thanks"), Field(R, "ok thanks"), "thanks",
                       False) == "not_landed"


def test_crlf_and_multiline_paste_still_match():
    b = Field(R, "")
    a = Field(R, uia._norm("Dear Sam\r\nSee you"))
    assert uia.verdict(b, a, None, "Dear Sam\nSee you", False) == "landed"


# ---- correction ----
COMMON = dictionary.COMMON


def _c(after, now, pasted="please ask Ahmd to call", words=()):
    return uia.correction(after, now, pasted, list(words), COMMON)


def test_a_respelt_name_is_offered():
    assert _c("Hi. please ask Ahmd to call", "Hi. please ask Ahmed to call") == ("Ahmd", "Ahmed")


def test_typing_elsewhere_is_ignored():
    assert _c("please ask Ahmd to call", "please ask Ahmd to call. Thanks, Sam") is None


def test_a_word_already_in_the_list_is_not_offered():
    assert _c("please ask Ahmd to call", "please ask Ahmed to call", words=["Ahmed"]) is None


@pytest.mark.parametrize("now", [
    "please ask the registrar to call",      # a rewrite, not a respelling
    "please tell Ahmed to phone",            # two separate edits
    "please ask Ahmd to",                    # a deletion
])
def test_rewrites_and_multiple_edits_are_not_offered(now):
    assert _c("please ask Ahmd to call", now) is None


def test_a_common_word_is_never_offered():
    assert uia.correction("the cat sat", "the car sat", "the cat sat", [], COMMON) is None


@pytest.mark.parametrize("heard, typed", [
    ("EGFR", "eGFR"),           # capitals-only fix, and clinically a different thing
    ("ecg", "ECG"),
    ("Sem", "Sam"),
])
def test_short_acronyms_and_names_are_offered(heard, typed):
    pasted = f"his {heard} rose from 55 to 65"
    assert uia.correction(pasted, pasted.replace(heard, typed), pasted, [],
                          COMMON) == (heard, typed)


def test_a_short_lowercase_fix_is_still_ignored():
    assert uia.correction("the cat sat", "the car sat", "the cat sat", [], COMMON) is None


def test_a_double_click_glued_word_is_not_offered():
    assert _c("please ask Ahmd to call", "please ask Ahmedto call") is None
    assert _c("please ask Ahmd to call", "please ask Ahmed to call") == ("Ahmd", "Ahmed")


def test_pasted_text_not_found_is_none():
    assert _c("something else entirely", "something else entirely!") is None


# ---- the controller's read-back flow ----
class _Rec:
    level = 0.0

    def __init__(self, cfg):
        pass


@pytest.fixture()
def ctl(monkeypatch):
    monkeypatch.setattr(controller.audio, "Recorder", _Rec)
    monkeypatch.setattr(controller.dictionary, "load", lambda strict=False: [])
    monkeypatch.setattr(controller, "VERIFY_READS", (0, 0))
    monkeypatch.setattr(controller.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller.uia, "last_input_tick", lambda: 0)
    events = []
    c = controller.Controller({"sounds": False}, object(), lambda fn: fn())
    c.on_paste_risk = lambda: events.append("chip")
    c.on_learn_offer = lambda w: events.append(("offer", w))
    c.events = events
    return c


def _reads(monkeypatch, *fields):
    it = iter(fields)
    monkeypatch.setattr(controller.uia, "focused", lambda: next(it, None))


def test_a_lost_paste_shows_the_chip_even_on_the_hotkey(ctl, monkeypatch):
    _reads(monkeypatch, Field(R, "x"), Field(R, "x"))
    ctl._verify(Field(R, "x"), "hello there", risky=False, pasted_at=1)
    assert ctl.events == ["chip"]


def test_a_landed_orb_paste_no_longer_shows_the_chip(ctl, monkeypatch):
    _reads(monkeypatch, Field(R, "hello there"))       # then None: the watch ends
    ctl._verify(Field(R, ""), "hello there", risky=True, pasted_at=1)
    assert ctl.events == []


def test_a_risky_paste_that_cannot_be_read_keeps_the_chip(ctl, monkeypatch):
    _reads(monkeypatch, None)
    ctl._verify(None, "hello there", risky=True, pasted_at=1)
    assert ctl.events == ["chip"]


def test_unknown_on_a_safe_paste_stays_silent(ctl, monkeypatch):
    _reads(monkeypatch, Field(OTHER, "x"), Field(OTHER, "x"))
    ctl._verify(Field(R, "x"), "hello there", risky=False, pasted_at=1)
    assert ctl.events == []


def test_a_correction_is_offered_once_it_is_stable(ctl, monkeypatch):
    pasted = "please ask Ahmd to call"
    _reads(monkeypatch,
           Field(R, pasted),                              # landed
           Field(R, "please ask Ahme to call"),           # mid-typing: not offered yet
           Field(R, "please ask Ahmed to call"),
           Field(R, "please ask Ahmed to call"))          # same twice: offered
    ctl._verify(Field(R, ""), pasted, risky=False, pasted_at=1)
    assert ctl.events == [("offer", "Ahmed")]


def test_a_new_dictation_ends_the_watch(ctl, monkeypatch):
    pasted = "please ask Ahmd to call"
    fields = iter([Field(R, pasted), Field(R, "please ask Ahmed to call")])

    def read():
        f = next(fields, None)
        if f is None:
            ctl._utter += 1          # user started dictating again
            return Field(R, "please ask Ahmed to call")
        return f
    monkeypatch.setattr(controller.uia, "focused", read)
    ctl._verify(Field(R, ""), pasted, risky=False, pasted_at=1)
    assert ctl.events == []


def test_the_read_back_never_logs_field_text(ctl, monkeypatch, caplog):
    import logging
    _reads(monkeypatch, Field(R, "SECRET patient words"))
    with caplog.at_level(logging.DEBUG):
        ctl._verify(Field(R, ""), "SECRET patient words", risky=False, pasted_at=1)
    assert "SECRET" not in caplog.text
