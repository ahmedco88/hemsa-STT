"""The word list: names, places and terms Hemsa should always get right.

The user types ONE column - the word exactly as it should be typed. Nothing
records what the speech model got wrong, so matching has to find the near-miss
itself. Two passes do that, in this order:

  1. EXACT pass (`correct`) - the behavioural contract in
     tests/fixtures/correction-vectors.json. Whole matches only, case-insensitive with verbatim output,
     longest match first, glued/hyphenated parts match, NFC-normalized. Driven
     with each list word as its own trigger, so it fixes spelling-correct but
     case-wrong text ("openscribe" -> "OpenScribe") and nothing else.
     Those vectors are the specification: a change to correction semantics
     starts in that file, not here.

  2. FUZZY pass (`_fuzzy`) - normalized span matching for what pass 1 cannot
     reach: a word split across tokens ("g p" -> "GP") and a genuine mishearing
     ("claud" -> "Claude"). Deliberately conservative, because a fuzzy rule that
     fires too eagerly corrupts ordinary English. Three guards, all needed:
       - the normalized span is at least MIN_KEY_LEN characters,
       - the first character matches,
       - similarity is at least THRESHOLD.
     Together those keep "cloud" away from a list entry of "Claude" (0.73) while
     letting "claud" through (0.91). COMMON is a fourth guard for the cases the
     first three let past: an ordinary English word is never fuzzy-replaced.

Storage: %LOCALAPPDATA%\\Hemsa\\dictionary.json - a JSON list of strings. The old
two-column [{"hear","write","enabled"}] shape is migrated on load, keeping the
"write" side of every enabled row.
"""

import difflib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass

from . import config

log = logging.getLogger("hemsa.dictionary")

PATH = config.DATA_DIR / "dictionary.json"

SEED: list[str] = []          # a fresh install starts empty, not with someone else's words

MIN_KEY_LEN = 5     # below this the fuzzy pass never fires; pass 1 still does
THRESHOLD = 0.82    # difflib ratio; see the module docstring for why not lower
MAX_SPAN = 4        # most tokens a single list word may be spread across

# Ordinary English words are never fuzzy-replaced, however close they look to a
# list entry. SHORT words matter as much as long ones: MIN_KEY_LEN applies to the
# whole span, not to its tokens, so a two-word span of short words is still a
# candidate - "good week" was captured by a list entry of "Goodwe" until "good"
# and "week" were listed here. Blocking a word here only blocks the FUZZY pass;
# an exact match still fires, so listing a common word costs the user nothing.
COMMON = {
    "also", "back", "base", "been", "best", "both", "call", "came", "case", "come",
    "cost", "date", "days", "done", "down", "each", "even", "ever", "face", "fact",
    "feel", "felt", "find", "fine", "form", "free", "full", "gave", "give",
    "goes", "gone", "good", "hand", "hard", "have", "head", "held", "help", "here",
    "high", "hold", "home", "hope", "hour", "idea", "into", "just", "keep", "kept",
    "kind", "knew", "know", "land", "last", "late", "lead", "left", "less", "life",
    "like", "line", "list", "live", "long", "look", "lost", "made", "make", "many",
    "mean", "meet", "mind", "miss", "more", "most", "move", "much", "must", "name",
    "near", "need", "next", "note", "once", "only", "open", "over", "page", "part",
    "past", "plan", "play", "post", "pull", "push", "read", "real", "rest", "risk",
    "role", "room", "rule", "said", "same", "save", "seen", "sent", "show", "side",
    "sign", "site", "size", "some", "soon", "sort", "stay", "step", "stop", "such",
    "sure", "take", "talk", "team", "tell", "term", "test", "than", "that", "them",
    "then", "they", "this", "time", "told", "took", "town", "true", "turn", "type",
    "unit", "upon", "used", "very", "view", "wait", "walk", "want", "ward", "warm",
    "week", "well", "went", "were", "what", "when", "will", "wish", "with", "word",
    "work", "year", "your",
    "about", "above", "after", "again", "against", "along", "already", "although",
    "always", "among", "another", "answer", "anyone", "anything", "around", "asked",
    "avoid", "because", "become", "before", "began", "begin", "behind", "being",
    "believe", "below", "besides", "better", "between", "beyond", "blood",
    "board", "bring", "brought", "build", "building", "business", "called", "cannot",
    "cause", "centre", "certain", "chance", "change", "check", "child", "children",
    "class", "clear", "clinic", "close", "cloud", "colour", "coming", "common",
    "community", "company", "complete", "concern", "condition", "consider",
    "continue", "could", "country", "course", "cover", "create", "current",
    "decide", "decision", "different", "difficult", "doctor", "doing", "during",
    "early", "either", "enough", "every", "everyone", "everything", "example",
    "expect", "experience", "family", "father", "feeling", "field", "figure",
    "final", "first", "follow", "following", "force", "found", "friend", "front",
    "further", "future", "general", "getting", "given", "going", "government",
    "great", "group", "growth", "happen", "having", "health", "heard", "history",
    "hospital", "house", "human", "hundred", "important", "increase", "indeed",
    "inside", "instead", "interest", "issue", "keeping", "known", "large", "later",
    "learn", "least", "leave", "letter", "level", "light", "likely", "listen",
    "little", "living", "local", "longer", "looking", "making", "manage", "market",
    "matter", "maybe", "means", "medical", "meeting", "member", "might", "million",
    "minute", "moment", "money", "month", "morning", "mother", "moved", "movement",
    "music", "nature", "nearly", "needed", "never", "night", "north", "nothing",
    "notice", "number", "offer", "office", "often", "order", "other", "others",
    "outside", "paper", "parent", "particular", "party", "patient", "people",
    "perhaps", "period", "person", "phone", "place", "plant", "please", "point",
    "policy", "position", "possible", "power", "practice", "present", "pressure",
    "pretty", "price", "private", "probably", "problem", "process", "produce",
    "program", "project", "provide", "public", "question", "quickly", "quite",
    "rather", "reach", "ready", "really", "reason", "receive", "recent", "record",
    "reduce", "regard", "remain", "remember", "report", "require", "research",
    "resource", "response", "result", "return", "right", "round", "school",
    "second", "section", "seems", "sense", "series", "service", "seven", "several",
    "should", "shown", "similar", "simple", "since", "single", "small", "social",
    "someone", "something", "sometimes", "sound", "south", "space", "speak",
    "special", "spend", "staff", "stage", "stand", "start", "state", "still",
    "story", "street", "strong", "study", "stuff", "subject", "success", "suggest",
    "support", "system", "table", "taken", "taking", "talking", "teacher",
    "their", "there", "these", "thing", "think", "third", "those", "though",
    "thought", "three", "through", "throughout", "times", "today", "together",
    "total", "toward", "training", "treatment", "trying", "under", "understand",
    "until", "usually", "value", "various", "visit", "voice", "waiting",
    "walking", "wanted", "watch", "water", "weeks", "where", "whether", "which",
    "while", "white", "whole", "window", "within", "without", "woman", "women",
    "words", "working", "world", "would", "write", "wrong", "years", "young",
}
# Plurals count as ordinary English too, and the all-common test above is only as
# good as its coverage: "practice records" slipped through because "records" was
# absent while "record" was present.
COMMON |= {w + "s" for w in COMMON if not w.endswith(("s", "y"))}


