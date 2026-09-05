"""Font roles: bundled faces when they loaded, system faces when they did not."""

from hemsa.ui import theme


def test_roles_fall_back_when_nothing_loaded():
    theme.set_fonts(set())
    assert theme.F.display[0] == "Cambria"
    assert theme.F.body == ("Segoe UI", 11)
    assert theme.F.medium == ("Segoe UI", 11, "bold")


def test_roles_use_bundled_faces_when_loaded():
    theme.set_fonts({"Instrument Serif", "Figtree", "Figtree Medium", "Figtree SemiBold"})
    try:
        assert theme.F.display == ("Instrument Serif", 30)
        assert theme.F.number == ("Instrument Serif", 36)
        assert theme.F.medium == ("Figtree Medium", 11)
        assert theme.F.eyebrow == ("Figtree SemiBold", 8)
    finally:
        theme.set_fonts(set())
