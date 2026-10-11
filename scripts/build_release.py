"""Bump the version, test, build the exe and the installer, print the installer's SHA256.

    .venv\\Scripts\\python.exe scripts\\build_release.py 0.6.5

The version goes into BOTH hemsa/__init__.py and installer/hemsa.iss: two builds with
the same version string cannot be told apart. No git here, commit and push yourself.
"""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "hemsa" / "__init__.py"
ISS = ROOT / "installer" / "hemsa.iss"

INIT_RE = re.compile(r'^__version__ = "([^"]+)"', re.M)
ISS_RE = re.compile(r'^#define AppVersion "([^"]+)"', re.M)


def parse(v: str) -> tuple[int, int, int]:
    """A plain MAJOR.MINOR.PATCH, nothing else (no 'v', no '-rc1', no leading zeros)."""
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", v):
        raise ValueError(f"not a plain MAJOR.MINOR.PATCH: {v!r}")
    return tuple(int(p) for p in v.split("."))


def current(init: Path = INIT, iss: Path = ISS) -> str:
    a = INIT_RE.search(init.read_text(encoding="utf-8"))
    b = ISS_RE.search(iss.read_text(encoding="utf-8"))
    if not a or not b:
        raise ValueError("version line not found in __init__.py or hemsa.iss")
    if a.group(1) != b.group(1):
        raise ValueError(f"__init__.py says {a.group(1)}, hemsa.iss says {b.group(1)}: fix by hand first")
    return a.group(1)


def bump(new: str, init: Path = INIT, iss: Path = ISS) -> str:
    old = current(init, iss)
    if parse(new) <= parse(old):
        raise ValueError(f"{new} is not higher than the current {old}")
    for path, rx, line in ((init, INIT_RE, f'__version__ = "{new}"'),
                           (iss, ISS_RE, f'#define AppVersion "{new}"')):
        raw = path.read_bytes().decode("utf-8")   # bytes, so CRLF line endings survive
        path.write_bytes(rx.sub(line, raw, count=1).encode("utf-8"))
    return old


def run(cmd: list[str]) -> None:
    print("\n>>", " ".join(cmd), flush=True)
    code = subprocess.run(cmd, cwd=ROOT).returncode
    if code != 0:
        sys.exit(f"STOPPED: exit {code} from {cmd[0]}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: build_release.py MAJOR.MINOR.PATCH")
    new = sys.argv[1]
    try:
        old = bump(new)
    except ValueError as e:
        sys.exit(f"STOPPED: {e}")
    print(f"version {old} -> {new}")

    venv = ROOT / ".venv" / "Scripts"
    iscc = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Inno Setup 6" / "ISCC.exe"
    run([str(venv / "python.exe"), "-m", "pytest", "tests", "-q"])
    run([str(venv / "pyinstaller.exe"), "hemsa.spec", "--noconfirm"])
    run([str(iscc), str(ISS)])

    out = ROOT / "installer" / "out" / f"HemsaSetup-{new}.exe"
    if not out.exists():
        sys.exit(f"STOPPED: ISCC exited 0 but {out} is missing")
    data = out.read_bytes()
    print(f"\ninstaller: {out}")
    print(f"size:      {len(data):,} bytes ({len(data) / 1e6:.1f} MB)")
    print(f"sha256:    {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
