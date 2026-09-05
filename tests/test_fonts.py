"""The bundled fonts load privately and never raise: a missing font is a log
line and a fallback face, not a broken app."""

from hemsa.ui import fonts


def test_folder_holds_the_five_faces():
    names = {p.name for p in fonts.FOLDER.glob("*.ttf")}
    assert names == set(fonts.FAMILIES)


def test_missing_folder_returns_empty_and_does_not_raise(tmp_path):
    assert fonts.load_private_fonts(tmp_path / "nowhere") == set()


def test_real_folder_loads_on_windows():
    got = fonts.load_private_fonts()
    assert {"Instrument Serif", "Figtree", "Figtree Medium", "Figtree SemiBold"} <= got
