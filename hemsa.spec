# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Hemsa. Build:  .venv\\Scripts\\pyinstaller.exe hemsa.spec
Produces dist\\Hemsa\\Hemsa.exe (onedir: sherpa-onnx DLLs load faster and the
661 MB model stays OUTSIDE the bundle - config.models_dir resolves it at runtime).
Windowed (no console): logs go to %LOCALAPPDATA%\\Hemsa\\hemsa.log as usual."""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

a = Analysis(
    ["launcher.py"],   # NOT hemsa\__main__.py: its relative imports need package context
    pathex=[],
    binaries=collect_dynamic_libs("sherpa_onnx"),
    # certifi is collected EXPLICITLY. It used to arrive only because something
    # imported requests; if that ever stopped, the model download would fail with
    # SSLCertVerificationError in the packaged build ONLY, never in the venv.
    datas=collect_data_files("certifi"),
    hiddenimports=[
        "sherpa_onnx",
        "pystray._win32",
        "PIL.ImageDraw", "PIL.ImageFont",
        "requests",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "matplotlib"],
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
