"""Autostart survives an upgrade.

Every install used to delete the HKCU Run value, because the installer's registry
entry carried Inno's `deletevalue` flag (which fires on INSTALL) alongside
`uninsdeletevalue`. The settings toggle still read "on", so nothing on screen ever
said Hemsa had stopped starting at login. Found on Ahmed's own PC, 2026-09-03.

Two guards, because either alone leaves a hole: the installer must not delete the
value, and the app repairs the value if some older installer already did.
"""

from pathlib import Path

from hemsa import winutil

ISS = (Path(__file__).resolve().parents[1] / "installer" / "hemsa.iss").read_text(
    encoding="utf-8")


def _run_key_line() -> str:
    """The [Registry] entry for the Run value, flags and all."""
    lines = [ln for ln in ISS.splitlines() if 'ValueName: "Hemsa"' in ln]
    assert len(lines) == 1, f"expected one Run-value entry, found {len(lines)}"
    return lines[0]


def test_installer_never_deletes_the_run_value_on_install():
    line = _run_key_line()
    assert "uninsdeletevalue" in line, "an uninstall must remove the stale entry"
    assert "deletevalue" not in line.replace("uninsdeletevalue", ""), (
        "deletevalue fires on INSTALL: every upgrade would silently switch "
        "autostart off while Settings still showed it on")


def test_installer_still_writes_no_value_of_its_own():
    """The app owns the value. The installer must never create it, or a user who
    turned autostart off gets it back on every upgrade."""
    assert "ValueType: none" in _run_key_line()


def test_reconcile_restores_a_deleted_entry(monkeypatch):
    calls = []
    monkeypatch.setattr(winutil, "get_autostart", lambda: False)
    monkeypatch.setattr(winutil, "set_autostart", lambda on: calls.append(on))
    assert winutil.reconcile_autostart({"autostart": True}) is True
    assert calls == [True]


def test_reconcile_leaves_a_healthy_entry_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(winutil, "get_autostart", lambda: True)
    monkeypatch.setattr(winutil, "set_autostart", lambda on: calls.append(on))
    assert winutil.reconcile_autostart({"autostart": True}) is False
    assert calls == []


def test_reconcile_respects_autostart_turned_off(monkeypatch):
    """The user's OFF must never be overridden - that is the whole reason the
    installer is not allowed to write the value either."""
    calls = []
    monkeypatch.setattr(winutil, "get_autostart", lambda: False)
    monkeypatch.setattr(winutil, "set_autostart", lambda on: calls.append(on))
    assert winutil.reconcile_autostart({"autostart": False}) is False
    assert winutil.reconcile_autostart({}) is False
    assert calls == []
