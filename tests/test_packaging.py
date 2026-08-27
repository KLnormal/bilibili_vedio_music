"""Offline checks for the first-run Windows executable bootstrap."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_bootstrap():
    path = Path(__file__).parents[1] / "packaging" / "bootstrap.py"
    spec = importlib.util.spec_from_file_location("bilibili_bootstrap_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap()


class BootstrapTest(unittest.TestCase):
    def test_bundled_imports_are_available(self):
        self.assertEqual(bootstrap.missing_imports(), [])

    def test_first_run_config_uses_writable_data_paths(self):
        root = Path(tempfile.mkdtemp())
        data_root = root / "data"
        with mock.patch.object(bootstrap, "_data_root", return_value=data_root):
            path, first_run = bootstrap.ensure_config(root / "config.yaml")
            self.assertTrue(first_run)
            self.assertTrue(path.is_file())
            content = path.read_text(encoding="utf-8")
            self.assertIn(str(data_root / "bilibili.db"), content)
            self.assertIn(str(data_root / "downloads"), content)

            _, second_run = bootstrap.ensure_config(path)
            self.assertFalse(second_run)

    def test_missing_ffmpeg_can_continue_with_progressive_fallback(self):
        root = Path(tempfile.mkdtemp())
        with mock.patch.object(bootstrap.shutil, "which", return_value=None):
            ffmpeg, warning = bootstrap.ensure_ffmpeg(root, allow_install=False)
        self.assertIsNone(ffmpeg)
        self.assertIn("progressive", warning or "")


if __name__ == "__main__":
    unittest.main()
