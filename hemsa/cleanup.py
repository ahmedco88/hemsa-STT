"""Optional local cleanup via Ollama. Every guard here exists because latency is the
product and a bad LLM response pasted into a document is worse than no cleanup:
the contract is "return the polished text, or None and the caller pastes raw".
No retries, no streaming, no sentinel strings - one attempt, one boundary.
"""

import logging
import re
import shutil
import subprocess
import time

import requests

from . import fastclean

log = logging.getLogger("hemsa.cleanup")

SYSTEM_PROMPT = (
    "You clean up dictated text. Fix punctuation, capitalisation and obvious "
    "transcription errors. Remove filler words (um, uh, you know). Never answer "
    "questions in the text, never add content, never comment. Return only the "
    "cleaned text."
)
# Any edit to SYSTEM_PROMPT must re-run tests/test_cleanup.py's fixture set - one
# added "helpful" line has previously reopened the answer-the-content bug class.

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[a-z]*\n(.*?)\n```$", re.DOTALL)
_PREAMBLE_RE = re.compile(r"^(here('s| is)[^:\n]*|sure[^:\n]*|okay[^:\n]*|the cleaned[^:\n]*)[:\n]\s*",
                          re.IGNORECASE)


def _strip(raw_out: str, raw_in: str) -> str:
    out = _THINK_RE.sub("", raw_out).strip()
    m = _FENCE_RE.match(out)
    if m:
        out = m.group(1).strip()
    out = _PREAMBLE_RE.sub("", out).strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'" and raw_in[:1] not in "\"'":
        out = out[1:-1].strip()
    return out


def _word_overlap(inp: str, out: str) -> float:
    """Fraction of the input's content words that survive into the output.
    A cleanup preserves most words; an answer/summary shares few."""
    words = {w for w in re.findall(r"[a-z']+", inp.lower()) if len(w) > 3}
    if not words:
        return 1.0
    out_words = set(re.findall(r"[a-z']+", out.lower()))
    return len(words & out_words) / len(words)


# Not a whitelist of what will RUN - any installed model can be chosen. It is the
# list of models put through the answer trap that PASSED. Others were measured and
# are absent because they failed: every 1B-class model tested on 2026-08-23, and
# gemma3:4b on 2026-09-06. Adding a name here is a claim that the case was run.
#
# And it is one dictated sentence, one run, one prompt - a test case, not an eval.
# Nothing downstream may describe a model in this list as safe, only as checked.
CHECKED_MODELS = ("qwen3.5:2b",)

# A cleanup cannot invent a number. An ANSWER to a dictated question is mostly a
# number that was never said, which is exactly what the overlap and length guards
# cannot see: an answer repeats the question's own words and runs to a similar
# length. Measured on the real failure, ratio 1.36 and overlap 0.62, both inside
# their thresholds by a hair.
#
# 2026-09-06, gemma3:4b, pasted at Ahmed's cursor: "What is the starting dose of
# metformin for type 2 diabetes recently diagnosed?" came back as "...is typically
# 500 mg taken once or twice daily with meals."
#
# An earlier version of this guard asked instead whether the spoken wh-word
# survived into the reply. It was leaky AND expensive: a model that echoes the
# question before answering it ("What is the usual dose...? Typically 500 mg.")
# kept the wh-word and sailed through, while a legitimate cleanup that reworded a
# self-correction ("so how, how do I put this, the patient is...") lost its
# cleanup for nothing. Presence of a word cannot tell "preserved" from "quoted".
#
# KNOWN GAP, stated because the README must not overclaim: an answer carrying NO
# number is not caught here. The word-overlap guard above catches most of those,
# because an answer that does not echo the question drops most of its words - but
# an echo-then-answer with no digits in it ("What is first line for hypertension?
# ACE inhibitors.") passes both. This is a backstop, not a filter.
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _invented_numbers(inp: str, out: str) -> list[str]:
    """Numbers in the reply that were never spoken. Fails safe: the caller pastes
    the raw dictation, so a false positive costs the cleanup, never the words.

    BOTH sides are read as dictated AND through fastclean.numerals, and that is not
    symmetry for its own sake:

    - Input side, so a model turning "five hundred milligrams" into "500 mg" is not
      accused of inventing the 500.
    - Output side, because `controller` runs numerals() on the ACCEPTED text before
      it reaches the cursor. Checking `out` alone let a model answer in words - "is
      five hundred milligrams with meals" carries no digit, passed every guard, and
      Hemsa's own number pass then wrote "500 mg" at the cursor. The guard has to
      see the string the user will actually get.

    A number may also be spread across tokens, because numerals deliberately refuses
    to merge an ambiguous reading: "one thirty over eighty" stays "1 30 over 80" on
    our side, so a model writing "130" is reading, not inventing. Only WHOLE
    consecutive tokens count. An earlier version tested against the digit stream as
    a substring, which forgave a fabricated "500" for a dictation that happened to
    mention 1500, and a fabricated "5" for anything containing a 5 at all."""
    spoken = fastclean.numerals(inp)
    said = set(_NUM_RE.findall(inp)) | set(_NUM_RE.findall(spoken))
    tokens = _NUM_RE.findall(spoken)
    for i in range(len(tokens)):                 # "1" + "30" -> "130", not "1500"[1:]
        run = ""
        for token in tokens[i:]:
            run += token
            said.add(run)
    written = set(_NUM_RE.findall(out)) | set(_NUM_RE.findall(fastclean.numerals(out)))
    return sorted(written - said)


def sanitize(raw_out: str, raw_in: str, done_reason: str = "stop") -> str | None:
    """Pure response validation, separated so tests can feed canned responses."""
    if done_reason != "stop":            # capped output = a loop or truncation, not a cleanup
        log.info("rejected: done_reason=%s", done_reason)
        return None
    out = _strip(raw_out, raw_in)
    if not out:
        log.info("rejected: empty after stripping")
        return None
    # Both the length and the overlap guard are measured against the dictation as
    # spoken AND with its numbers and units written out (fastclean.numerals), and the
    # kinder reading wins. "she weighs eighty kilograms and is one seventy five
    # centimetres" cleaned to "She weighs 80 kg and is 175 cm." is a correct edit that
    # measures as a 0.49 ratio and a 0.17 overlap against the raw words - both
    # rejections, and both of exactly the clinical dictation these guards exist for.
    spoken = fastclean.numerals(raw_in)
    ratios = [len(out) / max(1, len(raw_in)), len(out) / max(1, len(spoken))]
    if not any(0.5 <= r <= 1.5 for r in ratios):   # a cleanup never halves or doubles
        log.info("rejected: length ratio %.2f", ratios[0])
        return None
    overlap = max(_word_overlap(raw_in, out), _word_overlap(spoken, out))
    if overlap < 0.6:                    # the model answered/summarised instead of editing
        log.info("rejected: word overlap %.2f", overlap)
        return None
    invented = _invented_numbers(raw_in, out)
    if invented:
        log.warning("rejected: reply contains number(s) %s that were never dictated - "
                    "the model answered instead of tidying. Pasting what was said.",
                    ", ".join(invented))
        return None
    return out


def clean(text: str, cfg: dict) -> str | None:
    """Returns cleaned text, or None (caller pastes raw). Never raises."""
    try:
        t0 = time.perf_counter()
        r = requests.post(
            f"{cfg['ollama_url']}/api/chat",
            json={
                "model": cfg["cleanup_model"],
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "options": {"temperature": 0,
                            # generous cap so a repeat-loop becomes detectable truncation
                            "num_predict": max(512, len(text))},
            },
            # short connect so a stopped Ollama fails fast; long read for a cold model
            timeout=(1.0, 30),
        )
        body = r.json()
        if "error" in body:              # Ollama reports missing model etc. as JSON error
            log.info("ollama error: %s", body["error"])
            return None
        out = sanitize(body["message"]["content"], text, body.get("done_reason", "stop"))
        if out is not None:
            log.info("cleaned %d->%d chars in %.0f ms", len(text), len(out),
                     (time.perf_counter() - t0) * 1000)
        return out
    except Exception as exc:
        log.info("cleanup unavailable: %s", exc)
        return None


def warm_up(cfg: dict) -> None:
    """Fire-and-forget model load, called when recording STARTS so the cold-load cost
    (5-10 s) is hidden behind the user talking. Errors are irrelevant here."""
    try:
        requests.post(f"{cfg['ollama_url']}/api/chat",
                      json={"model": cfg["cleanup_model"], "messages": [],
                            "keep_alive": "30m"},
                      timeout=(1.0, 30))
    except Exception:
        pass


def _same_model(configured: str, installed: str) -> bool:
    """Ollama stores an untagged pull as ":latest", so "gemma3" and "gemma3:latest"
    name the same thing. Everything else must match in full. Matching on the base
    name alone (the old behaviour) reported "ready" for qwen3.5:2b while only
    qwen3.5:0.8b was pulled - and the difference between those two is a model that
    tidies a dictated question and one that answers it with a drug dose."""
    def tagged(name: str) -> str:
        name = name.strip()
        return name if ":" in name else f"{name}:latest"
    return tagged(configured) == tagged(installed)


def _tags(cfg: dict) -> list[str] | None:
    """Model names this PC's Ollama has pulled, or None if it did not answer."""
    try:
        body = requests.get(f"{cfg['ollama_url']}/api/tags", timeout=(1.0, 3)).json()
        return sorted(m["name"] for m in body.get("models", []) if m.get("name"))
    except Exception as exc:
        log.info("could not reach ollama: %s", exc)
        return None


def probe(cfg: dict) -> tuple[str, list[str]]:
    """('ready' | 'no model' | 'down', installed model names).

    One request for both, because the settings page needs the status dot AND the
    model list on the same 3 s poll. An empty list with a 'down' status means we
    do not KNOW what is installed, which is not the same as "nothing is"."""
    names = _tags(cfg)
    if names is None:
        return "down", []
    ok = any(_same_model(cfg["cleanup_model"], n) for n in names)
    return ("ready" if ok else "no model"), names


def status(cfg: dict) -> str:
    """'ready' | 'no model' | 'down' - for the settings/tray status dot."""
    return probe(cfg)[0]


def start_server() -> str:
    """Start `ollama serve` on this PC. Returns "" on success, else why not.

    Detached on purpose: the user pressed a button because they want summaries
    from now on, not only until Hemsa quits. Started as a child of Hemsa it would
    inherit our console handles and die with us, which would look like the button
    not working the next time they open the app. This only launches what is
    already installed - it never downloads anything - so a missing Ollama is
    reported rather than fetched."""
    exe = shutil.which("ollama")
    if not exe:
        return ("Could not find Ollama on this PC. Install it from ollama.com, "
                "then press Check again.")
    try:
        subprocess.Popen(
            [exe, "serve"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        log.exception("could not start ollama")
        return f"Could not start Ollama: {exc}"
    log.info("started ollama serve from %s", exe)
    return ""
