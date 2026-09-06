"""Response-guard tests for the cleanup boundary - canned responses, no Ollama needed.
Fixture set required after ANY edit to SYSTEM_PROMPT (see cleanup.py comment)."""

import pytest

from hemsa import cleanup
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


def test_start_server_reports_a_missing_ollama_instead_of_raising(monkeypatch):
    """The button is offered to people who may not have Ollama at all. A traceback
    behind a windowed exe is invisible; a sentence on the warning line is not."""
    monkeypatch.setattr(cleanup.shutil, "which", lambda name: None)

    problem = cleanup.start_server()

    assert "ollama.com" in problem.lower()


def test_start_server_launches_detached_and_says_nothing_on_success(monkeypatch):
    """Detached matters: as a plain child it dies with Hemsa, so the next launch
    would find Ollama down again and the button would look broken."""
    seen = {}

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        seen["flags"] = kw.get("creationflags", 0)
        return object()

    monkeypatch.setattr(cleanup.shutil, "which", lambda name: r"C:\ollama\ollama.exe")
    monkeypatch.setattr(cleanup.subprocess, "Popen", fake_popen)

    assert cleanup.start_server() == ""
    assert seen["cmd"] == [r"C:\ollama\ollama.exe", "serve"]
    assert seen["flags"] & cleanup.subprocess.DETACHED_PROCESS


def test_start_server_surfaces_an_oserror(monkeypatch):
    def boom(cmd, **kw):
        raise OSError("access denied")

    monkeypatch.setattr(cleanup.shutil, "which", lambda name: r"C:\ollama\ollama.exe")
    monkeypatch.setattr(cleanup.subprocess, "Popen", boom)

    assert "access denied" in cleanup.start_server()


# ---- which model is actually installed ----------------------------------

def _tags(monkeypatch, names):
    monkeypatch.setattr(cleanup, "_tags", lambda cfg: names)


CFG = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}


def test_a_different_size_of_the_same_model_is_not_the_model(monkeypatch):
    """The old check matched on the base name, so qwen3.5:0.8b passed as
    qwen3.5:2b - and every 1B-class model tested answered a dictated question
    with a drug dose. Same family is not the same model."""
    _tags(monkeypatch, ["qwen3.5:0.8b", "gemma3:4b"])
    assert cleanup.probe(CFG)[0] == "no model"


def test_the_installed_model_reads_ready(monkeypatch):
    _tags(monkeypatch, ["gemma3:4b", "qwen3.5:2b"])
    state, names = cleanup.probe(CFG)
    assert state == "ready"
    assert names == ["gemma3:4b", "qwen3.5:2b"]


def test_an_untagged_name_means_latest(monkeypatch):
    _tags(monkeypatch, ["gemma3:latest"])
    assert cleanup.probe({**CFG, "cleanup_model": "gemma3"})[0] == "ready"


def test_a_silent_ollama_is_down_and_lists_nothing(monkeypatch):
    """"Down" and "no models installed" must not look alike: an empty list from a
    server that never answered would let the settings page offer an empty
    dropdown as if it were the truth about this PC."""
    _tags(monkeypatch, None)
    assert cleanup.probe(CFG) == ("down", [])


# ---- a dictated question must never come back as an answer -----------------
# The overlap and length guards cannot see this shape: an answer repeats the
# question's own words and runs to a similar length. Measured on the real
# failure, ratio 1.36 and overlap 0.62, both inside their thresholds by a hair.
# The invariant that does catch it is that a cleanup cannot invent a NUMBER.

ASKED = "What is the starting dose of metformin for type 2 diabetes recently diagnosed?"
ANSWERED = ("A starting dose of metformin for type 2 diabetes is typically 500 mg "
            "taken once or twice daily with meals.")


