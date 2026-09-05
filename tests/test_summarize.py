import pytest

from hemsa import summarize

SEGS = [
    {"start": 0.0, "end": 4.0, "channel": "them", "text": "Morning, shall we start?"},
    {"start": 4.0, "end": 9.0, "channel": "me",
     "text": "Yes. What is the usual starting dose of metformin?"},
    {"start": 9.0, "end": 14.0, "channel": "them",
     "text": "Let's take that question to the pharmacist and move on to recalls."},
]



CFG = {"ollama_url": "http://localhost:11434", "cleanup_model": "qwen3.5:2b"}


def two_calls(monkeypatch, summary_reply, actions_reply, done="stop"):
    """SUMMARY and ACTIONS are separate calls now, so a canned reply has to say
    WHICH call it is answering. Matched on prompt identity, not on wording, so
    rephrasing a prompt cannot silently point every test at one branch."""
    def fake(prompt_and_text, cfg):
        system = prompt_and_text[0]
        if system is summarize.ACTIONS_PROMPT:
            content = actions_reply
        elif system is summarize.SUMMARY_PROMPT:
            content = summary_reply
        else:
            content = summary_reply          # the map pass, long meetings only
        return {"message": {"content": content}, "done_reason": done}
    monkeypatch.setattr(summarize, "_chat", fake)

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
    two_calls(monkeypatch,
              '{"summary": ["The usual starting dose of metformin is 500 mg once '
              'daily", "Recalls discussed"]}',
              '{"actions": []}')
    result = summarize.summarize(SEGS, CFG)
    assert result is not None
    summary, actions = result
    assert "500" not in summary and "500" not in actions
    assert "recalls" in summary.lower()



def test_truncated_response_rejected(monkeypatch):
    two_calls(monkeypatch, '{"summary": ["looping"]}', '{"actions": []}',
              done="length")
    assert summarize.summarize(SEGS, CFG) is None


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



def test_inline_bullets_in_prose_fail_loudly_instead_of_emptying(monkeypatch):
    """THE bug (2026-09-04). qwen3.5:2b answered the old single call with every
    bullet inline on one line, the "- " filter kept nothing, and the result was
    returned as "- (empty summary)" - a valid-looking result, so meetings shipped
    producing nothing at all. An empty parse is now None, which the UI reports."""
    two_calls(monkeypatch,
              "SUMMARY: - Recalls reviewed. - Template agreed.",   # not JSON
              "ACTIONS: none")
    assert summarize.summarize(SEGS, CFG) is None



def test_prose_fallback_normalises_mixed_markers(monkeypatch):
    """A model that ignores JSON mode still has to work: cleanup_model is
    user-editable, so the prose path stays. One bullet per LINE is what it can
    rescue - see the test above for the inline case, which it cannot."""
    two_calls(monkeypatch,
              "* Recalls reviewed\n1. Template agreed",
              "\u2022 Me - draft it")
    summary, actions = summarize.summarize(SEGS, CFG)
    assert summary == "- Recalls reviewed\n- Template agreed"
    assert actions == "- Draft it"



def test_json_list_tolerates_the_shapes_a_2b_model_returns():
    """Never lose a summary to a wrapper. A bare array, the right list under the
    wrong key, and a newline-separated string all mean the same thing."""
    assert summarize._json_list('["a", "b"]', "summary") == "- a\n- b"
    assert summarize._json_list('{"points": ["a"]}', "summary") == "- a"
    assert summarize._json_list('{"summary": "a\\nb"}', "summary") == "- a\n- b"
    assert summarize._json_list('{"summary": ["- a", "1. b"]}', "summary") == "- a\n- b"
    # an empty list is an ANSWER ("no actions"), not a parse failure
    assert summarize._json_list('{"actions": []}', "actions") == ""
    assert summarize._json_list('{"actions": ["none"]}', "actions") == ""
    # two keys and neither is ours: ambiguous, so hand it to the prose parser
    assert summarize._json_list('{"a": [1], "b": [2]}', "summary") is None
    assert summarize._json_list("not json at all", "summary") is None


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
    two_calls(monkeypatch,
              '{"summary": ["Cough discussed"]}',
              '{"actions": ["Johnny - order a chest x-ray", '
              '"**Doctor**: arrange the bloods", '
              '"Them - book a follow-up appointment"]}')
    summary, actions = summarize.summarize(CONSULT, CFG)
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
    two_calls(monkeypatch,
              '{"summary": ["Johnny - reported a three week cough"]}',
              '{"actions": []}')
    summary, actions = summarize.summarize(CONSULT, CFG)
    assert summary == "- Johnny - reported a three week cough"
    assert actions == "- none"


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
    assert summarize.summarize(SEGS, CFG) is None



def test_a_failed_actions_call_still_keeps_the_summary(monkeypatch):
    """Two calls means two things that can fail. Losing the action list is a
    shame; losing a good summary because of it would be worse."""
    def fake(prompt_and_text, cfg):
        if prompt_and_text[0] is summarize.ACTIONS_PROMPT:
            raise OSError("ollama went away between the two calls")
        return {"message": {"content": '{"summary": ["Recalls reviewed"]}'},
                "done_reason": "stop"}
    monkeypatch.setattr(summarize, "_chat", fake)
    summary, actions = summarize.summarize(SEGS, CFG)
    assert summary == "- Recalls reviewed"
    assert actions == "- none"

# --- actions that only restate the summary (2026-09-05) ----------------------
# Reported from a real meeting: SUMMARY listed three topics and ACTIONS listed
# the same three re-tensed, so the pane read as if it were repeating itself.

REPORTED_SUMMARY = ("- Discussing migraine and headache issues.\n"
                    "- Addressing knee pain problems.\n"
                    "- Reviewing project management workflow.")
REPORTED_ACTIONS = ("- Discuss migraine and headache issues\n"
                    "- Address knee pain concerns\n"
                    "- Review project management workflow\n"
                    "- Plan AI migration using Clock Code with Codex")


def test_the_reported_duplication_is_dropped_and_the_real_action_kept():
    out = summarize.drop_restatements(REPORTED_ACTIONS, REPORTED_SUMMARY)
    assert out == "- Plan AI migration using Clock Code with Codex"


def test_a_genuine_action_sharing_a_subject_with_the_summary_survives():
    """The cutoff is biased towards KEEPING: losing a real action is worse than
    showing a duplicate. These scored 0.52 to 0.66 against their summary line,
    the restatements above scored 0.76 to 0.96, and 0.74 sits in the gap."""
    summary = ("- Next flu clinic scheduled for the 18th with capacity capped "
               "at 35 patients.\n"
               "- Warranty status to be verified before replacement quote request.")
    actions = ("- Book flu clinic for 18th, cap at 35 patients\n"
               "- Check printer warranty and get replacement quote if out of warranty")
    assert summarize.drop_restatements(actions, summary) == actions


def test_dropping_every_action_leaves_none_rather_than_an_empty_pane(monkeypatch):
    two_calls(monkeypatch,
              '{"summary": ["Discussing the roster"]}',
              '{"actions": ["Discuss the roster"]}')
    summary, actions = summarize.summarize(SEGS, CFG)
    assert summary == "- Discussing the roster"
    assert actions == "- none"


def test_restatement_check_is_skipped_when_either_side_is_empty():
    assert summarize.drop_restatements("", "- anything") == ""
    assert summarize.drop_restatements("- Book the room", "") == "- Book the room"
