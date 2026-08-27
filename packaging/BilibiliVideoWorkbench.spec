# PyInstaller one-file build for the interactive Windows desktop workbench.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).parent
hiddenimports = (
    collect_submodules("bilibili_crawler")
    + collect_submodules("qrcode")
)
datas = collect_data_files("qrcode")
datas.append((str(project_root / "requirements.txt"), "."))

a = Analysis(
    [str(project_root / "packaging" / "bootstrap.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BilibiliVideoWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
