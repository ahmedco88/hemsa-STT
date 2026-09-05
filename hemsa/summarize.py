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

SUMMARY and ACTIONS are TWO separate calls asking for JSON (2026-09-04). All of
that shape came out of measuring `scripts/eval_summary.py` against the real
model, not from taste:

* One call for both used to ask for "SUMMARY:" / "ACTIONS:" sections of "- "
  bullets. qwen3.5:2b replied with every bullet inline on ONE line and then
  "ACTIONS: none", so the header regex found no bare header, the bullet filter
  found no line starting with "- ", and 100% of the output was discarded. The
  shipped release had been producing "- (empty summary)" for every meeting.
* `format: "json"` fixes the line-shape problem and Ollama 0.30.10 honours it. A
  JSON SCHEMA in `format` does NOT work on this build - it returns empty content,
  or is ignored outright when `think` is also set - so the key is named in the
  prompt instead, and the reply is still not guaranteed to parse.
* Asking one call for both keys left "actions" EMPTY in 3 of 3 runs: the model
  put the tasks in "summary" and nothing in "actions". Asking for actions FIRST
  fixed the content and broke the JSON instead (`"actions": "a", "b"`, no
  brackets). One list per call is the shape that holds, and it is the same
  lesson as REDUCE_PROMPT's "one instruction per section" one size up. It also
  deletes the SUMMARY/ACTIONS header split entirely - nothing has to find a
  boundary in a reply that only ever contains one list.
* Cost: two round trips instead of one, about 6 s each on this CPU-only PC for a
  short meeting, against an hour of recording. The map pass dominates a long one.

And the reason the failure stayed invisible: an empty parse was returned as
"- (empty summary)", which looks like a result, so nothing upstream complained.
An empty summary is now a None - the meeting reports that the summary failed.

