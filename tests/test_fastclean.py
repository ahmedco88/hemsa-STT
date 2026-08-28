"""Rules-only cleanup contract.

The safety property is the reason this exists: it must NEVER answer a question,
invent content, or drop a real word. Every small Ollama model tested on
2026-08-23 answered "what is the usual starting dose of metformin" with a dose,
and cleanup.sanitize() could not catch it because the answer repeats the
question's own words.
"""

import pytest

from hemsa import fastclean


def test_removes_fillers_and_capitalises():
    out = fastclean.clean(
        "um so the patient needs a repeat script for metformin and you know a "
        "follow up in two weeks")
    assert out == ("So the patient needs a repeat script for metformin and a "
                   "follow up in two weeks.")


def test_never_answers_a_question():
    q = "uh what is the usual starting dose of metformin for type two diabetes"
    out = fastclean.clean(q)
    assert "500" not in out and "mg" not in out.lower()
    assert "what is the usual starting dose" in out.lower()


def test_no_word_is_invented():
    """Every word in the output must have been in the input (bar capitalisation)."""
    src = ("please send the referral to the cardiologist and mention the ecg showed "
           "atrial fibrillation uh rate controlled on bisoprolol")
    out = fastclean.clean(src)
    src_words = set(src.lower().replace(".", "").split())
    for w in out.lower().replace(".", "").split():
        assert w in src_words, f"invented word: {w!r}"


def test_collapses_stutter():
    assert fastclean.clean("the the patient is stable") == "The patient is stable."


def test_lone_i_is_capitalised():
    assert fastclean.clean("i think i will review her") == "I think I will review her."


def test_sentences_after_a_full_stop_are_capitalised():
    assert fastclean.clean("she is well. she will return in a month") == \
        "She is well. She will return in a month."


def test_existing_punctuation_is_left_alone():
    src = "Already clean text, with punctuation."
    assert fastclean.clean(src) == src


def test_all_filler_input_is_returned_not_emptied():
    """Never hand back an empty string - the user would lose the paste entirely."""
    assert fastclean.clean("um uh erm") == "um uh erm"


def test_meaningful_words_are_not_stripped():
    """'like', 'so', 'right', 'well' carry meaning too often to remove."""
    src = "titrate it like this so the dose is right"
    assert fastclean.clean(src) == "Titrate it like this so the dose is right."


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_input_survives(blank):
    assert fastclean.clean(blank) == blank


def test_short_fragment_gets_no_full_stop():
    assert fastclean.clean("two weeks") == "Two weeks"


def test_is_fast():
    """Sub-millisecond is the whole point; the AI pass it replaces took 2-5 s."""
    import time
    src = "um so the patient needs a repeat script and you know a follow up " * 20
    t0 = time.perf_counter()
    for _ in range(100):
        fastclean.clean(src)
    per_call_ms = (time.perf_counter() - t0) * 1000 / 100
    assert per_call_ms < 5, f"{per_call_ms:.2f} ms per call"