class WordListUnreadable(Exception):
    """dictionary.json exists but could not be read or parsed.

    NOT the same as "no word list yet". They used to be the same, and that cost
    a real scare on 2026-08-28: a transient read failure returned SEED, the
    window showed the two seed rows as if they were the whole list, and the next
    Save would have written those two over the user's real words. A missing file
    still seeds; an unreadable one is raised so the caller can refuse.
    """


@dataclass
class Entry:
    """A trigger -> replacement rule. Internal to the exact pass: the word list
    drives it with hear == write. Kept as the contract's unit of test."""
    hear: str
    write: str
    enabled: bool = True


def _dedupe(words: list[str]) -> list[str]:
    seen, out = set(), []
    for w in words:
        w = w.strip()
        if w and w.casefold() not in seen:
            seen.add(w.casefold())
            out.append(w)
    return out


def _migrate(raw: list) -> list[str]:
    """Old two-column rows -> the typed side of every enabled row."""
    return _dedupe([r["write"] for r in raw
                    if isinstance(r, dict) and r.get("enabled", True) and r.get("write")])


def load(strict: bool = False) -> list[str]:
    """The word list. A missing file seeds; an unreadable one raises when strict."""
    if not PATH.exists():
        return list(SEED)
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("not a list")
        if raw and isinstance(raw[0], dict):
            words = _migrate(raw)
            log.info("migrated %d two-column rows to %d words", len(raw), len(words))
            save(words)                      # write the new shape once
            return words
        return _dedupe([w for w in raw if isinstance(w, str)])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("word list unreadable: %s", exc)
        if strict:
            raise WordListUnreadable(str(exc)) from exc
        return list(SEED)


def save(words: list[str]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(_dedupe(words), indent=2, ensure_ascii=False),
                    encoding="utf-8")


# --------------------------------------------------------------------------
# Pass 1: the exact contract. The vectors are the specification.
# --------------------------------------------------------------------------

def _trigger_regex(hear: str) -> str:
    # Rule 4: split the trigger into word parts; between parts allow spaces/hyphens
    # including none. Rule 1: word boundaries at both ends.
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", hear.strip()) if p]
    return r"\b" + r"[\s\-]*".join(parts) + r"\b"


