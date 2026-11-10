# -*- mode: python ; coding: utf-8 -*-
"""Build the Windows onedir release with its native runtime dependencies."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


PROJECT_ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(PROJECT_ROOT / "models" / "yolox_tiny.onnx"), "models"),
    (str(PROJECT_ROOT / "models" / "pose_landmarker_lite.task"), "models"),
    (str(PROJECT_ROOT / "tests" / "fixtures" / "standing_person_cc0.jpg"), "tests/fixtures"),
    (str(PROJECT_ROOT / "assets" / "splash.png"), "assets"),
    (str(PROJECT_ROOT / "assets" / "ui"), "assets/ui"),
]
binaries = []
hiddenimports = []

# These packages load native libraries, codecs, providers, or WASM files at runtime.
for package in (
    "aiortc",
    "av",
    "curl_cffi",
    "mediapipe",
    "onnxruntime",
    "sounddevice",
    "unitree_webrtc_connect",
    "wasmtime",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += copy_metadata("unitree_webrtc_connect")

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # MediaPipe -> matplotlib -> pyparsing.testing imports unittest at runtime.
    excludes=["pytest", "tkinter.test"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Go2 Controller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=str(PROJECT_ROOT / "assets" / "ui" / "app_icon.ico"),
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Go2 Controller",
)
