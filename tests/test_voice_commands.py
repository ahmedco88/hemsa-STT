"""v0.6 B: spoken punctuation, the spoken send, per-app cleanup, keep_alive."""

import pytest

from hemsa import controller, fastclean


@pytest.mark.parametrize("said, written", [
    ("Hello, comma, how are you?", "Hello, how are you?"),
    ("Dear John. New line. Thanks for the referral.", "Dear John\nThanks for the referral."),
    ("Plan new paragraph review in two weeks.", "Plan\n\nReview in two weeks."),
    ("Is it sore question mark", "Is it sore?"),
    ("BP fine full stop next item", "BP fine. Next item"),
    ("take with food open bracket not fasting close bracket daily.",
     "take with food (not fasting) daily."),
    ("Stop exclamation mark", "Stop!"),
])
def test_spoken_marks(said, written):
    assert fastclean.spoken(said) == written


@pytest.mark.parametrize("text", [
    "A period of two weeks.",                 # clinical words, never marks
    "Polyp in the sigmoid colon.",
    "Commas and semicolons are punctuation.",  # a mark NAME inside a longer word
    "take with food",                          # nothing said: untouched, even the case
])
def test_ordinary_words_are_left_alone(text):
    assert fastclean.spoken(text) == text


def test_a_trailing_new_line_eats_the_cleanup_full_stop():
    # Fast mode adds "." to a 3+ word dictation; spoken() runs after it
    assert fastclean.spoken(fastclean.clean("um dear john new line")) == "Dear john\n"


@pytest.mark.parametrize("text, phrase, out", [
    ("Thanks, send it.", "send it", ("Thanks", True)),
    ("Send it", "send it", ("", True)),
    ("send it to the lab", "send it", ("send it to the lab", False)),
    ("ok sendit", "send it", ("ok sendit", False)),
    ("Thanks, send it.", "", ("Thanks, send it.", False)),
])
def test_send_phrase_only_at_the_end(text, phrase, out):
    assert fastclean.split_send(text, phrase) == out


def test_per_app_mode_overrides_only_its_app():
    cfg = {"cleanup_mode": "ai", "app_rules": {"bp.exe": {"cleanup_mode": "off"},
                                               "chrome.exe": {}}}
    assert controller.effective_mode(cfg, "bp.exe") == "off"
    assert controller.effective_mode(cfg, "chrome.exe") == "ai"   # listed, no override
    assert controller.effective_mode(cfg, "word.exe") == "ai"
    assert controller.effective_mode({"cleanup_mode": "fast",
                                      "app_rules": {"x.exe": {"cleanup_mode": "bogus"}}},
                                     "x.exe") == "fast"


def test_app_key_is_the_lowercase_exe_name(monkeypatch):
    monkeypatch.setattr(controller.winutil, "window_exe",
                        lambda h: r"C:\Program Files\Google\Chrome\Application\Chrome.exe")
    assert controller.app_key(5) == "chrome.exe"
    assert controller.app_key(0) == ""


# ---- the paste path: spoken send presses Enter only into the restored window ----
class _Engine:
    state = "loaded"

    def transcribe(self, clip):
        return "thanks for that send it"


class _Rec:
    level = 0.0

    def __init__(self, cfg):
        pass


@pytest.fixture()
def ctl(monkeypatch):
    import numpy as np
    monkeypatch.setattr(controller.audio, "Recorder", _Rec)
    monkeypatch.setattr(controller.dictionary, "load", lambda: [])
    calls = []
    monkeypatch.setattr(controller.injector, "paste", lambda t: calls.append(("paste", t)))
    monkeypatch.setattr(controller.injector, "press_enter", lambda: calls.append(("enter",)))
    monkeypatch.setattr(controller.history, "append", lambda *a: None)
    monkeypatch.setattr(controller.stats, "record", lambda *a: None)
    monkeypatch.setattr(controller.winutil, "has_caret", lambda h: True)
    c = controller.Controller({"sounds": False, "send_phrase": "send it",
                               "spoken_punctuation": True, "cleanup_mode": "off"},
                              _Engine(), lambda fn: None)
    c.calls = calls
    c.clip = np.full(16000, 0.1, dtype=np.float32)
    return c


def test_send_phrase_pastes_then_presses_enter(ctl, monkeypatch):
    monkeypatch.setattr(controller.winutil, "focus_window", lambda h: True)
    ctl._process(ctl.clip)
    assert ctl.calls == [("paste", "thanks for that"), ("enter",)]


def test_no_enter_when_the_target_window_was_not_restored(ctl, monkeypatch):
    monkeypatch.setattr(controller.winutil, "focus_window", lambda h: False)
    ctl._process(ctl.clip)
    assert ("enter",) not in ctl.calls


def test_keep_alive_follows_the_setting(monkeypatch):
    from hemsa import cleanup
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(json)
        raise RuntimeError("no server in tests")
    monkeypatch.setattr(cleanup.requests, "post", fake_post)
    cleanup.warm_up({"ollama_url": "http://127.0.0.1:1", "cleanup_model": "m",
                     "ollama_keep_alive": "5m"})
    assert sent["keep_alive"] == "5m"


# ---- 0.6.2: bare "one" stays English unless a count follows ----
@pytest.mark.parametrize("said, written", [
    ("one thing", "one thing"),
    ("one example of this", "one example of this"),
    ("one of the referrals", "one of the referrals"),
    ("take one tablet daily", "take 1 tablet daily"),
    ("for one day", "for 1 day"),
    ("one point five milligrams", "1.5 mg"),
    ("one to two weeks", "1 to 2 weeks"),
    ("one hundred", "100"),
    ("twenty one days", "21 days"),
])
def test_bare_one(said, written):
    assert fastclean.numerals(said) == written


# ---- 0.6.2: fit the dictation to the cursor ----
from hemsa import dictionary as _d  # noqa: E402


@pytest.mark.parametrize("text, before, after, out", [
    ("Has diabetes.", "nt ", " and", "has diabetes"),     # mid-sentence insert
    ("Sam called.", "and ", "", "Sam called."),           # a name keeps its capital
    ("I agree.", "so ", "", "I agree."),                  # and so does I
    ("Also stable.", "ok", "", " also stable."),          # glued to a word: space
    ("Next item.", ". ", "", "Next item."),               # after a full stop: sentence
    ("Hello.", "", "", "Hello."),                         # start of the box
    ("Dear Sam\n", "\n", "", "Dear Sam\n"),
    ("Thanks.", None, None, "Thanks."),                   # box would not say: untouched
    ("then review", "x", "Plan", " then review "),        # space before the next word
])
def test_fit(text, before, after, out):
    assert fastclean.fit(text, before, after, _d.COMMON) == out
