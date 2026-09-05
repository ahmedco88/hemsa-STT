"""The packaged build must not ship ffmpeg.

PyAV's wheel bundles about 25 compiled DLLs, two of which (libx264, libx265) are
GPL in their free builds, and Hemsa is MIT. They cannot be dropped one by one:
avcodec imports them through its PE import table, so deleting them fails PyAV at
`import av` with "DLL load failed while importing _core". The decision (2026-09-02)
is therefore to bundle no av at all and make meeting file IMPORT a run-from-source
feature. Recording, transcription and summaries need no ffmpeg.

That decision lives in one line of hemsa.spec, which is not executed by any test
and would be reverted by anyone "fixing" the import feature in the packaged build.
Hence this file: a licence decision that only exists as a comment is not a control.
"""

from pathlib import Path

import pytest

from hemsa import importer

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "hemsa.spec").read_text(encoding="utf-8")


def test_spec_does_not_collect_pyav_binaries():
    assert 'collect_dynamic_libs("av")' not in SPEC
    assert "collect_dynamic_libs('av')" not in SPEC


def test_spec_does_not_hidden_import_av():
    """A hiddenimport pulls the package in even with no explicit binaries."""
    hidden = SPEC.split("hiddenimports=[", 1)[1].split("]", 1)[0]
    assert '"av"' not in hidden and "'av'" not in hidden


def test_spec_excludes_av_outright():
    excludes = SPEC.split("excludes=[", 1)[1].split("]", 1)[0]
    assert '"av"' in excludes or "'av'" in excludes


def test_third_party_notices_ship_with_the_installer():
    iss = (ROOT / "installer" / "hemsa.iss").read_text(encoding="utf-8")
    assert "THIRD-PARTY-NOTICES.md" in iss
    assert (ROOT / "THIRD-PARTY-NOTICES.md").exists()


def test_import_without_pyav_explains_itself(monkeypatch, tmp_path):
    """The message a packaged user meets has to say what to do, not 'no module
    named av'. Asserts the SETUP too: available() must really report False."""
    monkeypatch.setattr(importer, "available", lambda: False)
    assert importer.available() is False
    with pytest.raises(importer.ImportUnsupported) as exc:
        importer.to_wav(tmp_path / "meeting.m4a", tmp_path / "out.wav")
    message = str(exc.value).lower()
    assert "from source" in message
    assert "recording still works" in message


def test_spec_ships_the_fonts():
    """The typefaces load from Path(__file__).parent / "fonts"; a bundle without
    them falls back silently to Cambria / Segoe UI, which no test would notice."""
    assert '("hemsa/fonts/*.ttf", "hemsa/fonts")' in SPEC
    # OFL 1.1 clause 2: the licence travels WITH the font. Dropping this one line
    # from the spec redistributes five fonts with no licence text and stays green.
    assert '("hemsa/fonts/*.txt", "hemsa/fonts")' in SPEC
    assert len(list((ROOT / "hemsa" / "fonts").glob("*.ttf"))) == 5
    assert len(list((ROOT / "hemsa" / "fonts").glob("OFL-*.txt"))) == 2


def test_notices_name_both_font_families():
    text = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert "Instrument Serif" in text and "Figtree" in text
    assert "SIL Open Font" in text
