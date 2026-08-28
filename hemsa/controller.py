"""The state machine: idle -> recording -> processing -> idle.

Two trigger sources feed it (hotkey hold, orb click-toggle); both post thread-safe
events. All UI updates go out through callbacks the UI layer registers; the
controller itself never touches tkinter directly - the app queue delivers its
callbacks on the main thread.
"""

import logging
import threading
import time

import pyperclip

from . import (audio, cleanup, dictionary, fastclean, history, hotkey, injector,
               sounds, stats, winutil)

log = logging.getLogger("hemsa.controller")


class Controller:
    def __init__(self, cfg: dict, engine, post):
        """post(fn) schedules fn on the tkinter main thread."""
        self.cfg = cfg
        self.engine = engine
        self.post = post
        self.state = "idle"                      # idle | recording | processing
        self.on_state = lambda state: None       # UI hook, called on main thread
        self.on_paste_risk = lambda: None        # UI hook: the paste may not have landed
        self.last_text = ""
        self._target_hwnd = 0                    # window being dictated into, set at start
        self._trigger = "hotkey"                 # hotkey | orb - decides the rescue chip
        self._recorder = audio.Recorder(cfg)
        self._lock = threading.Lock()
        self._words = dictionary.load()
        if cfg.get("sounds"):
            sounds.warm_up()   # pre-open the tick stream so the first cue is instant

    # ---- triggers (any thread) ----
    def hotkey_press(self) -> None:
        self._trigger = "hotkey"
        self._start()

    def hotkey_release(self) -> None:
        self._finish()

    def orb_click(self) -> None:
        if self.state == "recording":
            self._finish()
        else:
            self._trigger = "orb"
            self._start()

    # ---- state moves ----
    def _set_state(self, s: str) -> None:
        self.state = s
        self.post(lambda: self.on_state(s))

    def _start(self) -> None:
        with self._lock:
            if self.state != "idle":
                return
            if self.engine.state == "error":
                log.error("engine failed to load: %s", self.engine.error)
                return
            try:
                self._recorder.start()
            except Exception:
                log.exception("mic start failed")
                return
            # remember where the text must land: if anything steals focus before
            # the paste (orb click edge cases, user alt-tabbing), we restore it
            self._target_hwnd = winutil.foreground_window()
            self._set_state("recording")
        wait = (time.perf_counter() - hotkey.LAST_PRESS) * 1000
        if 0 < wait < 2000:   # only meaningful right after a hotkey press, not orb clicks
            log.info("press -> mic gate open in %.0f ms", wait)
        sounds.play_start(self.cfg)
        if self.cfg.get("cleanup_mode") == "ai":
            # load the model while the user is talking, so cleanup adds ~1 s, not ~10.
            # Only for "ai" - the rules pass has nothing to warm up.
            threading.Thread(target=cleanup.warm_up, args=(self.cfg,), daemon=True).start()

    def _finish(self) -> None:
        with self._lock:
            if self.state != "recording":
                return
            clip = self._recorder.stop()
            self._set_state("processing")
        sounds.play_stop(self.cfg)
        threading.Thread(target=self._process, args=(clip,), daemon=True, name="process").start()

    def _process(self, clip) -> None:
        try:
            seconds = len(clip) / audio.SAMPLE_RATE if len(clip) else 0.0
            if audio.rms(clip) < self.cfg.get("silence_rms", 0.0015):
                log.info("skipped near-silent clip (%.1f s)", seconds)
                return
            t0 = time.perf_counter()
            text = self.engine.transcribe(clip)
            if not text:
                return
            text, applied = dictionary.apply(text, self._words)
            mode = self.cfg.get("cleanup_mode", "off")
            if mode == "fast":
                text = fastclean.clean(text)
            elif mode == "ai":
                cleaned = cleanup.clean(text, self.cfg)
                if cleaned is not None:
                    text = cleaned
            self.last_text = text
            landed = winutil.focus_window(self._target_hwnd)
            # "landed" only means we got the window back in front - it says nothing
            # about whether there was anywhere in it for the text to GO. An orb
            # click is mouse-first, so there often is no caret at all, and the
            # paste then vanishes (injector.paste restores the old clipboard).
            # So the chip is offered unless we can positively see a text caret.
            caret = winutil.has_caret(self._target_hwnd) if landed else False
            risky = not landed or (self._trigger == "orb" and not caret)
            injector.paste(text)
            if risky:
                log.warning("paste may not have landed (hwnd=%s trigger=%s focus=%s "
                            "caret=%s) - offering copy chip",
                            self._target_hwnd, self._trigger, landed, caret)
                self.post(lambda: self.on_paste_risk())
            history.append(text, self.cfg)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            stats.record(len(text.split()), seconds, elapsed_ms)
            log.info("%.1f s audio -> %d chars in %.0f ms (corrections: %d)",
                     seconds, len(text), elapsed_ms, len(applied))
        except Exception:
            log.exception("processing failed")
        finally:
            self._set_state("idle")

    def reload_dictionary(self) -> None:
        self._words = dictionary.load()

    # ---- orb context menu ----
    def copy_last(self) -> bool:
        """Put the last transcript on the clipboard. False if there is none."""
        if not self.last_text:
            return False
        pyperclip.copy(self.last_text)
        log.info("copied last transcript (%d chars) from the orb menu", len(self.last_text))
        return True

    def paste_last(self, hwnd: int) -> bool:
        """Re-paste the last transcript into `hwnd`. False if there is none.

        `hwnd` is captured BEFORE the menu opens, not read here: a popup menu takes
        the foreground, so by the time this runs the user's text field is no longer
        it. Same rescue-chip rule as a dictation, and unconditionally mouse-first -
        a menu click means there may well be no caret to paste into, and
        injector.paste puts the old clipboard back 0.6 s later, so a paste that
        lands nowhere loses the text outright.
        """
        if not self.last_text:
            return False
        landed = winutil.focus_window(hwnd)
        caret = winutil.has_caret(hwnd) if landed else False
        injector.paste(self.last_text)
        if not landed or not caret:
            log.warning("menu re-paste may not have landed (hwnd=%s focus=%s caret=%s)"
                        " - offering copy chip", hwnd, landed, caret)
            self.post(lambda: self.on_paste_risk())
        return True
