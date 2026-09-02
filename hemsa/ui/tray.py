"""System tray icon + menu (pystray). Menu callbacks arrive on pystray's thread and
must only post to the app queue."""

from PIL import Image, ImageDraw, ImageFont

import pystray

from .. import config, palette as P


def _icon_image(recording: bool = False) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill=P.REC if recording else P.DEEP)
    try:
        font = ImageFont.truetype("segoeui.ttf", 38)
    except OSError:
        font = ImageFont.load_default()
    # deep is the rail surface, so the glyph is text-on-rail = paper
    d.text((32, 29), "h", font=font, fill=P.PAPER, anchor="mm")
    return img


def title_for(state: str, meeting: bool = False) -> str:
    """Tray tooltip for the two jobs that can be running at once. A meeting is
    captured silently, so with the Meetings window closed the tray is the ONLY
    thing on screen saying the mic and the speakers are being recorded."""
    base = {"idle": "Hemsa - ready", "recording": "Hemsa - listening",
            "processing": "Hemsa - typing"}[state]
    if not meeting:
        return base
    if state == "idle":
        return "Hemsa - recording a meeting"
    return f"{base} (recording a meeting)"


def build(app) -> pystray.Icon:
    """app: the App object in __main__ (exposes cfg, post, and the actions)."""
    def item(label, action, checked=None):
        return pystray.MenuItem(label, lambda: app.post(action), checked=checked)

    def cleanup_item(mode):
        return pystray.MenuItem(
            config.CLEANUP_LABELS[mode],
            lambda: app.post(lambda: app.set_cleanup_mode(mode)),
            checked=lambda i: app.cfg.get("cleanup_mode", "off") == mode, radio=True)

    def theme_item(name):
        return pystray.MenuItem(
            P.LABELS[name],
            lambda: app.post(lambda: app.set_theme(name)),
            checked=lambda i: P.current() == name, radio=True)

    menu = pystray.Menu(
        pystray.MenuItem("Cleanup", pystray.Menu(
            *(cleanup_item(m) for m in config.CLEANUP_MODES))),
        item("Show orb", app.toggle_orb, checked=lambda i: app.cfg["show_orb"]),
        pystray.MenuItem("Theme", pystray.Menu(*(theme_item(n) for n in P.CHOICES))),
        pystray.Menu.SEPARATOR,
        item("Meetings…", app.open_meetings),
        item("Stats…", app.open_stats),
        item("History…", app.open_history),
        item("Word list…", app.open_dictionary),
        item("Settings…", app.open_settings),
        pystray.Menu.SEPARATOR,
        item("Pause hotkey", app.toggle_hotkey, checked=lambda i: not app.cfg["hotkey_enabled"]),
        item("About Hemsa…", app.open_about),
        item("Check for updates…", app.check_updates),
        item("Quit", app.quit),
    )
    return pystray.Icon("Hemsa", _icon_image(), "Hemsa - ready", menu)
