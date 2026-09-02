"""Config load/save. Lives in %LOCALAPPDATA%\\Hemsa\\config.json.

Model dir resolution, in order: an explicit models_dir in the config, then the
HEMSA_MODELS_DIR environment variable (for anyone who already has the files and
does not want a second 661 MB copy), then the default under %LOCALAPPDATA%.
Nothing here may hardcode a path from the author's machine - see the test.
"""

import json
import logging
import os
from pathlib import Path

from . import model_manifest

APP_NAME = "Hemsa"

# off  = paste exactly what was heard
# fast = rules only (fastclean), sub-millisecond, cannot invent content
# ai   = local Ollama model; the only one that fixes real transcription errors,
#        and on a CPU-only machine it costs seconds
CLEANUP_MODES = ("off", "fast", "ai")
CLEANUP_LABELS = {"off": "Off", "fast": "Fast (rules only)", "ai": "Full (Ollama)"}

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = DATA_DIR / "config.json"

ENV_MODELS = "HEMSA_MODELS_DIR"    # power-user override, e.g. a shared model folder

DEFAULTS = {
    "hotkey": "ctrl+win",          # keyboard-lib key name(s), hold-to-talk; see hotkey.CHOICES
    "hotkey_enabled": True,
    "mic_device": None,            # None = system default; else sounddevice name substring
    "sounds": True,
    "theme": "plum",               # palette.CHOICES; applied before any UI is built
    "onboarded": False,            # first-run setup completed
    "update_check": False,         # OFF by default: the app promises no network
    "autostart": False,
    "show_orb": True,
    "orb_pos": None,               # [x, y]; None = bottom-right default
    "cleanup_mode": "off",         # off | fast | ai - see CLEANUP_MODES
    "ollama_url": "http://localhost:11434",
    "cleanup_model": "qwen3.5:2b",
    "models_dir": None,            # resolved lazily, see models_dir()
    "silence_rms": 0.0015,         # skip near-silent clips (threshold proven in a sibling project)
    "history_cap": 200,
    "meeting_treatment": "ai",     # ai = transcript + summary, fast = transcript only
}


class ConfigUnreadable(Exception):
    """config.json exists but could not be read or parsed.

    This is NOT the same as "no config yet". Treating it as first-run silently
    resets every setting, offers to re-download the model, and then save()
    writes the defaults back - destroying the user's real settings. So it is
    raised, and the caller decides.
    """


def load(strict: bool = False) -> dict:
    """Read config.json, falling back to DEFAULTS when there is no file yet.

    strict=True raises ConfigUnreadable if the file exists but cannot be read,
    instead of pretending this is a first run. Startup uses strict.
    """
    cfg = dict(DEFAULTS)
    log = logging.getLogger("hemsa.config")
    try:
        # utf-8-sig, not utf-8: an editor that saves a BOM otherwise makes the
        # whole file unparseable, and the fallback would silently reset settings.
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        cfg.update(raw)
        _migrate(cfg, raw)
        return cfg
    except FileNotFoundError:
        return cfg                              # genuine first run
    except OSError as exc:
        # exists but locked, denied, or on an unavailable drive - transient
        log.warning("config.json unreadable (%s)", exc)
        if strict:
            raise ConfigUnreadable(str(exc)) from exc
    except ValueError as exc:
        log.warning("config.json is not valid JSON (%s)", exc)
        _quarantine()
        if strict:
            raise ConfigUnreadable(str(exc)) from exc
    return cfg


def _migrate(cfg: dict, raw: dict) -> None:
    """Carry old configs forward. `cleanup` was a bool before the three-way mode
    existed; a user who had it on must stay on the AI pass, not be silently
    downgraded."""
    if "cleanup_mode" not in raw:
        cfg["cleanup_mode"] = "ai" if raw.get("cleanup") else "off"
    cfg.pop("cleanup", None)                    # superseded; save() would drop it anyway
    if cfg.get("cleanup_mode") not in CLEANUP_MODES:
        cfg["cleanup_mode"] = "off"


def _quarantine() -> None:
    """COPY an unparseable config aside so it can be recovered by hand. A copy,
    not a move: never remove the user's only settings file on our own initiative."""
    try:
        CONFIG_PATH.replace(CONFIG_PATH.with_suffix(".bad.json"))
    except OSError:
        pass


def save(cfg: dict) -> None:
    """Write atomically. A plain write truncates first, so a second Hemsa
    starting at that instant can read an empty or half-written file and fall
    back to defaults - which is exactly how a working install can end up asking
    to re-download the model."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    known = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(known, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)                    # atomic on Windows and POSIX


def models_dir(cfg: dict) -> Path:
    if cfg.get("models_dir"):
        return Path(cfg["models_dir"])
    if os.environ.get(ENV_MODELS):
        return Path(os.environ[ENV_MODELS])
    return DATA_DIR / "models" / "parakeet-v2"


def models_present(cfg: dict) -> bool:
    """Existence is NOT enough: an aborted download leaves the right filenames at
    the wrong sizes, which reads as 'ready' and then dies inside sherpa-onnx with
    an opaque protobuf error. Size is the cheap integrity check; download.py does
    the full SHA256 before a file is ever given its real name.

    Uses model_manifest (stdlib only) rather than download (which needs requests):
    answering "do I have the model?" must never depend on an HTTP library.
    """
    return not model_manifest.missing(models_dir(cfg))
