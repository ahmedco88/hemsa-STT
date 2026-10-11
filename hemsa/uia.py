"""Reads the focused text field back through Windows UI Automation.

Two jobs, both after a paste:
- verdict(): did the text actually arrive? `has_caret()` can only guess, and an
  app that draws its own caret (Chromium, Electron) gives it nothing to go on.
- correction(): did the user retype one of the words straight afterwards? Then it
  is worth offering for the word list.

What is read stays in memory: it is never logged and never written anywhere. A
password field is never read at all.

The rule for both is FluidVoice's, and the same as the rescue chip's: every
ambiguity resolves to "unknown", and the caller then does what it did before
this module existed. A false "it did not land" is a needless popup; a false
"it landed" hides a lost dictation, so "landed" needs the pasted words to be seen.
"""

import ctypes
import difflib
import logging
import re
import threading
from dataclasses import dataclass

log = logging.getLogger("hemsa.uia")

MAX_CHARS = 20000          # a long document is read only this far; past it -> unknown
_local = threading.local()


@dataclass(frozen=True)
class Field:
    rid: tuple             # UIA runtime id: the same element, read twice
    text: str | None       # None = this element exposes no readable text


def _uia():
    """One UI Automation client per thread: COM objects are apartment-bound and
    dictations run on short-lived worker threads."""
    u = getattr(_local, "uia", None)
    if u is None:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        try:
            from comtypes.gen import UIAutomationClient as U
        except ImportError:          # first run from source: generate the wrapper once
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as U
        u = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        _local.uia, _local.U = u, U
    return u, _local.U


def focused() -> Field | None:
    """The focused element and its text, or None when UI Automation will not say."""
    try:
        u, U = _uia()
        el = u.GetFocusedElement()
        if el is None:
            return None
        rid = tuple(el.GetRuntimeId())
        if el.CurrentIsPassword:
            return Field(rid, None)
        return Field(rid, _norm(_text(el, U)))
    except Exception:
        return None


def caret_context(n: int = 2) -> tuple[str, str] | None:
    """(up to n chars before the cursor, up to n after), or None when the focused
    box does not say. "" before means the cursor is at the very start. A selection
    counts from its edges, because typing replaces it."""
    try:
        u, U = _uia()
        el = u.GetFocusedElement()
        if el is None or el.CurrentIsPassword:
            return None
        p = el.GetCurrentPattern(U.UIA_TextPatternId)
        if not p:
            return None
        sel = p.QueryInterface(U.IUIAutomationTextPattern).GetSelection()
        if sel is None or sel.Length < 1:
            return None
        cur = sel.GetElement(0)
        start, end = U.TextPatternRangeEndpoint_Start, U.TextPatternRangeEndpoint_End
        left = cur.Clone()
        left.MoveEndpointByRange(end, left, start)
        left.MoveEndpointByUnit(start, U.TextUnit_Character, -n)
        right = cur.Clone()
        right.MoveEndpointByRange(start, right, end)
        right.MoveEndpointByUnit(end, U.TextUnit_Character, n)
        return _norm(left.GetText(n)) or "", _norm(right.GetText(n)) or ""
    except Exception:
        return None


def _text(el, U) -> str | None:
    try:
        p = el.GetCurrentPattern(U.UIA_ValuePatternId)
        if p:
            v = p.QueryInterface(U.IUIAutomationValuePattern).CurrentValue
            if v is not None:
                return v[:MAX_CHARS]
    except Exception:
        pass
    try:
        p = el.GetCurrentPattern(U.UIA_TextPatternId)
        if p:
            return p.QueryInterface(U.IUIAutomationTextPattern).DocumentRange.GetText(MAX_CHARS)
    except Exception:
        pass
    return None


def _norm(s: str | None) -> str | None:
    return None if s is None else s.replace("\r\n", "\n").replace("\r", "\n")


