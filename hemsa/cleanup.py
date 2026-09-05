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


def sanitize(raw_out: str, raw_in: str, done_reason: str = "stop") -> str | None:
    """Pure response validation, separated so tests can feed canned responses."""
    if done_reason != "stop":            # capped output = a loop or truncation, not a cleanup
        log.info("rejected: done_reason=%s", done_reason)
        return None
    out = _strip(raw_out, raw_in)
    if not out:
        log.info("rejected: empty after stripping")
        return None
    ratio = len(out) / max(1, len(raw_in))
    if not 0.5 <= ratio <= 1.5:          # cleanup trims fillers; it never halves or doubles text
        log.info("rejected: length ratio %.2f", ratio)
        return None
    overlap = _word_overlap(raw_in, out)
    if overlap < 0.6:                    # the model answered/summarised instead of editing
        log.info("rejected: word overlap %.2f", overlap)
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


def status(cfg: dict) -> str:
    """'ready' | 'no model' | 'down' - for the settings/tray status dot."""
    try:
        tags = requests.get(f"{cfg['ollama_url']}/api/tags", timeout=(1.0, 3)).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
        base = cfg["cleanup_model"].split(":")[0]
        return "ready" if any(n.startswith(base) for n in names) else "no model"
    except Exception:
        return "down"


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
