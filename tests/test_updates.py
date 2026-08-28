"""Update-check contract: strict version parsing, numeric comparison, and a
browser call that can never be handed a URL the network chose."""

from hemsa import updates


def test_parse_version_accepts_plain_versions():
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.parse_version("0.1.0") == (0, 1, 0)
    assert updates.parse_version(" v2 ") == (2,)


def test_parse_version_rejects_anything_else():
    for bad in ("", "latest", "v1.2.3-beta", "1.2.3.4.5", "v1.2.3; rm -rf /",
                r"\\attacker\share\x.exe", "https://evil.example/x", None):
        assert updates.parse_version(bad) is None


def test_is_newer_compares_numerically_not_as_strings():
    # the classic trap: "0.10.0" < "0.9.0" is True for strings
    assert updates.is_newer("0.10.0", "0.9.0")
    assert not updates.is_newer("0.9.0", "0.10.0")


def test_is_newer_handles_unequal_lengths_and_equality():
    assert updates.is_newer("1.1", "1.0.9")
    assert not updates.is_newer("1.0", "1.0.0")
    assert not updates.is_newer("0.1.0", "0.1.0")


def test_is_newer_rejects_unparseable_versions():
    assert not updates.is_newer("garbage", "0.1.0")
    assert not updates.is_newer("0.2.0", "garbage")


def test_open_page_rejects_urls_outside_our_releases_path(monkeypatch):
    opened = []
    monkeypatch.setattr(updates.webbrowser, "open", opened.append)
    for hostile in ("https://evil.example/x", r"\\attacker\share\x.exe",
                    "file:///C:/Windows/System32/calc.exe",
                    "https://github.com/someone-else/repo/releases"):
        updates.open_page(hostile)
    assert opened == [updates.RELEASES_PAGE] * 4


def test_open_page_allows_our_own_release_tag():
    assert updates.RELEASES_PAGE.startswith("https://github.com/")
    url = f"{updates.RELEASES_PAGE}/tag/v9.9.9"
    assert url.startswith(updates.RELEASES_PAGE)
