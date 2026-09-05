# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Hemsa. Build:  .venv\\Scripts\\pyinstaller.exe hemsa.spec
Produces dist\\Hemsa\\Hemsa.exe (onedir: sherpa-onnx DLLs load faster and the
661 MB model stays OUTSIDE the bundle - config.models_dir resolves it at runtime).
Windowed (no console): logs go to %LOCALAPPDATA%\\Hemsa\\hemsa.log as usual."""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

a = Analysis(
    ["launcher.py"],   # NOT hemsa\__main__.py: its relative imports need package context
    pathex=[],
    # PyAV is deliberately NOT bundled. Its wheel carries ~25 ffmpeg DLLs, two of
    # which (libx264, libx265) are GPL in their free builds, and they cannot be
    # dropped individually: avcodec imports them through its PE import table, so
    # deleting them fails PyAV at `import av`. Hemsa is MIT, so the packaged build
    # ships no ffmpeg and meeting file IMPORT is run-from-source only. Recording,
    # transcription and summaries need none of it. tests/test_packaging_licence.py
    # fails if "av" comes back here. See THIRD-PARTY-NOTICES.md.
    binaries=(
        collect_dynamic_libs("sherpa_onnx")
        + collect_dynamic_libs("pyaudiowpatch")
    ),
    # certifi is collected EXPLICITLY. It used to arrive only because something
    # imported requests; if that ever stopped, the model download would fail with
    # SSLCertVerificationError in the packaged build ONLY, never in the venv.
    datas=collect_data_files("certifi") + [
        # the two bundled typefaces (SIL OFL), loaded privately at startup by
        # hemsa/ui/fonts.py; resolved as Path(__file__).parent / "fonts" in the
        # venv and in the onedir bundle alike. tests/test_packaging_licence.py
        # checks they are here and named in THIRD-PARTY-NOTICES.md.
        ("hemsa/fonts/*.ttf", "hemsa/fonts"),
        ("hemsa/fonts/*.txt", "hemsa/fonts"),
    ],
    hiddenimports=[
        "sherpa_onnx",
        "pystray._win32",
        "PIL.ImageDraw", "PIL.ImageFont",
        "requests",
        "pyaudiowpatch",
    ],
    # Belt and braces: even if something pulls av into the dependency graph, it
    # must not reach the bundle. See the binaries note above.
    excludes=["pytest", "matplotlib", "av"],
    hookspath=[],
    runtime_hooks=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Hemsa",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets\\hemsa.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Hemsa",
)
