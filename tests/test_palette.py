"""Theme registry contract: identical slots everywhere, valid hex, live rebind.
A partial theme must fail loudly - dict fallbacks are how one wrong-hued widget
ships with no error (the Tailwind partial-ramp bug, Python edition)."""

import re

import pytest

from hemsa import palette as P

HEX = re.compile(r"^#[0-9A-F]{6}$")


@pytest.fixture(autouse=True)
def restore_theme():
    yield
    P.set_theme(P.DEFAULT)


def test_every_theme_has_identical_slots():
    slots = set(P.THEMES[P.DEFAULT])
    for name, theme in P.THEMES.items():
        assert set(theme) == slots, f"{name} slot mismatch"


def test_every_value_is_uppercase_hex():
    for name, theme in P.THEMES.items():
        for slot, value in theme.items():
            assert HEX.match(value), f"{name}.{slot} = {value!r}"
    for value in (P.OK, P.WARN, P.DANGER, P.REC):
        assert HEX.match(value)


def test_plum_pins_shipped_values():
    plum = P.THEMES["plum"]
    assert plum["ACCENT"] == "#5B47A8"
    assert plum["PAPER"] == "#F4F2F1"
    assert plum["LINE"] == "#E6E2E3"
    assert plum["MIST"] == "#ECE8EA"
    assert plum["DARK_ACCENT"] == "#B7A7EC"
    assert plum["DARK_GROUND"] == "#150F24"


def test_status_inks_exist_and_are_hex():
    for value in (P.OK_INK, P.TRANSPARENT_KEY):
        assert HEX.match(value)


def test_palette_is_the_only_file_with_hex():
    """One file holds colour. Three copies of the HUD transparent key were the
    drift shape this guards against (found 2026-09-03)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "hemsa"
    hexes = re.compile(r"""["']#[0-9A-Fa-f]{3,12}["']""")
    offenders = [f"{p.relative_to(root)}: {m.group(0)}"
                 for p in sorted(root.rglob("*.py")) if p.name != "palette.py"
                 for m in hexes.finditer(p.read_text(encoding="utf-8"))]
    assert offenders == [], offenders


def test_set_theme_rebinds_module_attributes():
    P.set_theme("navy")
    assert P.ACCENT == P.THEMES["navy"]["ACCENT"]
    assert P.current() == "navy"
    P.set_theme("plum")
    assert P.ACCENT == "#5B47A8"


def test_unknown_theme_falls_back_to_default():
    applied = P.set_theme("no-such-theme")
    assert applied == P.DEFAULT
    assert P.ACCENT == P.THEMES[P.DEFAULT]["ACCENT"]


def test_labels_and_choices_cover_all_themes():
    assert set(P.CHOICES) == set(P.THEMES) == set(P.LABELS)
