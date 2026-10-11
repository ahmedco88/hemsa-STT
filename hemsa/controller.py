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

from . import (audio, cleanup, config, dictionary, fastclean, history, hotkey, injector,
               sounds, stats, uia, winutil)

log = logging.getLogger("hemsa.controller")

PREVIEW_MIN_S = 0.7     # floor between live-preview decodes
# The pill shows ~20 words (~8 s of speech), so the preview only decodes the last
# 12 s. Measured 2026-10-12: decoding the whole clip put up to +1.1 s on the paste
# of a 28 s dictation (the final decode waits for the in-flight preview pass);
# the window caps that wait at one 12 s decode however long the user talks.
PREVIEW_WINDOW = 12 * audio.SAMPLE_RATE
VERIFY_READS = (0.3, 0.9)   # s after the paste; a slow app gets a second chance
WATCH_S = 30                # how long a landed paste is watched for a correction


def app_key(hwnd: int) -> str:
    """'chrome.exe' style key for per-app rules; '' when Windows will not say."""
    path = winutil.window_exe(hwnd) if hwnd else ""
    return path.replace("/", "\\").rsplit("\\", 1)[-1].lower()


def effective_mode(cfg: dict, app: str) -> str:
    """The cleanup mode for dictation into app: its own rule if it has one."""
    mode = (cfg.get("app_rules") or {}).get(app, {}).get("cleanup_mode")
    return mode if mode in config.CLEANUP_MODES else cfg.get("cleanup_mode", "off")


