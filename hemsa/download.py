"""One-time download of the Parakeet TDT 0.6B v2 int8 model (~661 MB).

Public Hugging Face repo, no token, no account. Four plain files - deliberately
NOT an archive, so nothing is ever extracted and there is no zip-slip surface.
The only network calls Hemsa makes are this download and the opt-in update check.

Model licence: CC-BY-4.0 (csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8).
"""

import hashlib
import logging
import shutil
import threading
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import model_manifest

log = logging.getLogger("hemsa.download")

BASE_URL = ("https://huggingface.co/csukuangfj/"
            "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/resolve/main")

CHUNK = 1 << 20          # 1 MB
TIMEOUT = (10, 60)       # connect, read - per chunk, not for the whole transfer
HEADROOM = 100 << 20     # spare disk space demanded on top of the model

# huggingface.co 302s to its CDN (measured: us.aws.cdn.hf.co), so redirects must
# be followed - but every hop has to stay HTTPS on a Hugging Face host. The
# checksum guarantees integrity; this guarantees the bytes were not fetched over
# a downgraded connection, which the checksum cannot tell you.
ALLOWED_HOSTS = ("huggingface.co", "hf.co")


# The manifest (names, sizes, checksums) lives in model_manifest so config.py can
# check "is the model here?" without importing requests.
FILES = model_manifest.FILES
TOTAL_BYTES = model_manifest.TOTAL_BYTES


def file_url(f) -> str:
    return f"{BASE_URL}/{f.name}"


class Cancelled(Exception):
    """Raised when the caller's cancel event is set mid-download."""


class InsecureRedirect(Exception):
    """The download was redirected off HTTPS or off Hugging Face."""


def _check_transport(resp) -> None:
    """Every hop, including the final URL, must be HTTPS on a Hugging Face host."""
    for url in [h.url for h in resp.history] + [resp.url]:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(
                host == d or host.endswith("." + d) for d in ALLOWED_HOSTS):
            raise InsecureRedirect(f"refusing redirect to {parts.scheme}://{host}")


class VerifyFailed(Exception):
    """A finished file did not match its published checksum."""


def needed(dest: Path) -> list:
    """Files still to fetch. A wrong-sized leftover counts as missing - it is a
    truncated download, not a usable model."""
    return model_manifest.missing(dest)


def bytes_needed(dest: Path) -> int:
    return sum(f.size for f in needed(dest))


def _check_space(dest: Path, want: int) -> None:
    probe = dest
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < want + HEADROOM:
        raise OSError(f"Not enough disk space: {free // (1 << 20)} MB free, "
                      f"{(want + HEADROOM) // (1 << 20)} MB needed")


def _fetch(f, dest: Path, on_chunk, cancel: threading.Event) -> None:
    """Download one file to <name>.part, resuming if a partial exists, verify the
    checksum, then rename into place. The rename is the only moment the real
    filename appears, so a killed download can never look like a working model."""
    final = dest / f.name
    part = dest / (f.name + ".part")

    have = part.stat().st_size if part.exists() else 0
    if have > f.size:                 # junk from an aborted or changed download
        part.unlink()
        have = 0

    hasher = hashlib.sha256()
    if have:
        with part.open("rb") as fh:   # seed the running hasher with what we kept
            while (block := fh.read(CHUNK)):
                hasher.update(block)
        log.info("resuming %s at %d bytes", f.name, have)

    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(file_url(f), stream=True, timeout=TIMEOUT, headers=headers) as r:
        r.raise_for_status()          # before reading a byte: a 404 HTML page
        _check_transport(r)           # streamed to disk fails much later, opaquely
        if have and r.status_code != 206:
            # server ignored the Range request: start clean rather than splice
            log.info("%s: no range support (HTTP %s), restarting", f.name, r.status_code)
            have = 0
            hasher = hashlib.sha256()
        mode = "ab" if have else "wb"
        on_chunk(0)
        with part.open(mode) as fh:
            for block in r.iter_content(CHUNK):
                if cancel.is_set():
                    raise Cancelled()
                fh.write(block)
                hasher.update(block)
                on_chunk(len(block))

    got = hasher.hexdigest()
    if got != f.sha256 or part.stat().st_size != f.size:
        part.unlink(missing_ok=True)
        raise VerifyFailed(f"{f.name}: checksum mismatch, download discarded")
    part.replace(final)
    log.info("%s verified (%d bytes)", f.name, f.size)


def run(dest: Path, on_progress=None, cancel: threading.Event | None = None) -> None:
    """Fetch every missing file into dest.

    on_progress(done_bytes, total_bytes, label) is called from THIS thread - the
    caller is responsible for getting it to the UI thread safely.
    Raises Cancelled, VerifyFailed, OSError or requests exceptions.
    """
    cancel = cancel or threading.Event()
    todo = needed(dest)
    if not todo:
        return
    dest.mkdir(parents=True, exist_ok=True)
    total = sum(f.size for f in todo)
    _check_space(dest, total)

    done = 0
    for f in todo:
        if cancel.is_set():
            raise Cancelled()
        base = done

        def on_chunk(n: int, _f=f, _base=base) -> None:
            nonlocal done
            done += n
            if on_progress:
                on_progress(done, total, _f.name)

        # a resumed file starts partway through: count what is already on disk
        part = dest / (f.name + ".part")
        if part.exists():
            done += min(part.stat().st_size, f.size)
        _fetch(f, dest, on_chunk, cancel)
        done = base + f.size
        if on_progress:
            on_progress(done, total, f.name)
