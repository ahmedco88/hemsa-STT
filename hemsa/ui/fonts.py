"""Load the bundled fonts for THIS process only, before Tk starts.

Windows GDI (which Tk draws with) enumerates fonts when the interpreter starts, so
AddFontResourceExW must run before tk.Tk(). FR_PRIVATE means nothing is installed,
nothing is left behind, and no admin rights are needed. A failure is a log line and
a fallback face, never a dialog: fonts are cosmetics, dictation is the job.
"""

import ctypes
import logging
from pathlib import Path

log = logging.getLogger("hemsa.fonts")

FOLDER = Path(__file__).resolve().parent.parent / "fonts"
FR_PRIVATE = 0x10

# file name -> the family name Tk will see. Keep in step with the shipped files.
FAMILIES = {
    "InstrumentSerif-Regular.ttf": "Instrument Serif",
    "InstrumentSerif-Italic.ttf": "Instrument Serif",
    "Figtree-Regular.ttf": "Figtree",
    "Figtree-Medium.ttf": "Figtree Medium",
    "Figtree-SemiBold.ttf": "Figtree SemiBold",
}


def load_private_fonts(folder: Path | None = None) -> set[str]:
    """Families that loaded. Empty on a non-Windows host or any failure."""
    folder = FOLDER if folder is None else folder
    loaded: set[str] = set()
    try:
        add = ctypes.windll.gdi32.AddFontResourceExW
    except AttributeError:                    # not Windows
        return loaded
    for name, family in FAMILIES.items():
        path = folder / name
        if not path.is_file():
            log.warning("font missing: %s", path)
            continue
        try:
            if add(str(path), FR_PRIVATE, 0):
                loaded.add(family)
            else:
                log.warning("font refused by GDI: %s", path)
        except Exception:                     # ctypes oddities, keep going
            log.exception("font load failed: %s", path)
    log.info("fonts loaded: %s", sorted(loaded) or "none (system fallbacks)")
    return loaded
