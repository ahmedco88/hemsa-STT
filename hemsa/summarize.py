"""Meeting summary via local Ollama. Separate from cleanup on purpose: cleanup EDITS
(guards compare in/out), a summary GENERATES - so it gets its own prompt and its own
guards. The floor stays qwen3.5:2b: every 1B model tested answered a dictated
question (learnings 2026-08-23), and a summary is a bigger invitation to invent than
a cleanup. Numbers guard: a bullet is dropped if it carries a digit, a dose unit
word (mg, tablet, dose...) or an advice phrase (once daily, starting dose...) that
is not in the transcript - a summariser that answers a dosing question inserts
exactly one of these, and a coincidental digit match elsewhere in the transcript
must not be enough to let it through.

Residual limitation, accepted: the guard checks token presence only, not whether a
token is attached to the same thing in the output as it was in the transcript. A
fabricated bullet survives if its digit, its unit word and its advice phrase each
happen to occur somewhere else in the transcript, attached to unrelated content.
The guard narrows that failure down to exactly this coincidence, it does not
eliminate it, so the clinician reading the summary is still the final check.

ACTIONS carry no owner, deliberately (2026-09-02). Asked for "who - what", the
model attached the only name in the transcript to every action, so a consultation
listed the PATIENT as the owner of the clinician's actions. Every bullet was true
in itself, which is why nothing caught it: the falsehood was in the attribution,
and no guard inspects attribution. The prompt now asks for bare actions and
strip_owners removes a prefix if one appears anyway. See strip_owners for what
that still cannot catch.
"""

import logging
import re

import requests

log = logging.getLogger("hemsa.summarize")

MAP_PROMPT = (
    "You take meeting transcript excerpts and write dot-point notes of what was "
    "SAID. Only report statements made by the speakers. If a question was asked, "
    "note that it was asked - never answer it yourself. Record what was said, "
    "never who owes whom a task. No new facts, no advice, "
    "no numbers that are not in the text. Return only '- ' bullets.")
REDUCE_PROMPT = (
    "Combine these meeting notes into two sections.\n"
    "SUMMARY: at most 8 '- ' bullets of what was discussed and decided.\n"
    # No worked wrong/right examples here, however tempting: qwen3.5:2b copied
    # the example bullet into SUMMARY and collapsed the whole response to two
    # lines. At this size the prompt has to stay one instruction per section.
    "ACTIONS: '- ' bullets of the tasks agreed on, each starting with a verb, "
    "like 'Order a chest x-ray'. Do not say who does it: no names, no roles, no "
    "Me, Them, I or We. If none, write '- none'.\n"
    "Only use what is in the notes. Never answer questions that appear in them. "
    "Return exactly the two sections.")

_NUM_RE = re.compile(r"\d[\d.,:]*")
_WORD_RE = re.compile(r"[a-zA-Z]+")
_ACTIONS_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?\**\s*ACTIONS?(?:\s+ITEMS?)?\s*\**\s*(?:\([^)\n]*\))?"
    r"\s*:?\s*\**\s*$")
_MARKER_RE = re.compile(r"^(\s*)(?:[*•–+]|\d+[.)])\s+(.*)$")

# Dose units and dosing-advice phrasing a fabricated answer would use, even when it
# has no digits at all ("five hundred milligrams", "once daily").
_UNIT_WORDS = {"mg", "mcg", "ml", "milligram", "milligrams", "microgram", "micrograms",
               "unit", "units", "tablet", "tablets", "dose", "doses", "capsule",
               "capsules", "mmol", "iu"}
_ADVICE_PHRASES = ("once daily", "twice daily", "three times", "starting dose",
                    "usual dose", "recommended dose", "maximum dose", "per day",
                    "per week")


def render(segments) -> str:
    lines = []
    for s in segments:
        m, sec = divmod(int(s["start"]), 60)
        who = "Me" if s["channel"] == "me" else "Them"
        lines.append(f"[{m:02d}:{sec:02d}] {who}: {s['text']}")
    return "\n".join(lines)