def correct(text: str, entries: list[Entry]) -> tuple[str, list[str]]:
    """Applies exact corrections; returns (corrected text, list of 'write' values)."""
    active = [e for e in entries if e.enabled and e.hear.strip()]
    if not active or not text:
        return text, []

    # An accented trigger must match its decomposed (e + combining accent) form too,
    # so compare in NFC on both sides.
    text = unicodedata.normalize("NFC", text)
    for e in active:
        e.hear = unicodedata.normalize("NFC", e.hear)

    # Rule 3: longest trigger first. Python's regex alternation takes the first
    # alternative that matches at a position, so ordering by length implements it.
    active.sort(key=lambda e: len(e.hear), reverse=True)
    combined = "|".join(f"(?P<g{i}>{_trigger_regex(e.hear)})" for i, e in enumerate(active))
    applied: list[str] = []

    def sub(m: re.Match) -> str:
        idx = int(m.lastgroup[1:])            # type: ignore[index]
        if m.group(0) != active[idx].write:   # already-correct text is not a correction
            applied.append(active[idx].write)
        return active[idx].write              # Rule 2: output verbatim

    return re.sub(combined, sub, text, flags=re.IGNORECASE), applied


# --------------------------------------------------------------------------
# Pass 2: normalized span matching for near-misses.
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)      # runs of letters/digits
_JOINER_RE = re.compile(r"[\s\-']*")                # what may sit between tokens


def key(s: str) -> str:
    """Lowercase, accent-stripped, alphanumerics only - the comparison form."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if c.isalnum())


def _score(span_key: str, target_key: str, tokens: list[str]) -> float:
    """0.0, or a similarity in [0, 1]. An exact key match is allowed at any length -
    that is how initials reach their term ("g p" -> "GP") - but a merely SIMILAR
    span has to clear every guard below."""
    if span_key == target_key:
        return 1.0
    if len(span_key) < MIN_KEY_LEN:                  # too little signal to guess from
        return 0.0
    if span_key[0] != target_key[0]:                 # a mishearing keeps the onset
        return 0.0
    if all(t in COMMON for t in tokens):             # never rewrite ordinary English
        return 0.0
    # A span that merely CONTAINS the term still scores well, and replacing it
    # deletes the surrounding words: "OpenScribe daily" -> "OpenScribe" scored
    # 0.83 and ate "daily". Lengths have to be comparable, not just similar.
    if abs(len(span_key) - len(target_key)) > max(2, len(target_key) // 4):
        return 0.0
    return difflib.SequenceMatcher(None, span_key, target_key).ratio()


def _fuzzy(text: str, words: list[str]) -> tuple[str, list[str]]:
    targets = [(w, key(w)) for w in words]
    targets = [(w, k) for w, k in targets if k]
    if not targets or not text:
        return text, []

    toks = list(_TOKEN_RE.finditer(text))
    out: list[str] = []
    applied: list[str] = []
    pos = 0
    i = 0
    while i < len(toks):
        hit = None
        # BEST span, not longest. Longest-first looked right and was wrong twice
        # over: it let "OpenScribe daily" beat "OpenScribe" (deleting a word)
        # and "claud's" beat "claud" (deleting the possessive). Ties go to the
        # longer span, which is what makes a two-word entry win over a one-word
        # one when both match exactly (rule 3).
        for n in range(1, min(MAX_SPAN, len(toks) - i) + 1):
            # tokens may only be joined by spaces, hyphens or apostrophes - a
            # full stop or comma between them means they are not one term.
            if any(not _JOINER_RE.fullmatch(text[toks[j].end():toks[j + 1].start()])
                   for j in range(i, i + n - 1)):
                break
            start, end = toks[i].start(), toks[i + n - 1].end()
            span = text[start:end]
            k = key(span)
            if not k:
                continue
            tokens = [key(t) for t in _TOKEN_RE.findall(span)]
            word, score = max(((w, _score(k, tk, tokens)) for w, tk in targets),
                              key=lambda t: t[1])
            if score >= THRESHOLD and (hit is None or score >= hit[0]):
                hit = (score, n, word, start, end)
        if hit:
            _, n, word, start, end = hit
            out.append(text[pos:start])
            if text[start:end] != word:
                applied.append(word)
            out.append(word)
            pos = end
            i += n
        else:
            i += 1
    out.append(text[pos:])
    return "".join(out), applied


def apply(text: str, words: list[str]) -> tuple[str, list[str]]:
    """Correct `text` against the word list. Returns (text, words actually applied)."""
    words = _dedupe(words)
    if not words or not text:
        return text, []
    text, applied = correct(text, [Entry(w, w) for w in words])
    text, fuzzy_applied = _fuzzy(text, words)
    return text, applied + fuzzy_applied