class Controller:
    def __init__(self, cfg: dict, engine, post):
        """post(fn) schedules fn on the tkinter main thread."""
        self.cfg = cfg
        self.engine = engine
        self.post = post
        self.state = "idle"                      # idle | recording | processing
        self.on_state = lambda state: None       # UI hook, called on main thread
        self.on_paste_risk = lambda: None        # UI hook: the paste may not have landed
        self.on_cleanup_blocked = lambda nums: None   # UI hook: the model invented a number
        self.on_preview = lambda text: None      # UI hook: draft words while recording
        self.on_learn_offer = lambda word: None  # UI hook: a corrected word worth listing
        self._utter = 0                          # bumped per dictation; ends a watch
        self.last_text = ""
        self._preview_gen = 0                    # bumped per utterance; stale passes drop
        self.cleanup_mode = cfg.get("cleanup_mode", "off")   # effective for this utterance
        self.last_app = ""                       # exe name of the last dictation target
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
            # per-app rule, fixed for this utterance: the HUD's "polishing..." and
            # the cleanup pass must agree on which mode is running
            app = app_key(self._target_hwnd)
            if app:
                self.last_app = app              # Settings offers it as "Add <app>"
            self.cleanup_mode = effective_mode(self.cfg, app)
            self._preview_gen += 1
            self._utter += 1                     # a correction watch on the last paste ends
            self._set_state("recording")
        if self.cfg.get("live_preview", True):
            threading.Thread(target=self._preview_loop, args=(self._preview_gen,),
                             daemon=True, name="preview").start()
        wait = (time.perf_counter() - hotkey.LAST_PRESS) * 1000
        if 0 < wait < 2000:   # only meaningful right after a hotkey press, not orb clicks
            log.info("press -> mic gate open in %.0f ms", wait)
        sounds.play_start(self.cfg)
        if self.cleanup_mode == "ai":
            # load the model while the user is talking, so cleanup adds ~1 s, not ~10.
            # Only for "ai" - the rules pass has nothing to warm up.
            threading.Thread(target=cleanup.warm_up, args=(self.cfg,), daemon=True).start()

    def _finish(self) -> None:
        with self._lock:
            if self.state != "recording":
                return
            clip = self._recorder.stop()
            self._preview_gen += 1               # any pass still decoding is now stale
            self._set_state("processing")
        sounds.play_stop(self.cfg)
        before: dict = {}
        snap = None
        if self.cfg.get("verify_paste", True) or self.cfg.get("fit_to_cursor", True):
            # read the field BEFORE the paste, in parallel with the transcription:
            # the first UI Automation call measured ~140 ms, and the decode is longer
            def read():
                before.update(f=uia.focused(), ctx=uia.caret_context())
            snap = threading.Thread(target=read, daemon=True, name="uia-before")
            snap.start()
        threading.Thread(target=self._process, args=(clip, snap, before), daemon=True,
                         name="process").start()

    def _preview_loop(self, gen: int) -> None:
        """Re-decodes the utterance so far, so the pill can show it while the user
        talks. Parakeet is not a streaming model, but at ~40x real time a 20 s clip
        re-reads in ~0.5 s, so a second model would buy nothing but RAM.

        The interval is twice the last decode time (floor PREVIEW_MIN_S): a long
        clip throttles itself instead of keeping the engine busy back to back,
        which is what would delay the FINAL decode waiting on the same lock."""
        interval = PREVIEW_MIN_S
        silence = self.cfg.get("silence_rms", 0.0015)
        try:
            while True:
                time.sleep(interval)
                if gen != self._preview_gen or self.engine.state != "loaded":
                    return
                clip = self._recorder.snapshot()
                cut = len(clip) > PREVIEW_WINDOW
                clip = clip[-PREVIEW_WINDOW:]
                if audio.rms(clip) < silence:
                    continue
                t0 = time.perf_counter()
                text = self.engine.transcribe(clip)
                if cut and " " in text:
                    # the window starts mid-word, so its first word is a guess
                    text = "… " + text.split(" ", 1)[1]
                interval = max(PREVIEW_MIN_S, 2 * (time.perf_counter() - t0))
                if gen != self._preview_gen:
                    return
                text, _ = dictionary.apply(text, self._words)
                self.post(lambda t=text: self._deliver_preview(gen, t))
        except Exception:
            # off for this utterance only; the paste never depends on the preview
            log.exception("live preview failed")

    def _deliver_preview(self, gen: int, text: str) -> None:
        if gen == self._preview_gen and text:   # main thread: re-check, release may have won
            self.on_preview(text)

    def _process(self, clip, snap=None, before: dict | None = None) -> None:
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
            mode = self.cleanup_mode
            if mode == "fast":
                text = fastclean.clean(text)
            elif mode == "ai":
                cleaned, invented = cleanup.clean(text, self.cfg)
                if cleaned is not None:
                    text = cleaned
                elif invented:
                    # The one cleanup failure worth interrupting for. Everything
                    # else that returns None looks identical on screen - the user
                    # gets their own words - so only this one says anything.
                    self.post(lambda n=invented: self.on_cleanup_blocked(n))
                # digits and unit symbols are a house style, not a cleanup mode:
                # the model is inconsistent about them and this pass is not.
                text = fastclean.numerals(text)
            # spoken commands run AFTER cleanup: Fast mode adds a closing full stop,
            # which would otherwise land after a trailing "new line"
            text, send = fastclean.split_send(text, self.cfg.get("send_phrase", ""))
            if self.cfg.get("spoken_punctuation", True):
                text = fastclean.spoken(text)
            if not text.strip() and not send:
                return
            self.last_text = text
            landed = winutil.focus_window(self._target_hwnd)
            # "landed" only means we got the window back in front - it says nothing
            # about whether there was anywhere in it for the text to GO. An orb
            # click is mouse-first, so there often is no caret at all, and the
            # paste then vanishes (injector.paste restores the old clipboard).
            # So the chip is offered unless we can positively see a text caret.
            caret = winutil.has_caret(self._target_hwnd) if landed else False
            risky = not landed or (self._trigger == "orb" and not caret)
            if snap is not None:
                snap.join(0.5)               # never hold a paste longer than this
            verify = (snap is not None and self.cfg.get("verify_paste", True)
                      and text.strip() and not send)
            ctx = (before or {}).get("ctx")
            if ctx and self.cfg.get("fit_to_cursor", True):
                # space, capital and closing full stop to suit where the cursor is;
                # history keeps the dictation itself, the cursor gets the fitted one
                text = fastclean.fit(text, ctx[0], ctx[1], dictionary.COMMON)
            if text.strip():
                injector.paste(text)
            pasted_at = uia.tick()
            if send and landed:
                # never on a failed focus restore: Enter would go to whatever
                # window is in front instead, and send something half-written
                injector.press_enter()
            elif send:
                log.warning("send phrase heard but the target window was not restored "
                            "- Enter NOT pressed")
            if verify:
                # the read-back decides the chip from here, off this thread: the
                # pill must not wait ~1 s for it, and nor must the next dictation
                threading.Thread(target=self._verify,
                                 args=((before or {}).get("f"), text, risky, pasted_at),
                                 daemon=True, name="uia-verify").start()
            elif risky and text.strip():
                log.warning("paste may not have landed (hwnd=%s trigger=%s focus=%s "
                            "caret=%s) - offering copy chip",
                            self._target_hwnd, self._trigger, landed, caret)
                self.post(lambda: self.on_paste_risk())
            if text.strip():
                history.append(self.last_text, self.cfg)   # the dictation, not the fit
            elapsed_ms = (time.perf_counter() - t0) * 1000
            stats.record(len(text.split()), seconds, elapsed_ms)
            log.info("%.1f s audio -> %d chars in %.0f ms (corrections: %d)",
                     seconds, len(text), elapsed_ms, len(applied))
        except Exception:
            log.exception("processing failed")
        finally:
            self._set_state("idle")

    def _verify(self, before, text: str, risky: bool, pasted_at: int) -> None:
        """Read the field back and decide the rescue chip on evidence.

        risky (today's caret/focus rule said "maybe lost"): one read at 0.3 s, and
        only seeing our words suppresses the chip, so the old behaviour is merely
        0.3 s later, never weaker. Not risky: the chip appears only on a firm
        not_landed after two reads. Then, if it landed, watch for a correction."""
        utter = self._utter
        try:
            time.sleep(VERIFY_READS[0])
            after = uia.focused()
            acted = uia.last_input_tick() > pasted_at
            v = uia.verdict(before, after, None, text, acted)
            later = None
            if v != "landed" and not risky:
                time.sleep(VERIFY_READS[1] - VERIFY_READS[0])
                later = uia.focused()
                acted = uia.last_input_tick() > pasted_at
                v = uia.verdict(before, after, later, text, acted)
            # never the text: this log is the one place a read-back could leak
            log.info("paste read-back: %s (risky=%s, user acted=%s)", v, risky, acted)
            if v == "not_landed" or (risky and v != "landed"):
                self.post(lambda: self.on_paste_risk())
            elif v == "landed" and self.cfg.get("learn_corrections", True):
                base = later if later is not None and later.text is not None else after
                self._watch(base, text, utter)
        except Exception:
            log.exception("paste read-back failed")
            if risky:
                self.post(lambda: self.on_paste_risk())

    def _watch(self, base, pasted: str, utter: int) -> None:
        """Poll the same field for WATCH_S. An identical candidate on two reads in a
        row is offered (a half-typed word is not); focus moving away, a new
        dictation or any offer ends the watch."""
        prev = None
        for _ in range(WATCH_S):
            time.sleep(1.0)
            if utter != self._utter:
                return
            now = uia.focused()
            if now is None or now.rid != base.rid or now.text is None:
                return
            cand = uia.correction(base.text, now.text, pasted, self._words, dictionary.COMMON,
                                  dictionary.MIN_KEY_LEN)
            if utter != self._utter:             # a dictation started during the read
                return
            if cand and cand == prev:
                log.info("correction seen after paste - offering it for the word list")
                self.post(lambda t=cand[1]: self.on_learn_offer(t))
                return
            prev = cand

    def add_word(self, word: str) -> None:
        """The word list's own load/save, so the Words page sees it next open."""
        words = dictionary.load(strict=True)
        if word not in words:
            dictionary.save(words + [word])
        self.reload_dictionary()

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