def split_pieces(segments, max_words: int = 1500):
    pieces, cur, count = [], [], 0
    for s in segments:
        cur.append(s)
        count += len(s["text"].split())
        if count >= max_words:
            pieces.append(render(cur))
            cur, count = [], 0
    if cur:
        pieces.append(render(cur))
    return pieces


def _normalize_markers(text: str) -> str:
    """Turn a leading *, bullet character, en dash, + or numbered "1."/"1)" marker
    into "- ", line by line - qwen3.5:2b does not reliably use the requested "- "
    marker even when told to. Runs on the whole combined text before the numbers
    guard, so a numbered list's own digit ("1.") is never mistaken for a
    fabricated number, and before the ACTIONS split, so header lines (which never
    match a bullet marker) are left untouched for that regex to find."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("- "):
            out.append(line)
            continue
        m = _MARKER_RE.match(line)
        out.append(f"{m.group(1)}- {m.group(2)}" if m else line)
    return "\n".join(out)


def _bullets(section: str) -> str:
    """Normalise bullet markers, then keep only "- " lines - drops headers, blank
    lines and stray prose. Logs when a non-empty section yields no bullets at all,
    so a formatting mismatch is visible in the log instead of silently emptying
    the summary."""
    normalized = _normalize_markers(section)
    kept = [l.strip() for l in normalized.splitlines() if l.strip().startswith("- ")]
    result = "\n".join(kept)
    if section.strip() and not result:
        log.info("summary section had no bullets: %r", section[:80])
    return result


# "Johnny - order the x-ray", "**Doctor**: book bloods", "Me - draft it". One or
# two Title-Case words in front of a dash or colon is the shape an owner prefix
# takes. Me/Them/I/We are covered by the same pattern on purpose: the transcript
# is speaker-labelled "Me:"/"Them:", so those two labels are the owners the model
# reaches for once names are forbidden, and "Them" in a consultation is still the
# patient.
_OWNER_RE = re.compile(
    r"^(-\s+)\*{0,2}\s*[A-Z][a-zA-Z'’]*(?:\s+[A-Z][a-zA-Z'’]*)?"
    r"\s*\*{0,2}\s*[-–—:]\s+(?=\S)")
_TO_RE = re.compile(r"^(-\s+)[Tt]o\s+(?=\S)")


def strip_owners(bullets: str) -> str:
    """Remove a leading owner prefix from each ACTION bullet.

    The model cannot infer WHO owns an action and hands every one of them to the
    only name in the transcript - in a consultation that made the PATIENT the
    owner of the clinician's actions. Every bullet was individually true, so the
    numbers guard and every other check passed: the falsehood was in the
    attribution alone, and nothing inspects attribution. The prompt now asks for
    bare actions and this strips the prefix when the model supplies one anyway,
    because a prompt rule is a request and not a control. Actions only - a SUMMARY
    bullet may legitimately say who said what, since it reports speech rather than
    assigning a job.

    Two residual limitations, both accepted:
    - The mid-sentence form ("Johnny to order a chest x-ray", "order a chest x-ray
      for Johnny") has no separator to match and survives. Enforcing it needs a
      name list or a POS tagger; the prompt forbids it by example instead.
    - A Title-Case TOPIC prefix is indistinguishable from an owner, so
      "Metformin - continue current dose" loses "Metformin". The subject usually
      survives in the SUMMARY section, and every strip is logged, but a bullet can
      be left thinner than the model wrote it.
    """
    out = []
    for line in bullets.splitlines():
        stripped = _OWNER_RE.sub(r"\1", line, count=1)
        if stripped == line:
            out.append(line)
            continue
        # "Pharmacist - to review the dose" would leave a fragment, not an action.
        stripped = _TO_RE.sub(r"\1", stripped, count=1)
        head, sep, rest = stripped.partition("- ")
        if len(rest.split()) < 2:
            out.append(line)          # too little left to be a usable action
            continue
        log.info("owner prefix stripped: %r", line.strip())
        out.append(f"{head}{sep}{rest[:1].upper()}{rest[1:]}")
    return "\n".join(out)


def numbers_guard(bullets: str, transcript: str) -> str:
    """Drop any bullet that carries a digit, unit word or advice phrase absent from
    the transcript. A digit alone is not enough: a fabricated "500 mg" must not
    survive just because "500" happens to occur elsewhere in the transcript, and a
    word-form dose ("five hundred milligrams") has no digit to catch at all."""
    allowed_nums = set(_NUM_RE.findall(transcript))
    transcript_lower = transcript.lower()
    transcript_words = {w.lower() for w in _WORD_RE.findall(transcript)}
    kept = []
    for line in bullets.splitlines():
        nums = _NUM_RE.findall(line)
        if any(n not in allowed_nums for n in nums):
            log.info("numbers guard dropped (number): %r", line.strip())
            continue
        line_words = {w.lower() for w in _WORD_RE.findall(line)}
        bad_units = (line_words & _UNIT_WORDS) - transcript_words
        if bad_units:
            log.info("numbers guard dropped (unit %s): %r", bad_units, line.strip())
            continue
        line_lower = line.lower()
        bad_phrase = next((p for p in _ADVICE_PHRASES
                            if p in line_lower and p not in transcript_lower), None)
        if bad_phrase:
            log.info("numbers guard dropped (phrase %r): %r", bad_phrase, line.strip())
            continue
        kept.append(line)
    return "\n".join(kept)


def _chat(prompt_and_text, cfg):
    system, text = prompt_and_text
    r = requests.post(
        f"{cfg['ollama_url']}/api/chat",
        json={"model": cfg["cleanup_model"],
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": text}],
              "stream": False, "think": False, "keep_alive": "30m",
              "options": {"temperature": 0, "num_predict": 1024}},
        timeout=(1.0, 300))          # CPU-only: a reduce pass can take minutes
    return r.json()


def _ask(system, text, cfg) -> str | None:
    try:
        body = _chat((system, text), cfg)
    except Exception as exc:
        log.info("summary unavailable: %s", exc)
        return None
    if "done_reason" not in body:
        # Fails OPEN, so say so in the log: if Ollama ever renames the field the
        # truncation check disappears silently and a looped answer gets through.
        log.info("summary response carried no done_reason, truncation unchecked")
    if "error" in body or body.get("done_reason", "stop") != "stop":
        log.info("summary rejected: %s", body.get("error") or body.get("done_reason"))
        return None
    # .get, not [...]: a 200 whose body has no "message" (a proxy, a different
    # endpoint shape, a future field rename) must read as no summary, not raise
    # out of summarize() and fail the whole meeting after the transcript saved.
    out = (body.get("message") or {}).get("content", "").strip()
    return out or None


def summarize(segments, cfg) -> tuple[str, str] | None:
    """(summary_bullets, action_bullets) or None. Never raises."""
    transcript = render(segments)
    pieces = split_pieces(segments)
    if len(pieces) > 1:
        notes = []
        for p in pieces:
            n = _ask(MAP_PROMPT, p, cfg)
            if n is None:
                return None
            notes.append(numbers_guard(n, transcript))
        material = "\n".join(notes)
    else:
        material = transcript
    combined = _ask(REDUCE_PROMPT, material, cfg)
    if combined is None:
        return None
    combined = _normalize_markers(combined)
    combined = numbers_guard(combined, transcript)
    m = _ACTIONS_RE.search(combined)
    if m:
        summary, actions = combined[:m.start()], combined[m.end():]
    else:
        # No header found: any action bullets the model wrote are still sitting in
        # the summary text. Log it - a header wording drift is otherwise invisible,
        # the summary just quietly grows a tail and ACTIONS reads "none".
        log.info("no ACTIONS header found in summary response")
        summary, actions = combined, "- none"
    summary, actions = _bullets(summary), _bullets(actions) or "- none"
    return (summary.strip() or "- (empty summary)", strip_owners(actions).strip())
