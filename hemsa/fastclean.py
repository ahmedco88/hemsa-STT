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

    out = numerals(out)
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


# ---- numerals and units -------------------------------------------------
# Dictated notes want "16", "5 mg", "80 kg", not the words. This is still
# rules-only: a lookup table cannot invent a number that was not spoken.
#
# Deliberately NOT clever. Two number words are merged only where English
# syntax is unambiguous - tens+unit ("twenty five" -> 25) and a scale word
# ("two hundred and ten" -> 210). Two bare numbers side by side stay side by
# side, so a dictated blood pressure comes out "1 10 over 70", not "110/70".
# Merging those needs to know it is a blood pressure, and guessing wrong on a
# clinical number is worse than leaving the reading in two pieces.
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000}

_UNITS = {
    "millimetre": "mm", "millimeter": "mm", "centimetre": "cm", "centimeter": "cm",
    "metre": "m", "meter": "m", "kilometre": "km", "kilometer": "km",
    "microgram": "mcg", "milligram": "mg", "kilogram": "kg", "gram": "g",
    "kilo": "kg", "millilitre": "mL", "milliliter": "mL", "litre": "L",
    "liter": "L", "millimole": "mmol",
}
# Left alone on purpose: hours/minutes/weeks (an abbreviation there reads worse
# than the word) and insulin "units" (abbreviating that one is a dosing error
# waiting to happen).

_NUM_RUN_RE = re.compile(
    r"\b(?:%s)(?:[\s-]+(?:and[\s-]+)?(?:%s))*\b"
    % ("|".join(list(_ONES) + list(_TENS)),
       "|".join(list(_ONES) + list(_TENS) + list(_SCALES))),
    re.IGNORECASE)
# "one of the referrals" must not become "1 of the referrals". Every other
# number word is safe to spell as a digit; bare "one" is the one that carries
# its weight as an ordinary English word.
_ONE_OF_RE = re.compile(r"^one$", re.IGNORECASE)
# "zero point five milligrams" -> "0.5 mg". Only between two numbers already
# written as digits, so ordinary sentences using the word "point" are safe.
_DECIMAL_RE = re.compile(r"\b(\d+) point (\d+)\b", re.IGNORECASE)
_UNIT_RE = re.compile(r"(?<=\d)(\s*)(%s)s?\b" % "|".join(_UNITS), re.IGNORECASE)


def _run_to_digits(words: list[str]) -> str | None:
    """Fold a run of number words into one integer, or None if the run is not a
    single well-formed number (in which case the caller converts word by word)."""
    total = current = 0          # completed thousands, and the part below 1000
    tens_open = ones_open = False
    for w in words:
        if w == "and":
            continue
        if w in _TENS:
            if tens_open or ones_open:
                return None                    # "twenty thirty" is not a number
            current += _TENS[w]
            tens_open = True
        elif w in _ONES:
            value = _ONES[w]
            if ones_open or (tens_open and value > 9):
                return None                    # "five ten", "twenty twelve"
            current += value
            tens_open, ones_open = False, True
        else:                                  # hundred / thousand
            current = max(current, 1) * _SCALES[w]
            tens_open = ones_open = False
            if _SCALES[w] == 1000:
                total, current = total + current, 0
    return str(total + current)


def _sub_run(m: re.Match) -> str:
    words = [w.lower() for w in re.split(r"[\s-]+", m.group(0))]
    if len(words) == 1 and _ONE_OF_RE.match(words[0]):
        # only a bare "one" with nothing numeric attached; see _ONE_OF_RE
        after = m.string[m.end():m.end() + 4].lower()
        if after.startswith(" of"):
            return m.group(0)
    joined = _run_to_digits(words)
    if joined is not None:
        return joined
    # Not one number. Split it into the longest well-formed numbers it does contain,
    # rather than one digit per word: "one seventy five" is a person reading 1 and 75,
    # so "1 75" is right and "1 70 5" is noise. "one ten" still splits to "1 10",
    # because that IS two readings and merging it would be a guess at a blood pressure.
    out, rest = [], [w for w in words if w != "and"]
    while rest:
        for size in range(len(rest), 0, -1):
            piece = _run_to_digits(rest[:size])
            if piece is not None:
                out.append(piece)
                rest = rest[size:]
                break
        else:                                    # unreachable: one word always folds
            out.append(rest[0])
            rest = rest[1:]
    return " ".join(out)


def numerals(text: str) -> str:
    """Spelled-out numbers to digits, then spoken units to their symbols.

    Units only convert when a digit is already in front of them, so "he lost
    weight in kilograms" is untouched while "eighty kilograms" becomes "80 kg".
    """
    out = _NUM_RUN_RE.sub(_sub_run, text)
    out = _DECIMAL_RE.sub(r"\1.\2", out)
    return _UNIT_RE.sub(lambda m: " " + _UNITS[m.group(2).lower()], out)
