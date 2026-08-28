"""Config loading contract. The load path must never silently discard a user's
settings: save() writes back whatever load() returned, so a swallowed parse
error turns into permanent data loss on the next setting change."""

import json

import pytest

from hemsa import config


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return p


def test_missing_file_gives_defaults(cfg_path):
    assert config.load() == dict(config.DEFAULTS)


def test_round_trip(cfg_path):
    cfg = config.load()
    cfg["theme"] = "teal"
    cfg["onboarded"] = True
    config.save(cfg)
    assert config.load()["theme"] == "teal"
    assert config.load()["onboarded"] is True


def test_utf8_bom_is_read_not_discarded(cfg_path):
    """Notepad and PowerShell's Set-Content both emit a BOM. Reading with plain
    utf-8 raises, which used to reset every setting to defaults."""
    cfg_path.write_text(json.dumps({"theme": "navy", "onboarded": True}),
                        encoding="utf-8-sig")
    loaded = config.load()
    assert loaded["theme"] == "navy"
    assert loaded["onboarded"] is True


def test_unreadable_existing_file_is_not_mistaken_for_first_run(cfg_path, monkeypatch):
    """The 2026-08-23 incident: a working install showed the first-run setup and
    offered to re-download 661 MB. Anything that stops us reading an EXISTING
    config must raise, never silently hand back defaults that save() then makes
    permanent."""
    cfg_path.write_text('{"theme": "navy", "onboarded": true}', encoding="utf-8")

    def boom(*a, **k):
        raise PermissionError("file is locked by another process")

    monkeypatch.setattr(type(cfg_path), "read_text", boom)
    with pytest.raises(config.ConfigUnreadable):
        config.load(strict=True)
    assert config.load()["onboarded"] is False    # non-strict still degrades


def test_missing_file_is_not_an_error_even_in_strict_mode(cfg_path):
    assert config.load(strict=True) == dict(config.DEFAULTS)


def test_save_is_atomic_and_leaves_no_temp(cfg_path):
    """A plain write truncates first, so a second Hemsa starting at that instant
    reads an empty file and falls back to defaults."""
    cfg = config.load()
    cfg["theme"] = "teal"
    config.save(cfg)
    assert not cfg_path.with_suffix(".tmp").exists()
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["theme"] == "teal"


def test_models_present_needs_no_http_library():
    """config must answer 'is the model here?' without importing requests: a
    missing HTTP library turned a simple file check into a startup crash.
    Checked statically so the test cannot itself perturb module state."""
    import ast

    root = config.Path(__file__).resolve().parents[1] / "hemsa"
    banned = {"requests", "urllib3", "certifi"}
    for name in ("config.py", "model_manifest.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods = {(node.module or "").split(".")[0]} | {a.name for a in node.names}
            else:
                continue
            assert not (mods & banned), f"{name} imports {mods & banned}"
            assert "download" not in mods, f"{name} imports download (pulls in requests)"


def test_corrupt_file_is_quarantined_not_overwritten(cfg_path):
    cfg_path.write_text("{ this is not json", encoding="utf-8")
    loaded = config.load()
    assert loaded == dict(config.DEFAULTS)
    bad = cfg_path.with_suffix(".bad.json")
    assert bad.exists(), "the unparseable file must be kept for recovery"
    assert "not json" in bad.read_text(encoding="utf-8")


def test_save_keeps_only_known_keys(cfg_path):
    cfg = config.load()
    cfg["junk_key"] = "x"
    config.save(cfg)
    assert "junk_key" not in json.loads(cfg_path.read_text(encoding="utf-8"))


def test_models_dir_prefers_explicit_then_env(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_MODELS, str(tmp_path / "from-env"))
    assert config.models_dir({"models_dir": str(tmp_path / "explicit")}) == \
        tmp_path / "explicit"
    assert config.models_dir({}) == tmp_path / "from-env"
    monkeypatch.delenv(config.ENV_MODELS)
    assert config.models_dir({}) == config.DATA_DIR / "models" / "parakeet-v2"


def test_no_absolute_paths_in_source():
    """The repo is public: a hardcoded absolute path would leak whoever built it
    and break model resolution for everyone else. Model dir resolution is config
    -> env var -> %LOCALAPPDATA%, and none of those is spelled out in the source.

    Matched by SHAPE rather than by listing the offending strings, so this test
    catches any developer's machine layout and does not have to name one to do it.
    """
    import re
    root = config.Path(__file__).resolve().parents[1] / "hemsa"
    # the lookbehind is what keeps "https://..." out of a drive-letter match
    drive = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{1,2}[A-Za-z0-9_.-]")
    unix_home = re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+")
    offenders = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in (drive, unix_home):
            for m in pattern.finditer(text):
                offenders.append(f"{path.name}: {m.group(0)}")
    assert offenders == [], offenders


def test_cleanup_mode_migrates_from_the_old_bool(cfg_path):
    """A user who had cleanup on must stay on the AI pass, not be silently
    downgraded to no cleanup at all."""
    cfg_path.write_text('{"cleanup": true}', encoding="utf-8")
    assert config.load()["cleanup_mode"] == "ai"

    cfg_path.write_text('{"cleanup": false}', encoding="utf-8")
    assert config.load()["cleanup_mode"] == "off"


def test_explicit_cleanup_mode_wins_over_the_legacy_bool(cfg_path):
    cfg_path.write_text('{"cleanup": true, "cleanup_mode": "fast"}', encoding="utf-8")
    assert config.load()["cleanup_mode"] == "fast"


def test_unknown_cleanup_mode_falls_back_to_off(cfg_path):
    cfg_path.write_text('{"cleanup_mode": "turbo"}', encoding="utf-8")
    assert config.load()["cleanup_mode"] == "off"


def test_legacy_cleanup_key_is_dropped_on_save(cfg_path):
    cfg_path.write_text('{"cleanup": true}', encoding="utf-8")
    cfg = config.load()
    config.save(cfg)
    written = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "cleanup" not in written
    assert written["cleanup_mode"] == "ai"


def test_every_cleanup_mode_has_a_label():
    assert set(config.CLEANUP_LABELS) == set(config.CLEANUP_MODES)
