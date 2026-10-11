"""build_release's version check and two-file bump, on temp copies only."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)


@pytest.fixture
def copies(tmp_path):
    init, iss = tmp_path / "__init__.py", tmp_path / "hemsa.iss"
    init.write_bytes(b'"""doc"""\r\n\r\n__version__ = "0.6.4"\r\n')
    iss.write_bytes(b'#define AppVersion "0.6.4"\r\nAppVersion={#AppVersion}\r\n')
    return init, iss


@pytest.mark.parametrize("bad", ["0.6.4", "0.6.3", "0.5.99", "v0.6.5", "0.6.5-rc1", "0.6", "0.06.5", " 0.6.5"])
def test_refuses_anything_but_a_higher_plain_version(copies, bad):
    init, iss = copies
    before = init.read_bytes(), iss.read_bytes()
    with pytest.raises(ValueError):
        br.bump(bad, init, iss)
    assert (init.read_bytes(), iss.read_bytes()) == before


def test_bumps_both_files_and_keeps_crlf(copies):
    init, iss = copies
    assert br.bump("0.10.0", init, iss) == "0.6.4"
    assert init.read_bytes() == b'"""doc"""\r\n\r\n__version__ = "0.10.0"\r\n'
    assert iss.read_bytes() == b'#define AppVersion "0.10.0"\r\nAppVersion={#AppVersion}\r\n'


def test_refuses_when_the_two_files_already_disagree(copies):
    init, iss = copies
    iss.write_bytes(b'#define AppVersion "0.6.3"\r\n')
    with pytest.raises(ValueError):
        br.bump("0.6.5", init, iss)
