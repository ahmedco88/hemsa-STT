"""Response-guard tests for the cleanup boundary - canned responses, no Ollama needed.
Fixture set required after ANY edit to SYSTEM_PROMPT (see cleanup.py comment)."""

from hemsa.cleanup import sanitize

DICTATED = "um so the patient needs a repeat script for metformin and you know a follow up in two weeks"


def test_normal_edit_passes():
    out = sanitize("The patient needs a repeat script for metformin and a follow-up in two weeks.", DICTATED)
    assert out is not None and "metformin" in out


def test_truncated_response_rejected():
    assert sanitize("The patient needs", DICTATED, done_reason="length") is None


def test_think_block_stripped():
    out = sanitize("<think>user wants cleanup</think>The patient needs a repeat script for "
                   "metformin and a follow-up in two weeks.", DICTATED)
    assert out is not None and "<think>" not in out


def test_preamble_and_fence_stripped():
    out = sanitize("Here's the cleaned text:\nThe patient needs a repeat script for metformin "
                   "and a follow-up in two weeks.", DICTATED)
    assert out is not None and not out.lower().startswith("here")


def test_wrapping_quotes_stripped():
    out = sanitize('"The patient needs a repeat script for metformin and a follow-up in two weeks."',
                   DICTATED)
    assert out is not None and not out.startswith('"')


def test_answer_instead_of_edit_rejected():
    # the answer-trap bug class: model answers the dictated question
    question = "um what is the usual starting dose of metformin for type two diabetes"
    answer = "The usual starting dose is 500 mg once or twice daily with meals, titrated up."
    assert sanitize(answer, question) is None or _mostly_same(answer, question)


def _mostly_same(a, b):
    return False


def test_doubled_output_rejected():
    doubled = ("The patient needs a repeat script for metformin and a follow-up in two weeks. "
               "The patient needs a repeat script for metformin and a follow-up in two weeks. "
               "The patient needs a repeat script for metformin and a follow-up in two weeks.")
    assert sanitize(doubled, DICTATED) is None


def test_empty_rejected():
    assert sanitize("   ", DICTATED) is None
