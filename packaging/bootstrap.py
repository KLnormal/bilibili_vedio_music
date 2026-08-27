"""First-run bootstrap and desktop launcher for the Windows executable.

The PyInstaller executable carries the Python runtime and Python packages. On
first launch this module creates an app-local configuration, verifies the
bundled imports, and installs a portable ffmpeg copy when one is not already
available.  All mutable data is kept under ``%LOCALAPPDATA%`` rather than next
to the executable, so installing a new build does not overwrite the database
or cookies.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable


APP_NAME = "BilibiliVideoWorkbench"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
REQUIRED_IMPORTS = {
    "requests": "requests>=2.31.0",
    "rich": "rich>=13.7.0",
    "qrcode": "qrcode>=7.4.2",
    "yaml": "PyYAML>=6.0.1",
    "PySide6": "PySide6>=6.7.0",
}

# When invoked directly from ``packaging\bootstrap.py`` Python puts only the
# packaging directory on ``sys.path``. Add the checkout root so the same file
# works both from source and from the PyInstaller bundle.
_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))


def _project_root() -> Path:
    return _SOURCE_ROOT


def _data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / ".local" / APP_NAME


def _requirements_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "requirements.txt"
    return _project_root() / "requirements.txt"


def _config_path(explicit: str | None) -> Path:
    return Path(explicit).expanduser().resolve() if explicit else _data_root() / "config.yaml"


def ensure_config(path: Path) -> tuple[Path, bool]:
    """Create a first-run config with absolute, user-writable paths."""
    if path.is_file():
        return path, False

    from bilibili_crawler.config.configuration import DEFAULT_CONFIG, save_config

    data = _data_root()
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["database"]["path"] = str(data / "bilibili.db")
    config["auth"]["cookie_file"] = str(data / "cookies.json")
    config["download"]["save_root"] = str(data / "downloads")
    config["logging"]["file"] = str(data / "crawler.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    save_config(config, path)
    return path, True


def missing_imports() -> list[str]:
    missing: list[str] = []
    for module in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module)
        except Exception:  # pragma: no cover - only exercised by broken installs
            missing.append(module)
    return missing


def _system_python() -> str | None:
    candidates = [shutil.which("python"), shutil.which("py")]
    return next((candidate for candidate in candidates if candidate), None)


def install_missing_dependencies(missing: Iterable[str]) -> tuple[bool, str]:
    """Install missing requirements for source-mode launches.

    A correctly-built frozen executable already contains these packages. The
    fallback is retained for damaged builds and for running this bootstrap
    script directly from a source checkout.
    """
    wanted = [REQUIRED_IMPORTS[name] for name in missing if name in REQUIRED_IMPORTS]
    if not wanted:
        return True, ""
    python = _system_python()
    if not python:
        return False, "未找到 Python，无法安装缺失依赖：" + ", ".join(missing)
    command = [python, "-m", "pip", "install", *wanted]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return False, f"安装依赖失败：{exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-600:]
        return False, f"安装依赖失败（退出码 {completed.returncode}）：{detail}"
    return True, ""


def _bundled_ffmpeg(root: Path) -> Path | None:
    for candidate in (
        root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        root / "ffmpeg" / "bin" / "ffmpeg.exe",
    ):
        if candidate.is_file():
            return candidate
    nested_root = root / "tools" / "ffmpeg"
    if nested_root.is_dir():
        for candidate in nested_root.rglob("ffmpeg.exe"):
            if candidate.is_file() and candidate.parent.name.lower() == "bin":
                return candidate
    return None


def ensure_ffmpeg(root: Path, *, allow_install: bool = True) -> tuple[Path | None, str | None]:
    """Find ffmpeg or install a user-local portable copy on first run."""
    found = shutil.which("ffmpeg")
    if found:
        return Path(found), None
    existing = _bundled_ffmpeg(root)
    if existing:
        return existing, None
    if not allow_install:
        return None, "未检测到 ffmpeg，将使用 progressive 下载回退。"

    target = root / "tools" / "ffmpeg"
    archive = root / "tools" / "ffmpeg.zip.part"
    target.mkdir(parents=True, exist_ok=True)
    try:
        import requests

        with requests.get(FFMPEG_URL, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            with archive.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        stream.write(chunk)
        with zipfile.ZipFile(archive) as package:
            member = next(
                name for name in package.namelist()
                if name.lower().endswith("/bin/ffmpeg.exe")
            )
            package.extract(member, target)
        archive.unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover - network-dependent path
        archive.unlink(missing_ok=True)
        return None, f"ffmpeg 自动安装失败：{exc}；将使用 progressive 下载回退。"
    installed = _bundled_ffmpeg(root)
    if installed:
        return installed, None
    return None, "ffmpeg 压缩包中未找到 ffmpeg.exe，将使用 progressive 下载回退。"


def _set_ffmpeg(config_path: Path, ffmpeg: Path | None) -> None:
    if ffmpeg is None:
        return
    import yaml

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config.setdefault("download", {})["ffmpeg_path"] = str(ffmpeg)
    from bilibili_crawler.config.configuration import save_config

    save_config(config, config_path)


def _dialog(title: str, message: str, *, warning: bool = False) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    QApplication.instance() or QApplication(sys.argv)
    if warning:
        QMessageBox.warning(None, title, message)
    else:
        QMessageBox.information(None, title, message)


def run_bootstrap(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--config", default=None, help="override the user config path")
    parser.add_argument("--check-only", action="store_true", help="check setup and exit")
    parser.add_argument("--skip-ffmpeg", action="store_true", help="do not download ffmpeg")
    args = parser.parse_args(argv)

    from bilibili_crawler.desktop.app import _prepare_interactive_qt_platform

    _prepare_interactive_qt_platform()
    from PySide6.QtWidgets import QApplication, QProgressDialog

    qt_app = QApplication.instance() or QApplication(sys.argv)
    data = _data_root()
    data.mkdir(parents=True, exist_ok=True)
    config_path, first_run = ensure_config(_config_path(args.config))

    progress = QProgressDialog("正在检查运行环境…", None, 0, 0)
    progress.setWindowTitle("Bilibili 工作台首次启动")
    progress.setMinimumDuration(0)
    progress.setCancelButton(None)
    progress.show()
    qt_app.processEvents()

    missing = missing_imports()
    dependency_error = None
    if missing:
        ok, dependency_error = install_missing_dependencies(missing)
        if ok:
            missing = missing_imports()
        if missing and not dependency_error:
            dependency_error = "仍缺少依赖：" + ", ".join(missing)

    ffmpeg, ffmpeg_warning = ensure_ffmpeg(
        data, allow_install=first_run and not args.skip_ffmpeg
    )
    _set_ffmpeg(config_path, ffmpeg)
    progress.close()

    messages = [m for m in (dependency_error, ffmpeg_warning) if m]
    if args.check_only:
        if messages:
            print("\n".join(messages))
            return 1 if dependency_error else 0
        print(f"OK: config={config_path}; ffmpeg={ffmpeg or 'progressive fallback'}")
        return 0
    if dependency_error:
        _dialog("环境检查失败", dependency_error, warning=True)
        return 1
    if messages:
        _dialog("环境提示", "\n".join(messages), warning=True)

    from bilibili_crawler.desktop import run_desktop

    return run_desktop(["--config", str(config_path)])


if __name__ == "__main__":
    raise SystemExit(run_bootstrap())
