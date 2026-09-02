import pytest

from hemsa import summarize

SEGS = [
    {"start": 0.0, "end": 4.0, "channel": "them", "text": "Morning, shall we start?"},
    {"start": 4.0, "end": 9.0, "channel": "me",
     "text": "Yes. What is the usual starting dose of metformin?"},
    {"start": 9.0, "end": 14.0, "channel": "them",
     "text": "Let's take that question to the pharmacist and move on to recalls."},
]


def test_render_formats_timestamps_and_speakers():
    out = summarize.render(SEGS)
    assert "[00:04] Me: Yes." in out and out.startswith("[00:00] Them:")


def test_split_pieces_respects_word_budget():
    segs = [{"start": i, "end": i + 1, "channel": "me", "text": "word " * 100}
            for i in range(60)]
    pieces = summarize.split_pieces(segs, max_words=1500)
    assert len(pieces) >= 4
    assert all(len(p.split()) <= 1700 for p in pieces)   # budget + line overhead


def test_numbers_guard_drops_invented_numbers():
    transcript = summarize.render(SEGS)
    bullets = "- Discussed recalls\n- Start metformin 500 mg daily\n- Meet at 3pm"
    kept = summarize.numbers_guard(bullets, transcript)
    assert "recalls" in kept.lower()
    assert "500" not in kept and "3pm" not in kept


def test_answer_trap_summary_must_not_answer(monkeypatch):
    """A question in the transcript must never gain an answer via the summary.
    Simulates a small model that answers - the numbers guard must strip it."""
    canned = {"message": {"content":
              "- The usual starting dose of metformin is 500 mg once daily\n"
              "- Recalls discussed"},
              "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    result = summarize.summarize(SEGS, cfg)
    assert result is not None
    summary, actions = result
    assert "500" not in summary and "500" not in actions


def test_truncated_response_rejected(monkeypatch):
    canned = {"message": {"content": "- looping " * 200}, "done_reason": "length"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    assert summarize.summarize(SEGS, cfg) is None


def test_numbers_guard_unit_word_without_transcript_support():
    segs = SEGS + [{"start": 14.0, "end": 18.0, "channel": "them",
                     "text": "let's meet at 3pm and review the 500 recalls"}]
    transcript = summarize.render(segs)
    bullets = ("- Start metformin 500 mg once daily\n"
               "- Review the 500 recalls at 3pm")
    kept = summarize.numbers_guard(bullets, transcript)
    assert "metformin" not in kept.lower()
    assert "review the 500 recalls at 3pm" in kept.lower()


def test_numbers_guard_word_form_dose_dropped():
    transcript = summarize.render(SEGS)
    bullets = "- The usual starting dose is five hundred milligrams"
    kept = summarize.numbers_guard(bullets, transcript)
    assert kept.strip() == ""


def test_actions_split_handles_markdown_heading(monkeypatch):
    canned = {"message": {"content":
              "### SUMMARY\n"
              "- Recalls reviewed\n"
              "### ACTIONS\n"
              "- Me - draft the invite template"},
              "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    result = summarize.summarize(SEGS, cfg)
    assert result is not None
    summary, actions = result
    assert summary == "- Recalls reviewed"
    assert actions == "- Draft the invite template"


def test_mixed_bullet_markers_normalised(monkeypatch):
    canned = {"message": {"content":
              "SUMMARY:\n* Recalls reviewed\n1. Template agreed\n"
              "ACTIONS:\n• Me - draft it"},
              "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    result = summarize.summarize(SEGS, cfg)
    assert result is not None
    summary, actions = result
    assert summary == "- Recalls reviewed\n- Template agreed"
    assert actions == "- Draft it"


def test_actions_split_ignores_reactions_word(monkeypatch):
    canned = {"message": {"content":
              "SUMMARY:\n"
              "- Discussed patient reactions to the new vaccine\n"
              "- Recalls reviewed\n"
              "ACTIONS:\n"
              "- Me - draft the invite template"},
              "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    result = summarize.summarize(SEGS, cfg)
    assert result is not None
    summary, actions = result
    assert "reactions" in summary.lower()
    assert actions == "- Draft the invite template"


# --- action-item owner attribution (2026-09-02) -------------------------------
# The model cannot infer who owns an action and hands every one to the only name
# in the transcript, so a consultation listed the PATIENT as the owner of the
# clinician's actions. Actions are emitted bare; strip_owners is the enforcement.

CONSULT = [
    {"start": 0.0, "end": 5.0, "channel": "them",
     "text": "Morning Johnny, what brings you in?"},
    {"start": 5.0, "end": 10.0, "channel": "me",
     "text": "This cough has been going three weeks."},
    {"start": 10.0, "end": 15.0, "channel": "them",
     "text": "I will send you for a chest x-ray and arrange some bloods."},
]


def test_owner_prefix_stripped_from_actions(monkeypatch):
    """The reported bug: the patient's name attached to a clinician action."""
    canned = {"message": {"content":
              "SUMMARY:\n- Cough discussed\n"
              "ACTIONS:\n"
              "- Johnny - order a chest x-ray\n"
              "- **Doctor**: arrange the bloods\n"
              "- Them - book a follow-up appointment"},
              "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    summary, actions = summarize.summarize(CONSULT, cfg)
    assert actions == ("- Order a chest x-ray\n"
                       "- Arrange the bloods\n"
                       "- Book a follow-up appointment")
    for owner in ("Johnny", "Doctor", "Them"):
        assert owner not in actions


def test_owner_strip_leaves_a_verb_not_a_fragment():
    """"Pharmacist - to review" would otherwise read as a truncation."""
    assert (summarize.strip_owners("- Pharmacist - to review the dose")
            == "- Review the dose")


def test_owner_strip_declines_when_too_little_is_left():
    """A one-word remainder is not a usable action, so the line is left alone."""
    assert summarize.strip_owners("- Metformin - continue") == "- Metformin - continue"


def test_summary_bullets_keep_their_attribution(monkeypatch):
    """Actions only. A summary bullet REPORTS speech, so who said it is a fact."""
    canned = {"message": {"content":
              "SUMMARY:\n- Johnny - reported a three week cough\n"
              "ACTIONS:\n- none"},
              "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    summary, actions = summarize.summarize(CONSULT, cfg)
    assert summary == "- Johnny - reported a three week cough"


def test_mid_sentence_owner_is_a_KNOWN_MISS():
    """Documented limitation, asserted so it cannot be assumed fixed: with no
    separator there is nothing to match without a name list or a POS tagger. The
    prompt forbids this form by example; nothing enforces it."""
    line = "- Johnny to order a chest x-ray"
    assert summarize.strip_owners(line) == line


def test_missing_message_key_is_no_summary_not_a_crash(monkeypatch):
    """A 200 with an unexpected body shape must not raise out of summarize() and
    fail a meeting whose transcript already saved."""
    monkeypatch.setattr(summarize, "_chat",
                        lambda prompt, cfg: {"done_reason": "stop"})
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    assert summarize.summarize(SEGS, cfg) is None


def test_action_items_header_variant_still_splits(monkeypatch):
    canned = {"message": {"content":
              "SUMMARY:\n- Recalls reviewed\n"
              "**ACTION ITEMS:**\n- Draft the invite template"},
              "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", lambda prompt, cfg: canned)
    cfg = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}
    summary, actions = summarize.summarize(SEGS, cfg)
    assert summary == "- Recalls reviewed"
    assert actions == "- Draft the invite template"
