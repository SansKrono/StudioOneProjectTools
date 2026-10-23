# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Windows onedir build. Run via BUILD_WINDOWS_EXE.bat.

from pathlib import Path

project_root = Path.cwd()

datas = [
    ('README.md', '.'),
    ('VERSION.txt', '.'),
]

# es.exe (Everything command-line client) is third-party and not part of this
# repository. It is bundled only if you placed it in the project root yourself.
if (project_root / 'es.exe').is_file():
    datas.append(('es.exe', '.'))

a = Analysis(
    ['main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StudioOneProjectTools',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-packed executables are a common antivirus false-positive trigger.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='StudioOneProjectTools',
)
