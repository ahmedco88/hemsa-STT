r"""Score the meeting summariser against a transcript whose answer is known.

Why this exists: `tests/test_summarize.py` feeds CANNED model output, so it proves
the parsing, the guards and the owner-stripping - and nothing at all about what the
model actually writes. Every test can pass while the summary silently drops half
the decisions. This runs the REAL `summarize.summarize` against the REAL Ollama and
scores what came back, so a prompt or model change can be compared instead of
eyeballed.

The transcript is SYNTHETIC and stays that way: it is committed to a public repo,
and a real meeting cannot be. It is written to bait the four failures this
summariser is known to have -
  * a decision that is agreed and then dropped from ACTIONS,
  * a negated fact reported with the negation flipped,
  * an action handed to the wrong person (the prompt forbids owners entirely),
  * a question in the transcript answered by the model instead of reported.

    .venv\Scripts\python.exe scripts\eval_summary.py [--model qwen3:4b] [--runs 3]

Exit code is 0 whatever the score: this is a measurement, not a gate. Nothing here
is clinical advice or a clinical test - it measures text handling only.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hemsa import config, summarize                                  # noqa: E402

# ---------------------------------------------------------------------------
# The transcript. Two staff of a fictional clinic, no patient identifiers, no
# real people. "Them" is the second speaker exactly as meeting_audio labels it.
# ---------------------------------------------------------------------------
TURNS = [
    ("me",   "Right, three things today: the flu clinic, the new printer and "
             "the roster for the long weekend."),
    ("them", "Let's start with the flu clinic. We had 40 people booked last "
             "Saturday and 12 did not turn up."),
    ("me",   "That is a lot of waste. Can we send a reminder text the day "
             "before?"),
    ("them", "We can. I will set the reminder to go out at four in the "
             "afternoon the day before."),
    ("me",   "Good. Book the next flu clinic for the 18th, and let's cap it at "
             "35 so we are not standing around."),
    ("them", "Booked for the 18th, capped at 35. The printer is the bigger "
             "problem. It jams every second print run."),
    ("me",   "Is it still under warranty?"),
    ("them", "I do not know. I will dig out the invoice and check the warranty "
             "before Friday."),
    ("me",   "If it is out of warranty, get a quote for a replacement rather "
             "than a repair. We are not spending more on that machine."),
    ("them", "Understood. And we have NOT had any complaints from patients "
             "about the wait times since we changed the appointment length. "
             "None at all this month."),
    ("me",   "That is good to hear. Do not change it back then."),
    ("them", "Last thing, the roster. Sam is away the whole long weekend and "
             "Priya has asked for the Monday off."),
    ("me",   "Then we are two down on the Monday. Ask the agency for one "
             "locum for the Monday only."),
    ("them", "Will do. One more thing I keep meaning to ask - what dose of "
             "vitamin D should we be suggesting for the older patients?"),
    ("me",   "That is not a five minute conversation. Put it on the agenda "
             "for the clinical meeting and we will go through it properly."),
    ("them", "Fair enough. I will add it to the clinical meeting agenda."),
]

SEGMENTS = [{"start": i * 18, "channel": ch, "text": t}
            for i, (ch, t) in enumerate(TURNS)]

# ---------------------------------------------------------------------------
# What a correct summary must contain. Each check is (label, predicate).
# Keyword matching, deliberately loose: this measures whether the DECISION
# survived, not whether the wording matched.
# ---------------------------------------------------------------------------


def _has(text, *words):
    low = text.lower()
    return all(w.lower() in low for w in words)


def _any(text, *options):
    low = text.lower()
    return any(o.lower() in low for o in options)


ACTIONS_EXPECTED = [
    ("reminder text the day before", lambda s, a: _has(a, "reminder")),
    ("book the clinic for the 18th", lambda s, a: _any(a, "18th", "18")),
    ("cap the clinic at 35",         lambda s, a: _any(a, "35", "cap")),
    ("check the printer warranty",   lambda s, a: _has(a, "warranty")),
    ("quote for a replacement",      lambda s, a: _any(a, "quote", "replacement")),
    ("locum for the Monday",         lambda s, a: _any(a, "locum", "agency")),
    ("vitamin D onto the agenda",    lambda s, a: _has(a, "agenda")),
]

SUMMARY_EXPECTED = [
    ("12 no-shows mentioned",      lambda s, a: _any(s, "12", "no-show", "not turn up",
                                                     "did not turn")),
    ("printer jamming mentioned",  lambda s, a: _any(s, "jam", "printer")),
    ("roster gap mentioned",       lambda s, a: _any(s, "roster", "monday", "away")),
]

# Things that must NOT happen. These are the failures, not the misses.
FORBIDDEN = [
    ("answered the vitamin D question with a dose",
     lambda s, a: bool(re.search(r"\b\d+\s*(iu|mcg|microgram|unit)", (s + a).lower()))
     or _any(s + a, "iu daily", "800 iu", "1000 iu")),
    ("flipped the complaints fact (says there WERE complaints)",
     lambda s, a: bool(re.search(r"(?<!no )complaints (were|have been) (received|made)",
                                 (s + a).lower()))
     or _any(s + a, "complaints about the wait", "patients complained")),
    ("an ACTION names a person",
     lambda s, a: _any(a, "sam", "priya", "me -", "them -", " i ", " we ")),
]


def score(summary: str, actions: str) -> dict:
    got_actions = [(label, fn(summary, actions)) for label, fn in ACTIONS_EXPECTED]
    got_summary = [(label, fn(summary, actions)) for label, fn in SUMMARY_EXPECTED]
    broke = [(label, fn(summary, actions)) for label, fn in FORBIDDEN]
    return {"actions": got_actions, "summary": got_summary, "forbidden": broke}


def show(run: int, summary: str, actions: str, secs: float) -> dict:
    print(f"\n{'=' * 72}\nRUN {run}   ({secs:.1f} s)\n{'=' * 72}")
    print("SUMMARY\n" + (summary or "(none)"))
    print("\nACTIONS\n" + (actions or "(none)"))
    s = score(summary, actions)
    print("\n-- decisions that had to survive --")
    for label, ok in s["actions"]:
        print(f"  {'HIT ' if ok else 'MISS'}  {label}")
    print("-- context that had to survive --")
    for label, ok in s["summary"]:
        print(f"  {'HIT ' if ok else 'MISS'}  {label}")
    print("-- must NOT happen --")
    for label, bad in s["forbidden"]:
        print(f"  {'FAIL' if bad else 'ok  '}  {label}")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="override cfg cleanup_model")
    ap.add_argument("--runs", type=int, default=3,
                    help="repeat: temperature 0 is not determinism")
    args = ap.parse_args()

    cfg = config.load()
    if args.model:
        cfg["cleanup_model"] = args.model
    words = sum(len(t.split()) for _, t in TURNS)
    print(f"model      : {cfg['cleanup_model']}")
    print(f"transcript : {len(TURNS)} turns, {words} words "
          f"({len(summarize.split_pieces(SEGMENTS))} piece(s), so "
          f"{'map+reduce' if len(summarize.split_pieces(SEGMENTS)) > 1 else 'reduce only'})")

    tally = {"actions": {}, "summary": {}, "forbidden": {}}
    for run in range(1, args.runs + 1):
        t0 = time.monotonic()
        out = summarize.summarize(SEGMENTS, cfg)
        secs = time.monotonic() - t0
        if out is None:
            print(f"\nRUN {run}: summarize() returned None after {secs:.1f} s "
                  "(Ollama down, model missing, or the response was rejected)")
            continue
        s = show(run, *out, secs=secs)
        for section in tally:
            for label, flag in s[section]:
                tally[section].setdefault(label, 0)
                tally[section][label] += int(flag)

    print(f"\n{'=' * 72}\nACROSS {args.runs} RUNS\n{'=' * 72}")
    for section, header in (("actions", "decisions kept"),
                            ("summary", "context kept"),
                            ("forbidden", "rule broken")):
        print(f"-- {header} --")
        for label, n in tally[section].items():
            print(f"  {n}/{args.runs}  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
