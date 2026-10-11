"""Notices a meeting app opening the microphone, so Hemsa can OFFER to record.

It never records on its own. Recording a consultation needs everyone's consent, so
the prompt says so, and the whole feature is off by default (config
`meeting_prompt`).

The signal is Windows' own microphone privacy record, the same one behind the
taskbar's mic-in-use icon: each app that has used the mic has a key under
...\\ConsentStore\\microphone, and LastUsedTimeStop is 0 while it is using it. One
registry read every few seconds, no hooks, nothing injected into the other app.

Browsers are deliberately NOT matched: a browser has the mic open for many reasons,
and nothing here can see which tab. Google Meet in Chrome is therefore not
detected - a documented gap, not a bug.
"""

import logging
import winreg

log = logging.getLogger("hemsa.meeting_detect")

_BASE = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
         r"\ConsentStore\microphone")

# exe name (lowercase) for desktop apps
EXES = {
    "ms-teams.exe": "Teams", "teams.exe": "Teams",
    "zoom.exe": "Zoom",
    "ciscocollabhost.exe": "Webex", "webex.exe": "Webex", "atmgr.exe": "Webex",
    "slack.exe": "Slack",
    "skype.exe": "Skype",
}
# package family name PREFIX for Store apps
PACKAGES = {
    "MSTeams_": "Teams",
    "Microsoft.SkypeApp_": "Skype",
    "5319275A.WhatsAppDesktop_": "WhatsApp",
}


def name_for(key: str) -> str | None:
    """Meeting app name for one ConsentStore subkey name, or None."""
    if "#" in key:                                   # NonPackaged: the path with # for \
        return EXES.get(key.rsplit("#", 1)[-1].lower())
    for prefix, name in PACKAGES.items():
        if key.startswith(prefix):
            return name
    return None


def _in_use(path: str) -> list[str]:
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if sub == "NonPackaged":
                    out += _in_use(path + "\\" + sub)
                    continue
                try:
                    with winreg.OpenKey(k, sub) as s:
                        if winreg.QueryValueEx(s, "LastUsedTimeStop")[0] == 0:
                            out.append(sub)
                except OSError:
                    pass
    except OSError:
        pass
    return out


def meeting_apps_on_mic() -> set[str]:
    """Names of the known meeting apps using the microphone right now."""
    return {n for n in map(name_for, _in_use(_BASE)) if n}


class Detector:
    """Edge detection: an app is reported once when it STARTS using the mic, and
    again only after it has let go. A prompt per poll would be unbearable."""

    def __init__(self):
        self._live: set[str] = set()

    def poll(self, now: set[str]) -> list[str]:
        new = sorted(now - self._live)
        self._live = set(now)
        return new
