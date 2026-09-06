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


def _stub(monkeypatch, value):
    """Pretend the Run key holds `value` (None = no entry at all)."""
    calls = []
    monkeypatch.setattr(winutil, "autostart_value", lambda: value)
    monkeypatch.setattr(winutil, "set_autostart", lambda on: calls.append(on))
    return calls


def test_reconcile_restores_a_deleted_entry(monkeypatch):
    calls = _stub(monkeypatch, None)
    assert winutil.reconcile_autostart({"autostart": True}) is True
    assert calls == [True]


def test_reconcile_leaves_a_healthy_entry_alone(monkeypatch, tmp_path):
    there = tmp_path / "Hemsa.exe"
    there.write_text("")
    calls = _stub(monkeypatch, f'"{there}"')
    assert winutil.reconcile_autostart({"autostart": True}) is False
    assert calls == []


def test_reconcile_leaves_another_copy_of_hemsa_alone(monkeypatch, tmp_path):
    """An entry pointing at a DIFFERENT but real Hemsa is somebody's deliberate
    choice. Running from source for five minutes must not repoint sign-in at the
    repo, so only a dangling target is rewritten."""
    other = tmp_path / "Start Hemsa.bat"
    other.write_text("")
    calls = _stub(monkeypatch, f'"{other}"')
    assert winutil.reconcile_autostart({"autostart": True}) is False
    assert calls == []


def test_reconcile_repairs_an_entry_whose_target_is_gone(monkeypatch, tmp_path):
    """The failure the presence-only check could not see: the value is there, so
    Settings says "on", and it names an exe that no longer exists."""
    calls = _stub(monkeypatch, f'"{tmp_path / "moved" / "Hemsa.exe"}"')
    assert winutil.reconcile_autostart({"autostart": True}) is True
    assert calls == [True]


def test_reconcile_respects_autostart_turned_off(monkeypatch):
    """The user's OFF must never be overridden - that is the whole reason the
    installer is not allowed to write the value either."""
    calls = _stub(monkeypatch, None)
    assert winutil.reconcile_autostart({"autostart": False}) is False
    assert winutil.reconcile_autostart({}) is False
    assert calls == []


def test_command_target_ignores_quotes_and_arguments():
    assert winutil._command_target('"C:\\Apps\\Hemsa\\Hemsa.exe"').name == "Hemsa.exe"
    assert winutil._command_target("C:\\Apps\\Hemsa.exe --hidden").name == "Hemsa.exe"


def test_windows_disabling_the_entry_is_read_but_never_written(monkeypatch):
    """Task Manager > Startup apps writes a flag beside the Run value instead of
    deleting it. Hemsa reads it so the toggle can stop claiming to be on; it must
    never write it, because that switch belongs to the user."""
    import hemsa.winutil as w

    assert "StartupApproved" in w._APPROVED_KEY
    src = (Path(w.__file__)).read_text(encoding="utf-8")
    approved = src.split("_APPROVED_KEY = ", 1)[1]
    assert "SetValueEx" not in approved and "DeleteValue" not in approved


def test_blocked_reads_the_explorer_flag_bytes(monkeypatch):
    for blob, blocked in ((b"\x02" + b"\x00" * 11, True),
                          (b"\x03" + b"\x00" * 11, True),
                          (b"\x06" + b"\x00" * 11, False),
                          (b"\x00" + b"\x00" * 11, False)):
        monkeypatch.setattr(winutil, "_read_approved", lambda b=blob: b)
        assert winutil.autostart_blocked() is blocked


def test_blocked_is_false_when_windows_has_no_opinion(monkeypatch):
    monkeypatch.setattr(winutil, "_read_approved", lambda: None)
    assert winutil.autostart_blocked() is False
