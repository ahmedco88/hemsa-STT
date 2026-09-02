"""Right-click menu on the orb - the quick-access set, not a copy of the tray.

Deliberately shorter than the tray menu: the orb is where the hand already is
mid-dictation, so it carries what you want IN that moment (the last transcript,
the word list, pause) and leaves Theme / Cleanup / Stats / About / Updates to the
tray.

TWO focus rules, and both exist because the orb is a no-activate window whose
whole job is never to take the caret away from what you are dictating into:

  * The target window is captured BEFORE the menu opens, in `popup`. A popup menu
    takes the foreground, so anything reading `foreground_window()` from inside a
    menu command gets the menu's own owner, not the user's text field. That is the
    "dictation lands nowhere" bug wearing a different hat.
  * Commands are POSTED to the app queue, not run inline. A menu command runs on
    the Tk callback stack with the menu's grab still unwinding; pasting from
    inside it fights that grab. Posting also keeps this file on the same thread
    contract as the tray.

tkinter menus cannot show the mock's per-item icons (menu entries take a Tk image,
not an icon font). The order, grouping and the transcript preview line are the
parts that carry the meaning, and those are here.
"""

import logging
import tkinter as tk

from .. import palette as P
from .. import winutil

log = logging.getLogger("hemsa.orb_menu")

PREVIEW_CHARS = 38


def _preview(text: str) -> str:
    one_line = " ".join(text.split())
    if len(one_line) > PREVIEW_CHARS:
        one_line = one_line[:PREVIEW_CHARS].rstrip() + "…"
    return f'"{one_line}"'


class OrbMenu:
    def __init__(self, root: tk.Tk, app):
        self._root = root
        self._app = app
        self._menu: tk.Menu | None = None

    # The menu is rebuilt on every open: the preview line, the enabled state of
    # the two transcript items and the Pause tick all change between opens.
    def _build(self) -> tk.Menu:
        app, ctl = self._app, self._app.ctl
        m = tk.Menu(self._root, tearoff=0,
                    bg=P.DARK_CARD, fg=P.DARK_ACCENT,
                    activebackground=P.DARK_ACCENT, activeforeground=P.DARK_CARD,
                    disabledforeground=P.MUTED, selectcolor=P.DARK_ACCENT,
                    borderwidth=1, relief="solid", font=("Segoe UI", 9))

        has_text = bool(ctl.last_text)
        state = "normal" if has_text else "disabled"
        if has_text:
            m.add_command(label=_preview(ctl.last_text), state="disabled")
        m.add_command(label="Copy last transcript", state=state,
                      command=lambda: app.post(ctl.copy_last))
        m.add_command(label="Paste it again", state=state,
                      command=lambda h=self._target: app.post(lambda: ctl.paste_last(h)))

        m.add_separator()
        # posted like every other command here: opening a window does not need the
        # captured hwnd, but running it inline still fights the menu's own grab.
        m.add_command(label="Meetings…", command=lambda: app.post(app.open_meetings))
        m.add_command(label="Word list…", command=lambda: app.post(app.open_dictionary))
        m.add_command(label="History…", command=lambda: app.post(app.open_history))
        m.add_command(label="Settings…", command=lambda: app.post(app.open_settings))

        m.add_separator()
        paused = tk.BooleanVar(master=self._root, value=not app.cfg["hotkey_enabled"])
        self._paused = paused          # a checkbutton's variable must outlive _build
        m.add_checkbutton(label="Pause hotkey", variable=paused,
                          command=lambda: app.post(app.toggle_hotkey))
        m.add_command(label="Hide orb", command=lambda: app.post(app.toggle_orb))

        m.add_separator()
        m.add_command(label="Quit Hemsa", command=lambda: app.post(app.quit))
        return m

    def popup(self, x: int, y: int) -> None:
        # BEFORE the menu exists - see the module docstring.
        self._target = winutil.foreground_window()
        if self._menu is not None:
            self._menu.destroy()
        self._menu = self._build()
        try:
            self._menu.tk_popup(x, y)
        finally:
            # Without this the global grab can outlive the menu and the desktop
            # stops responding to clicks until Hemsa is killed.
            self._menu.grab_release()