ACTIONS carry no owner, deliberately (2026-09-02). Asked for "who - what", the
model attached the only name in the transcript to every action, so a consultation
listed the PATIENT as the owner of the clinician's actions. Every bullet was true
in itself, which is why nothing caught it: the falsehood was in the attribution,
and no guard inspects attribution. The prompt now asks for bare actions and
strip_owners removes a prefix if one appears anyway. See strip_owners for what
that still cannot catch.
"""

import difflib
import json
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
SUMMARY_PROMPT = (
    "Summarise what was discussed in these meeting notes. Return JSON: "
    '{"summary": ["...", "..."]} and nothing else. At most 8 short strings, '
    # No worked wrong/right examples here, however tempting: qwen3.5:2b copied
    # the example bullet into the output and collapsed the whole response.
    "keeping any number that was said. Only use what is in the notes. "
    "Never answer a question in the notes.")
ACTIONS_PROMPT = (
    "List every task that was agreed in these meeting notes. Return JSON: "
    '{"actions": ["...", "..."]} and nothing else. One short string per task, '
    "each starting with a verb, keeping any date, number or deadline that was "
    "said. Do not say who does it: no names, no roles, no Me, Them, I or We. "
    # Do NOT add a "a topic is not a task" line here, however tempting: it was
    # measured, and it made qwen3.5:2b terser across the board - "Book flu
    # clinic" instead of "Book flu clinic for 18th, cap at 35 patients", losing
    # every detail the next line asks for. drop_restatements is the control for
    # topic-shaped actions; the prompt is only ever a request.
    "Only tasks that were actually agreed. Never answer a question in the "
    "notes. Empty list if there are none.")

_NUM_RE = re.compile(r"\d[\d.,:]*")
_WORD_RE = re.compile(r"[a-zA-Z]+")
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


def _strip_marker(item: str) -> str:
    """A "- ", "* ", "1." or bullet-character prefix inside a JSON string. The
    model was told not to, and adds them anyway about half the time."""
    line = _normalize_markers(item.strip())
    return line[2:].strip() if line.startswith("- ") else line.strip()


def _json_list(content: str, key: str) -> str | None:
    """The named list from a JSON reply as "- " bullets, or None when the reply
    is not JSON at all - which hands the caller back to the prose parser.

    Deliberately tolerant about everything except being parseable: a bare array,
    the right list under the wrong key, a single newline-separated string and a
    stray number are all things a 2B model returns, and none of them is a reason
    to throw the summary away. An empty list is "" and NOT None: the model saying
    there are no actions is an answer, not a failure."""
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if isinstance(data, dict):
        value = data.get(key)
        if value is None and len(data) == 1:
            value = next(iter(data.values()))       # right list, wrong key
    else:
        value = data                                # a bare array
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return None
    lines = []
    for item in value:
        if not isinstance(item, (str, int, float)):
            continue
        text = _strip_marker(str(item))
        # "none" is the model spelling out an empty list, not an action
        if text and text.lower().strip(".") != "none":
            lines.append(f"- {text}")
    return "\n".join(lines)


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


# Measured, not guessed. On the reported case the restated actions scored
# 0.76, 0.96 and 0.96 against their summary line, while genuine actions that
# merely share a subject with the summary scored 0.24 to 0.66. 0.74 sits in
# that gap. Re-derive it if the prompts change: the numbers are the reason.
RESTATE_RATIO = 0.74


def drop_restatements(actions: str, summary: str) -> str:
    """Drop an ACTION that is just a SUMMARY line re-tensed.

    Asked separately what was discussed and what was agreed, a small model
    given a thin transcript answers both with the same topics - "Discussing
    knee pain" in the summary and "Address knee pain concerns" as an action -
    and the pane reads as if it is repeating itself.

    Biased towards KEEPING: losing a real action is worse than showing a
    duplicate, so the cutoff sits nearer the restatements than the genuine
    ones, and a bullet is only ever compared whole."""
    if not actions.strip() or not summary.strip():
        return actions
    heads = [l[2:].strip(" .").lower() for l in summary.splitlines()
             if l.startswith("- ")]
    kept = []
    for line in actions.splitlines():
        body = line[2:].strip(" .").lower() if line.startswith("- ") else ""
        hit = None
        if body:
            for head in heads:
                if difflib.SequenceMatcher(None, body, head).ratio() >= RESTATE_RATIO:
                    hit = head
                    break
        if hit:
            log.info("dropped action restating the summary: %r", line.strip())
            continue
        kept.append(line)
    return "\n".join(kept)


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
    # a 3-tuple, not three arguments: the tests monkeypatch this with a
    # two-parameter lambda and must not have to care what is inside the first
    system, text, fmt = prompt_and_text
    body = {"model": cfg["cleanup_model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": text}],
            "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 1024}}
    if fmt:
        body["format"] = fmt
    r = requests.post(f"{cfg['ollama_url']}/api/chat", json=body,
                      timeout=(1.0, 300))   # CPU-only: a reduce can take minutes
    return r.json()


def _ask(system, text, cfg, fmt=None) -> str | None:
    try:
        body = _chat((system, text, fmt), cfg)
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


def _ask_list(prompt: str, material: str, cfg, key: str) -> str | None:
    """One JSON list from one call, as "- " bullets. None means the call itself
    failed; "" means the model returned an empty list, which is a real answer."""
    reply = _ask(prompt, material, cfg, fmt="json")
    if reply is None:
        return None
    bullets = _json_list(reply, key)
    if bullets is None:
        # Not JSON: an older Ollama, or a model that ignores the format flag.
        log.info("%s reply was not JSON, reading it as prose", key)
        bullets = _bullets(reply)
    return bullets


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
    summary = _ask_list(SUMMARY_PROMPT, material, cfg, "summary")
    if summary is None:
        return None
    # actions failing must not cost the summary: a meeting with notes and no
    # action list is still worth reading, an empty one is not.
    actions = _ask_list(ACTIONS_PROMPT, material, cfg, "actions") or ""

    summary = numbers_guard(summary, transcript).strip()
    actions = strip_owners(numbers_guard(actions, transcript)).strip()
    # last, so it compares the bullets as they will actually be shown
    actions = drop_restatements(actions, summary).strip()
    if not summary:
        # An empty parse is a FAILURE, not a summary. Returning "- (empty
        # summary)" here is how a totally broken reduce pass shipped unnoticed:
        # it looked like a result, so nothing upstream ever complained.
        log.info("summary parsed to nothing")
        return None
    return summary, actions or "- none"
