"""Configuration loading with sane defaults and deep-merge overrides.

The shipped ``config.yaml`` is a template; a user-provided file (or a
``--config`` CLI argument) is merged on top of the built-in defaults. No
secret material ever lives in the configuration file.
"""
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

import yaml

# ---------------------------------------------------------------------------
# Built-in defaults (also mirrored by the template config.yaml).
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "database": {"path": "bilibili.db"},
    "auth": {"cookie_file": "cookies.json"},
    "crawler": {
        "page_size": 30,
        # Max pages of the submission list to scan; 0 = unlimited (full scan).
        "max_pages": 0,
        "stop_after_existing": 10,
        "request_timeout": 15,
        "retries": 3,
        "retry_backoff": 1.0,
        "request_interval": 0.3,
    },
    "filter": {"min_duration": 300, "max_duration": 1800},
    "download": {
        "save_root": "downloads",
        "max_speed_mbps": 40,
        "concurrency": 2,
        "qn": 80,
        "prefer_dash": True,
        "ffmpeg_path": "",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "referer": "https://www.bilibili.com",
    },
    "tui": {"refresh_interval": 0.5},
    "logging": {"level": "INFO", "file": "crawler.log"},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Load configuration, merging an optional YAML file over the defaults.

    If ``path`` is None, a ``config.yaml`` next to the current working
    directory is used when it exists.
    """
    config = copy.deepcopy(DEFAULT_CONFIG)

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(Path.cwd() / "config.yaml")

    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"config file {candidate} must contain a YAML mapping")
            config = _deep_merge(config, loaded)

    return config


def save_config(config: Dict[str, Any], path: str | os.PathLike) -> Path:
    """Atomically write a user configuration file and return its path."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return target


def resolve_data_path(config: Dict[str, Any]) -> Path:
    """Return the absolute SQLite database path from the config."""
    path = Path(config["database"]["path"])
    return path.resolve()


def resolve_cookie_path(config: Dict[str, Any]) -> Path:
    """Return the absolute cookie file path from the config."""
    return Path(config["auth"]["cookie_file"]).resolve()