@pytest.mark.parametrize("said, replied", [
    # 2026-09-06, gemma3:4b, pasted at Ahmed's cursor
    (ASKED, ANSWERED),
    # the shape a small model most naturally produces: quote the question, then
    # answer it. An earlier version of this guard asked whether the spoken wh-word
    # SURVIVED, and this sails through that test while being the same failure.
    ("um what is the usual starting dose of metformin for type two diabetes",
     "What is the usual starting dose of metformin for type 2 diabetes? Typically 500 mg."),
    # no wh-word and no question mark at all: an imperative, answered
    ("tell me the starting dose of metformin for type 2 diabetes",
     "The starting dose of metformin for type 2 diabetes is 500 mg twice daily with meals."),
    ("give me the maintenance dose of metformin in renal impairment",
     "The maintenance dose of metformin in renal impairment is 500 mg once daily."),
    # 2026-08-23, three separate 1B models
    ("um what is the usual starting dose of metformin for type two diabetes",
     "500 mg once daily."),
    ("how much paracetamol can she have", "She can have 1 g every 4 to 6 hours."),
    # ANSWERED IN WORDS. This carries no digit at all, so checking the reply alone
    # passed it - and controller then runs fastclean.numerals on the ACCEPTED text,
    # writing "500 mg" at the cursor. The guard has to read the string the user gets.
    (ASKED, "The starting dose of metformin for type 2 diabetes recently diagnosed is "
            "five hundred milligrams with meals."),
    ("what is the maximum daily dose of paracetamol",
     "The maximum daily dose of paracetamol is four grams."),
    # A NUMBER THE DICTATION HAPPENED TO CONTAIN THE DIGITS OF. Testing the digit
    # stream as a substring forgave a fabricated 500 because 1500 was said, and a
    # fabricated 5 for anything with a 5 anywhere. Only whole consecutive tokens count.
    ("she is on metformin 1500 mg daily, what is the usual starting dose for a new "
     "diagnosis?",
     "She is on metformin 1500 mg daily. The usual starting dose for a new diagnosis "
     "is 500 mg."),
    ("he is on metformin 1000 mg twice daily, what should i start a new patient on",
     "He is on metformin 1000 mg twice daily. A new patient should start on 100 mg."),
    ("she takes warfarin 2.5 mg at night, is that the right starting dose for her",
     "She takes warfarin 2.5 mg at night. The right starting dose for her is 5 mg."),
])
def test_a_reply_carrying_a_number_nobody_said_is_rejected(said, replied):
    assert sanitize(replied, said) is None


@pytest.mark.parametrize("said, replied", [
    # the same question, actually cleaned rather than answered
    ("uh what is the usual starting dose of metformin for type two diabetes",
     "What is the usual starting dose of metformin for type 2 diabetes?"),
    # a self-correction reworded away. The wh-word version of this guard rejected
    # it, which cost the user their cleanup for nothing.
    ("so how, how do i put this, the patient is non compliant with her insulin",
     "The patient is non-compliant with her insulin."),
    # an indirect question repunctuated as the instruction it was
    ("can you check the bloods before she leaves?",
     "Please check the bloods before she leaves."),
    # spoken numbers and units written out: every digit traces to a spoken word
    ("start her on metformin five hundred milligrams twice daily",
     "Start her on metformin 500 mg twice daily."),
    ("she weighs eighty kilograms and is one seventy five centimetres",
     "She weighs 80 kg and is 175 cm."),
    # a reading fastclean deliberately refuses to merge, merged by the model
    ("blood pressure was one ten over seventy today",
     "Blood pressure was 110 over 70 today."),
    ("she is on 10 mg of ramipril daily", "She is on 10 mg of ramipril daily."),
    ("give paracetamol one gram every six hours", "Give paracetamol 1 g every 6 hours."),
    # a reading fastclean refuses to merge, merged by the model: consecutive tokens
    ("blood pressure was one thirty over eighty today",
     "Blood pressure was 130 over 80 today."),
])
def test_a_correct_edit_still_passes(said, replied):
    """The guards are measured against the dictation as spoken AND with its numbers
    and units written out, kinder reading wins. Scored against the raw words alone,
    "eighty kilograms" -> "80 kg" is a 0.49 length ratio and a 0.17 overlap: two
    rejections, both of exactly the clinical dictation these guards exist for."""
    assert sanitize(replied, said) is not None


def test_the_known_gap_is_a_gap_and_not_a_promise():
    """Stated so nothing downstream claims more than this catches: an answer with no
    number in it, that also echoes enough of the question to clear the overlap guard,
    is NOT caught. This is a backstop, not a filter, and the README says so."""
    assert sanitize("What is first line for hypertension? ACE inhibitors.",
                    "what is first line for hypertension") is not None


def test_the_checked_model_list_holds_only_what_passed():
    """Adding a model here is a claim that the trap was run against it AND that it
    passed. Others were measured: every 1B model tested on 2026-08-23 and gemma3:4b
    on 2026-09-06 all answered a dictated question with a dose."""
    assert cleanup.CHECKED_MODELS == ("qwen3.5:2b",)


def test_the_cursor_string_is_what_gets_validated():
    """controller applies fastclean.numerals to the ACCEPTED cleanup before pasting,
    so a reply and the text the user actually receives are different strings. The
    guard reads both; validating only the reply is how a dose spelled out in words
    reached the cursor as digits."""
    from hemsa import fastclean
    reply = "The starting dose is five hundred milligrams."
    assert "500" not in reply                      # nothing to catch in the reply
    assert "500" in fastclean.numerals(reply)      # but the cursor gets 500
    assert cleanup._invented_numbers("what is the starting dose", reply) == ["500"]


def test_a_number_may_be_spread_across_tokens_but_not_hidden_inside_one():
    """fastclean deliberately refuses to merge "one thirty over eighty", so a model
    writing 130 is reading, not inventing - whole consecutive tokens are forgiven.
    A number merely CONTAINED in a spoken one is not: 1500 does not license 500."""
    assert cleanup._invented_numbers("blood pressure one thirty over eighty",
                                     "BP 130 over 80.") == []
    assert cleanup._invented_numbers("she is on metformin 1500 mg",
                                     "Start 500 mg.") == ["500"]
