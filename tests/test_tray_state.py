"""The tray is the only thing on screen that shows a meeting is being recorded
once the Meetings window is closed - the silent-recording courtesy reminder
depends on it. These check the tooltip text and that App._refresh_tray folds both
jobs into the same update without disturbing the dictation states.
"""

import pytest


@pytest.fixture()
def app_mod(monkeypatch, tmp_path):
    import hemsa.config as config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    import hemsa.__main__ as main_mod
    return main_mod


class FakeTray:
    title = None
    icon = None


class Fake:
    """Just enough of App for the unbound _refresh_tray call."""

    def __init__(self, state, recording_id):
        self.tray = FakeTray()
        self.ctl = type("Ctl", (), {"state": state})()
        self.jobs = type("Jobs", (), {"recording_id": recording_id})()


def test_titles_keep_the_dictation_states_unchanged():
    from hemsa.ui import tray
    assert tray.title_for("idle") == "Hemsa - ready"
    assert tray.title_for("recording") == "Hemsa - listening"
    assert tray.title_for("processing") == "Hemsa - typing"


def test_a_meeting_recording_shows_in_the_title_and_the_icon(app_mod):
    from hemsa.ui import tray
    assert tray.title_for("idle", meeting=True) == "Hemsa - recording a meeting"
    assert "recording a meeting" in tray.title_for("processing", meeting=True)

    app = Fake("idle", "abc123")
    app_mod.App._refresh_tray(app)
    assert "recording a meeting" in app.tray.title
    assert app.tray.icon.tobytes() == tray._icon_image(True).tobytes()


def test_a_dictation_state_alone_does_not_repaint_the_icon_red(app_mod):
    from hemsa.ui import tray
    app = Fake("recording", None)
    app_mod.App._refresh_tray(app)
    assert app.tray.title == "Hemsa - listening"
    assert app.tray.icon.tobytes() == tray._icon_image(False).tobytes()
