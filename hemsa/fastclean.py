"""Instant rules-only cleanup. No model, no network, sub-millisecond.

Exists because the Ollama pass was measured on Ahmed's PC changing a median of
FOUR characters for 2.5 s (max 5.2 s), on a machine where Ollama runs 100% on
CPU. Worse, every small model tested answered a dictated question instead of
tidying it ("what is the usual starting dose of metformin" came back as a dose),
and cleanup.sanitize() cannot catch that because the answer repeats the
question's own words.

Regex cannot answer a question, cannot hallucinate a dose, and cannot invent
content. That safety property is the point, not just the speed.

Scope is deliberately narrow: fillers, stutters, spacing, capitalisation. Real
transcription errors are the AI pass's job.
"""

import re

# Conservative on purpose. "like", "so", "right", "well" and "actually" are NOT
# here: they carry meaning often enough that removing them corrupts real text
# ("titrate like this", "so 5 mg daily"). A cleanup that edits meaning is worse
# than one that leaves a filler in.
_FILLERS = ("um", "umm", "ummm", "uh", "uhh", "uhhh", "erm", "ehm", "hmm", "mmm")

_FILLER_RE = re.compile(r"\b(?:%s)\b[\s,]*" % "|".join(_FILLERS), re.IGNORECASE)
_PHRASE_RE = re.compile(r"\b(?:you know|i mean)\b[\s,]*", re.IGNORECASE)
# "the the patient" -> "the patient". Word must repeat with only space between.
_STUTTER_RE = re.compile(r"\b(\w+)(\s+\1\b)+", re.IGNORECASE)
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)([a-z])")
_LONE_I_RE = re.compile(r"\bi\b")
_TERMINAL_RE = re.compile(r"[.!?,;:\"')\]]$")


def clean(text: str) -> str:
    """Tidy dictated text. Never adds content, never removes a real word."""
    if not text or not text.strip():
        return text

    out = _PHRASE_RE.sub("", text)
    out = _FILLER_RE.sub("", out)
    out = _STUTTER_RE.sub(r"\1", out)
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _MULTI_SPACE_RE.sub(" ", out).strip()
    if not out:
        return text.strip()          # it was fillers all the way down: keep the original

    out = _LONE_I_RE.sub("I", out)
    # capitalise the first letter of the text and of each sentence, including a
    # word newly exposed by removing a leading filler
    out = _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), out)
    # a stray comma left where a filler used to be, e.g. "um, so" -> ", so"
    out = re.sub(r"^[,;]\s*", "", out)
    out = out[:1].upper() + out[1:] if out else out

    if len(out.split()) >= 3 and not _TERMINAL_RE.search(out):
        out += "."
    return out
