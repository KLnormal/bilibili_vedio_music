"""Download-root normalization and filesystem indexing.

The database stores media state for the *active* download root.  This module
contains only filesystem concerns so the application and repository can keep
the reconciliation policy deterministic and testable.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple


_BVID_RE = re.compile(r"\[(BV[0-9A-Za-z]{5,20})\]")
MediaKey = Tuple[str, str]


@dataclass(frozen=True)
class MediaFile:
    bvid: str
    media_type: str
    path: Path
    mtime_ns: int
    size: int


def normalize_download_root(path: str | os.PathLike) -> Path:
    """Return a stable absolute path used for root identity and I/O."""
    if path is None or not str(path).strip():
        raise ValueError("download root must not be empty")
    return Path(path).expanduser().resolve(strict=False)


def ensure_writable_root(path: str | os.PathLike) -> Path:
    """Create a root if needed and verify that it is writable."""
    root = normalize_download_root(path)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    probe = None
    fd = None
    try:
        fd, name = tempfile.mkstemp(prefix=".bili-write-test-", dir=str(root))
        probe = Path(name)
        with os.fdopen(fd, "wb") as fh:
            fd = None
            fh.write(b"ok")
            fh.flush()
    except OSError:
        if probe is not None:
            probe.unlink(missing_ok=True)
        raise
    finally:
        if fd is not None:
            os.close(fd)
        if probe is not None:
            probe.unlink(missing_ok=True)
    return root


def build_media_file_index(path: str | os.PathLike) -> Dict[MediaKey, MediaFile]:
    """Recursively index valid final MP4/M4A files below ``path``.

    Directory symlinks are not followed.  A failed stat/read is surfaced to
    the caller so a directory switch can safely roll back instead of silently
    marking every missing file as pending.
    """
    root = normalize_download_root(path)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    found: Dict[MediaKey, MediaFile] = {}

    def onerror(error):
        raise OSError(f"cannot scan download directory: {error}") from error

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        # Explicitly prune symlinked directories (and avoid descending into
        # reparse-point aliases where the platform exposes them as links).
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for filename in filenames:
            suffix = Path(filename).suffix.casefold()
            media_type = {".mp4": "video", ".m4a": "audio"}.get(suffix)
            if media_type is None or filename.casefold().endswith(".part"):
                continue
            matches = _BVID_RE.findall(filename)
            if not matches:
                continue
            bvid = matches[-1]
            file_path = Path(dirpath, filename).resolve(strict=False)
            try:
                stat = file_path.stat()
            except OSError as exc:
                raise OSError(f"cannot inspect media file: {file_path}: {exc}") from exc
            if stat.st_size <= 0:
                continue
            item = MediaFile(bvid, media_type, file_path, stat.st_mtime_ns, stat.st_size)
            key = (bvid, media_type)
            previous = found.get(key)
            if previous is None or (item.mtime_ns, str(item.path).casefold()) > (previous.mtime_ns, str(previous.path).casefold()):
                found[key] = item
    return found
