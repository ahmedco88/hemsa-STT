"""Windows plumbing: no-activate windows, screen-edge snap, single instance, autostart.
The no-activate pattern (including the GetParent redirect for overrideredirect Tk
windows) is proven in a sibling project.
"""

import ctypes
import sys
from pathlib import Path

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def set_noactivate(win) -> None:
    """Clicking this Tk window must not steal keyboard focus from the text field the
    user is dictating into - this is the load-bearing detail of the whole app.
    WS_EX_TOOLWINDOW also keeps it out of Alt+Tab."""
    try:
        user32 = ctypes.windll.user32
        hwnd = win.winfo_id()
        parent = user32.GetParent(hwnd)   # overrideredirect Tk: real top-level is the parent
        target = parent if parent else hwnd
        ex = user32.GetWindowLongW(target, GWL_EXSTYLE)
        user32.SetWindowLongW(target, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def work_area() -> tuple[int, int, int, int]:
    """Primary monitor work area (excludes taskbar): left, top, right, bottom."""
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rect = RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
        return rect.l, rect.t, rect.r, rect.b
    return 0, 0, 1920, 1040


def _top_level_hwnd(win) -> int:
    """The real Win32 top-level behind a Tk window. An overrideredirect Toplevel
    is a CHILD of the window Windows actually owns, so every ctypes call about it
    has to go through GetParent first."""
    user32 = ctypes.windll.user32
    hwnd = win.winfo_id()
    parent = user32.GetParent(hwnd)
    return parent if parent else hwnd


def is_window_visible(win) -> bool:
    """Does Windows consider this window shown? Deliberately NOT Tk's state():
    an exclusive-fullscreen app, a session lock or a display change can have the
    OS hide a topmost tool window without Tk ever hearing about it, and Tk then
    keeps reporting "normal" for a window that is not on screen."""
    try:
        return bool(ctypes.windll.user32.IsWindowVisible(_top_level_hwnd(win)))
    except Exception:
        return True                     # unknown: never hide something on a guess


def virtual_bounds() -> tuple[int, int, int, int]:
    """The whole desktop across every monitor: left, top, right, bottom.

    work_area() is the PRIMARY monitor only, so it cannot answer "is this window
    still somewhere the user can see it?" on a multi-monitor setup.
    """
    try:
        gsm = ctypes.windll.user32.GetSystemMetrics
        left, top = gsm(76), gsm(77)                # SM_X/YVIRTUALSCREEN
        return left, top, left + gsm(78), top + gsm(79)
    except Exception:
        return work_area()


def on_screen(x: int, y: int, w: int, h: int, need: int = 20) -> bool:
    """True when at least `need` px of the window in both axes sits inside the
    virtual desktop, i.e. enough of it is left to see and to grab."""
    left, top, right, bottom = virtual_bounds()
    return (min(x + w, right) - max(x, left) >= need
            and min(y + h, bottom) - max(y, top) >= need)


def has_caret(hwnd: int) -> bool:
    """True when the given window's thread currently owns a text caret, i.e. the
    user is very likely focused in a text field.

    Only ever used to SUPPRESS the rescue copy chip, never to trigger it: apps
    that draw their own caret (Chromium, Electron, some Qt) report nothing here,
    and the safe answer to "not sure" is to offer the chip.
    """
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
                    ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                    ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                    ("rcCaret", ctypes.c_long * 4)]
    try:
        user32 = ctypes.windll.user32
        if not hwnd:
            return False
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            return False
        return bool(info.hwndCaret) or bool(info.flags & 0x00000001)   # GUI_CARETBLINKING
    except Exception:
        return False


def snap_to_edge(x: int, y: int, w: int, h: int, margin: int = 8) -> tuple[int, int]:
    """Clamp inside the work area, then stick to the nearest screen edge."""
    left, top, right, bottom = work_area()
    x = max(left + margin, min(x, right - w - margin))
    y = max(top + margin, min(y, bottom - h - margin))
    dists = {
        "left": x - left, "right": right - (x + w),
        "top": y - top, "bottom": bottom - (y + h),
    }
    edge = min(dists, key=dists.get)
    if edge == "left":
        x = left + margin
    elif edge == "right":
        x = right - w - margin
    elif edge == "top":
        y = top + margin
    else:
        y = bottom - h - margin
    return x, y


def place_near_tray(win, width: int, height: int, margin: int = 16) -> None:
    """Opens a Toplevel anchored bottom-right, near the tray icon, instead of
    tkinter's default (screen centre-ish) which read as 'miles from the tray'."""
    _left, _top, right, bottom = work_area()
    win.geometry(f"{width}x{height}+{right - width - margin}+{bottom - height - margin}")


def foreground_window() -> int:
    """HWND of the window the user is working in. 0 if none, or if it is one of
    OUR windows (an orb click that somehow activated us is not a paste target)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        import os
        if pid.value == os.getpid():
            return 0
        return hwnd
    except Exception:
        return 0


def focus_window(hwnd: int) -> bool:
    """Put hwnd back in the foreground so a Ctrl+V lands in it. True when the
    target is (or becomes) the foreground window.

    Windows refuses SetForegroundWindow from a background process, so attach to
    the current foreground thread's input state first; a synthetic Alt tap is
    the documented fallback that lifts the same restriction.
    """
    user32 = ctypes.windll.user32
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    try:
        cur = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        tgt = user32.GetWindowThreadProcessId(hwnd, None)
        if cur != tgt:
            user32.AttachThreadInput(cur, tgt, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if cur != tgt:
            user32.AttachThreadInput(cur, tgt, False)
        if user32.GetForegroundWindow() != hwnd:
            VK_MENU, KEYUP = 0x12, 0x0002
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYUP, 0)
            user32.SetForegroundWindow(hwnd)
        import time
        time.sleep(0.05)               # let focus settle before any paste
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


_mutex = None


def single_instance() -> bool:
    """True if we are the only Hemsa. The mutex handle must stay referenced."""
    global _mutex
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, "Global\\HemsaSingleInstance")
    return kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):              # PyInstaller exe
        return f'"{sys.executable}"'
    # Not "pythonw.exe -m hemsa" directly: the Run key launches with no particular
    # working directory, and `-m hemsa` needs cwd on the project root to find the
    # package. The batch file's `cd /d` fixes that regardless of how it's invoked.
    bat = Path(__file__).resolve().parents[1] / "Start Hemsa.bat"
    return f'"{bat}"'


def set_autostart(enabled: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "Hemsa", 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, "Hemsa")
            except FileNotFoundError:
                pass


def get_autostart() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "Hemsa")
        return True
    except OSError:
        return False
