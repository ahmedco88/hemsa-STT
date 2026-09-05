"""Hemsa colours - the multi-theme registry, and the ONLY file allowed to
contain hex values.

CARD/INK/MUTED/ACCENT/ACCENT_LIT/DEEP and the whole dark group come from the
author's house design system (palettes.css), copied verbatim and WCAG-measured as
pairs - never re-tune a value by eye. PAPER, LINE and MIST are Hemsa's own
(2026-09-03): a near-neutral warm cream with a faint theme bias, so the four themes
share one ground and differ by accent; every text-on-surface pair was measured by
color-token-guard before shipping. Ochre and forest are deliberately excluded from
Hemsa: ochre's base equals the shared WARN ink and forest's greens collide with OK.

Consumers must `from .. import palette as P` and read attributes at use time.
Never `from ..palette import ACCENT` - that freezes the value at import and
silently ignores theme switches. set_theme() rebinds the module-level names.

tkinter note: ttk widgets need Style().configure with theme 'clam' - bg/fg
kwargs are silently ignored on the Windows default theme.
"""

# The slot contract. Every theme must define exactly these keys - no fallbacks,
# a partial theme is a startup error, never a silently wrong colour.
_SLOTS = frozenset({
    # light group (settings / history / dictionary / stats windows)
    "PAPER", "CARD", "INK", "MUTED", "LINE", "MIST", "ACCENT", "ACCENT_LIT", "DEEP",
    # dark group (HUD pill, orb, tray icon surfaces)
    "DARK_GROUND", "DARK_CARD", "DARK_INK", "DARK_MUTED", "DARK_LINE", "DARK_MIST",
    "DARK_ACCENT", "DARK_ACCENT_LIT", "DARK_DEEP",
    # semantic: text sitting on an ACCENT fill (design system: text-on-accent = card)
    "TEXT_ON_ACCENT",
})

THEMES = {
    "plum": {
        "PAPER": "#F4F2F1", "CARD": "#FFFFFF", "INK": "#1B1626", "MUTED": "#625A75",
        "LINE": "#E6E2E3", "MIST": "#ECE8EA", "ACCENT": "#5B47A8",
        "ACCENT_LIT": "#8D7BD8", "DEEP": "#3C2A6B",
        "DARK_GROUND": "#150F24", "DARK_CARD": "#201833", "DARK_INK": "#F0EBFA",
        "DARK_MUTED": "#ADA2C4", "DARK_LINE": "#2C2245", "DARK_MIST": "#3A2E5A",
        "DARK_ACCENT": "#B7A7EC", "DARK_ACCENT_LIT": "#9E8AE2", "DARK_DEEP": "#0E0A18",
        "TEXT_ON_ACCENT": "#FFFFFF",
    },
    "navy": {
        "PAPER": "#F2F3F4", "CARD": "#FFFFFF", "INK": "#0F1B2A", "MUTED": "#54657E",
        "LINE": "#E2E5E8", "MIST": "#E8EBEE", "ACCENT": "#17457C",
        "ACCENT_LIT": "#3B8FD4", "DEEP": "#0E2C4F",
        "DARK_GROUND": "#0B1725", "DARK_CARD": "#132436", "DARK_INK": "#E8EFF7",
        "DARK_MUTED": "#9DB0C6", "DARK_LINE": "#1E3247", "DARK_MIST": "#24405C",
        "DARK_ACCENT": "#8FBEE9", "DARK_ACCENT_LIT": "#6FB3E8", "DARK_DEEP": "#081019",
        "TEXT_ON_ACCENT": "#FFFFFF",
    },
    "teal": {
        "PAPER": "#F1F3F2", "CARD": "#FFFFFF", "INK": "#0E1F1A", "MUTED": "#4E6961",
        "LINE": "#E0E6E3", "MIST": "#E6ECE9", "ACCENT": "#0F6E56",
        "ACCENT_LIT": "#12A594", "DEEP": "#0B3D31",
        "DARK_GROUND": "#0A1714", "DARK_CARD": "#12241F", "DARK_INK": "#E6F2EE",
        "DARK_MUTED": "#9BB5AC", "DARK_LINE": "#1D332C", "DARK_MIST": "#23453B",
        "DARK_ACCENT": "#6FCFB6", "DARK_ACCENT_LIT": "#4FC3A8", "DARK_DEEP": "#061210",
        "TEXT_ON_ACCENT": "#FFFFFF",
    },
    "inkblue": {
        "PAPER": "#F2F3F5", "CARD": "#FFFFFF", "INK": "#14181F", "MUTED": "#656D78",
        "LINE": "#E2E5EA", "MIST": "#EBEEF3", "ACCENT": "#2F5FD0",
        "ACCENT_LIT": "#6D93E8", "DEEP": "#1E3A7B",
        "DARK_GROUND": "#0C1424", "DARK_CARD": "#151E33", "DARK_INK": "#EBEFF9",
        "DARK_MUTED": "#A3AEC6", "DARK_LINE": "#202B45", "DARK_MIST": "#2A3859",
        "DARK_ACCENT": "#9DB6F0", "DARK_ACCENT_LIT": "#7E9DEA", "DARK_DEEP": "#080E1A",
        "TEXT_ON_ACCENT": "#FFFFFF",
    },
}

DEFAULT = "plum"
CHOICES = tuple(THEMES)
LABELS = {"plum": "Plum", "navy": "Navy", "teal": "Teal", "inkblue": "Ink blue"}

# Loud, at import: a theme missing or inventing a slot must never ship.
for _name, _t in THEMES.items():
    assert set(_t) == _SLOTS, f"theme {_name!r} slot mismatch: {set(_t) ^ _SLOTS}"

# --- shared status colours (meaning, not brand - identical in every theme) ---
OK = "#2E9E86"
WARN = "#A85B10"
DANGER = "#B4232A"
REC = "#E15B72"          # recording dot/pulse on dark surfaces (danger family, dark-legible)
OK_INK = "#1B6F5D"       # OK as WORDS (5.3:1 on white). OK itself is a dot colour at 3.3:1
TRANSPARENT_KEY = "#010203"   # -transparentcolor key for HUD / orb / chip, never visible

_current = DEFAULT


def set_theme(name: str) -> str:
    """Rebind the module-level colour names to the named theme.

    Unknown names fall back to DEFAULT (config.json may hold junk); returns the
    name actually applied so callers can normalise their config.
    """
    global _current
    if name not in THEMES:
        name = DEFAULT
    globals().update(THEMES[name])
    _current = name
    return name


def current() -> str:
    return _current


set_theme(DEFAULT)
