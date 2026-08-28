"""Types the text at the cursor: clipboard paste with previous clipboard restored.
Pattern proven in a sibling project - keyboard.write() is slow for long text and mangles
some apps, Ctrl+V lands everywhere.
"""

import threading
import time

import keyboard
import pyperclip


def paste(text: str) -> None:
    prev = None
    try:
        prev = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    time.sleep(0.06)                  # clipboard write is async; give it a beat
    keyboard.send("ctrl+v")
    if prev is not None:
        def restore():
            time.sleep(0.6)           # after the target app has read the clipboard
            try:
                pyperclip.copy(prev)
            except Exception:
                pass
        threading.Thread(target=restore, daemon=True).start()
