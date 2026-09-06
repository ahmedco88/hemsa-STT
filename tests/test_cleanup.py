"""Response-guard tests for the cleanup boundary - canned responses, no Ollama needed.
Fixture set required after ANY edit to SYSTEM_PROMPT (see cleanup.py comment)."""

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