def last_input_tick() -> int:
    """GetLastInputInfo: the tick of the newest key or mouse event, any app."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


def tick() -> int:
    return ctypes.windll.kernel32.GetTickCount()


def _snippet(pasted: str) -> str:
    """The part of the pasted text that should appear verbatim in the field. The
    first line only: apps rewrite line breaks (a chat box may turn them into <br>)."""
    first = _norm(pasted).strip().split("\n", 1)[0].strip()
    return first[:200]


def verdict(before: Field | None, after: Field | None, later: Field | None,
            pasted: str, user_acted: bool) -> str:
    """'landed' | 'not_landed' | 'unknown'.

    not_landed ONLY when the same element held exactly the same text at both reads
    and nobody touched a key or the mouse in between: a slow app gets two chances,
    and a user who pressed Enter (sending and clearing the field) proves nothing."""
    if before is None or after is None or before.text is None or after.text is None:
        return "unknown"
    if after.rid != before.rid:
        return "unknown"                 # focus moved: nothing to compare
    snip = _snippet(pasted)
    for read in (after, later):
        if read is None or read.rid != before.rid or read.text is None:
            continue
        if snip and read.text.count(snip) > before.text.count(snip):
            return "landed"
    if user_acted or later is None or later.rid != before.rid:
        return "unknown"
    if after.text == before.text and later.text == before.text:
        return "not_landed"
    return "unknown"                     # changed, but not by our words: say nothing


_TOKEN = re.compile(r"\S+")
_EDGE = ".,;:!?\"'()[]{}"


def correction(after: str, now: str, pasted: str, words: list[str],
               common: set[str], min_len: int = 5) -> tuple[str, str] | None:
    """(heard, typed) when exactly one short span of the PASTED words was replaced
    by a similar-looking spelling, else None.

    Edits outside the pasted words are ignored (the user carrying on typing). Two
    or more edits inside it, a rewrite rather than a respelling, a common word, or
    a word already in the list all return None: an offer the user has to decline
    every few dictations trains them to ignore the one that matters."""
    after, now, pasted = _norm(after) or "", _norm(now) or "", (_norm(pasted) or "").strip()
    start = after.rfind(pasted) if pasted else -1
    if start < 0:
        return None
    end = start + len(pasted)
    a = [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(after)]
    b = [m.group() for m in _TOKEN.finditer(now)]
    inside = [i for i, (_w, s, e) in enumerate(a) if s >= start and e <= end]
    if not inside:
        return None
    r1, r2 = inside[0], inside[-1] + 1
    ops = [op for op in difflib.SequenceMatcher(None, [w for w, _s, _e in a], b,
                                                autojunk=False).get_opcodes()
           if op[0] != "equal" and op[1] < r2 and op[2] > r1]
    if len(ops) != 1 or ops[0][0] != "replace":
        return None
    tag, i1, i2, j1, j2 = ops[0]
    if i1 < r1 or i2 > r2:
        return None                      # straddles the edge of what we pasted
    old = " ".join(w for w, _s, _e in a[i1:i2]).strip(_EDGE)
    new = " ".join(b[j1:j2]).strip(_EDGE)
    if not (1 <= len(old.split()) <= 3 and 1 <= len(new.split()) <= 3):
        return None
    if not new or len(new) > 40 or new == old or not re.search(r"[^\W\d_]", new):
        return None
    if difflib.SequenceMatcher(None, old.lower(), new.lower()).ratio() < 0.5:
        return None                      # a different word, not a respelling
    if all(t.lower() in common for t in new.split()):
        return None
    if len(re.sub(r"\W", "", new)) < min_len and new.islower():
        # "cat" -> "car" is a typo fix, not a term: COMMON cannot list all of English.
        # A short word WITH a capital is kept: acronyms and names (ECG, eGFR, Sam) are
        # exactly what the list is for, and EGFR -> eGFR is a different test, not a typo.
        return None
    old_words = old.split()
    if len(old_words) > 1 and len(old_words[-1]) > 1 \
            and new.lower().endswith(old_words[-1].lower()) and " " not in new:
        # a double-click grabs the trailing space, so the retyped word briefly glues to
        # the next one ("Ahmd to" -> "Ahmedto"); never offer the glued half-edit
        return None
    if new in words:
        return None
    return old, new
