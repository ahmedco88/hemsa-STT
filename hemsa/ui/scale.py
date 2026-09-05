"""Windows DPI: draw at the monitor's real pixels, then scale our own sizes.

Without SetProcessDpiAwareness a process is told the screen is 96 DPI and
smaller than it is; Windows then bitmap-STRETCHES the finished window up to the
user's scale. That stretch is why every glyph and every card edge looked soft at
125%. Awareness has to be set before tk.Tk(), the same constraint the private
fonts have, so set_dpi_aware() sits next to fonts.load_private_fonts() in main().

Tk then scales POINT-sized things by itself - theme.F is all points, so the type
grows on its own - but nothing in Tk scales a PIXEL. A width=, a padx= or a
canvas coordinate stays exactly where it was while the text around it grows, and
the layout tightens until it clips. px() is that missing multiplier: every
hand-written pixel in the UI goes through it.

K stays 1.0 until init() runs, so tests and any non-Windows host see the sizes
the code literally says. Do NOT bake px() into a module-level constant - it is
read at import, long before a root exists. Same trap as theme.F.
"""

import ctypes
import logging

log = logging.getLogger("hemsa.scale")

K = 1.0
SYSTEM_AWARE = 1        # PROCESS_SYSTEM_DPI_AWARE


def set_dpi_aware() -> None:
    """System-DPI-aware, not per-monitor. Tk 8.6 reads the DPI once at startup
    and has no rescale path, so per-monitor awareness would only trade a blurry
    window on a second, differently-scaled monitor for a wrongly-sized one."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(SYSTEM_AWARE)
    except AttributeError:                    # not Windows
        pass
    except OSError as exc:                    # already set (E_ACCESSDENIED), fine
        log.info("dpi awareness already set: %s", exc)


def init(root) -> float:
    """Read the real DPI back off Tk and set the multiplier. Clamped: a screen
    that reports something absurd must not blow the window up off the desktop."""
    global K
    try:
        K = min(3.0, max(1.0, root.winfo_fpixels("1i") / 96.0))
    except Exception:                         # a screen Tk cannot measure
        log.exception("could not read screen dpi, staying at 1x")
        K = 1.0
    log.info("dpi scale: %.2fx", K)
    return K


def px(n: float) -> int:
    """A hand-written pixel, in real screen pixels."""
    return round(n * K)
