"""Optional update check against the public GitHub Releases API.

Hemsa's promise is that nothing leaves the machine, so this is OFF by default and
never runs unsolicited: either the user ticks "check on start", or they pick
"Check for updates…" from the tray. The request sends no information about the
user - it is a plain GET of a public endpoint, no telemetry, no identifiers.

Everything coming back is untrusted remote data, and it is handled by NOT using
it: the only value taken from the response is a tag that matches a strict version
regex (digits and dots, nothing else), and the URL we open is then built locally
from that tag. No string GitHub sends us ever reaches the browser call - on
Windows webbrowser.open() is os.startfile(), i.e. ShellExecute, which accepts
UNC paths and protocol handlers, not just http URLs.

Release notes are deliberately not displayed: rendering arbitrary remote text
means length caps, control-character stripping and bidi-override stripping, for
no real benefit over "a new version exists, here is the page".
"""

import logging
import re
import webbrowser

import requests

log = logging.getLogger("hemsa.updates")

REPO = "ahmedco88/hemsa"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT = 10
MAX_BODY = 1 << 20     # a releases response is a few KB; anything huge is wrong

_TAG = re.compile(r"^v?(\d+(?:\.\d+){0,3})$")


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.2.3' -> (1, 2, 3). None for anything that is not a plain version."""
    m = _TAG.match((text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _pad(v: tuple[int, ...], n: int) -> tuple[int, ...]:
    return v + (0,) * (n - len(v))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return _pad(a, n) > _pad(b, n)


def check(current: str) -> dict | None:
    """Return {'version', 'url'} when a newer release exists, else None.

    Never raises: an update check is a convenience and must not break the app or
    pop an error at a user who did not ask for one. Failures - including
    GitHub's 60-per-hour rate-limit response, which has no tag_name at all -
    are logged and treated as "could not check".
    """
    try:
        r = requests.get(API, timeout=TIMEOUT, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Hemsa/{current}",      # GitHub 403s without one
        })
        if r.status_code != 200 or len(r.content) > MAX_BODY:
            log.info("update check: HTTP %s (%d bytes)", r.status_code, len(r.content))
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        tag = str(data.get("tag_name", ""))
        version = parse_version(tag)
        if version is None or not is_newer(tag, current):
            return None
        # URL built from OUR constants and the regex-validated tag - never from
        # any string in the response. See the module docstring.
        dotted = ".".join(str(p) for p in version)
        return {"version": dotted, "url": f"{RELEASES_PAGE}/tag/{tag}"}
    except Exception as exc:
        log.info("update check failed: %s", exc)
        return None


def open_page(url: str = RELEASES_PAGE) -> None:
    """Open a release page. Belt and braces: even though check() builds this URL
    locally, refuse anything that is not on our own releases path, because this
    call is ShellExecute underneath."""
    if not url.startswith(RELEASES_PAGE):
        url = RELEASES_PAGE
    webbrowser.open(url)
