"""End-to-end demo test (T1~T6) against the real Bilibili API.

Sandboxed test harness used on the ``debug_test`` branch. It drives the same
``App`` / crawler / downloader / database code paths as production, but reads
``test_config.yaml`` (isolated database, isolated download directory, 180~200s
duration window, highest quality, max 2 scan pages).

Run from the project root:

    python tests/e2e_demo.py

Prerequisites: valid ``cookies.json`` (login) and ffmpeg (via
``test_config.yaml``'s ``download.ffmpeg_path``).

Exit code: 0 = all tests passed, 1 = at least one failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bilibili_crawler.app import App  # noqa: E402
from bilibili_crawler.database.models import DownloadStatus  # noqa: E402

# ---------------------------------------------------------------------------
# Test fixture (per the agreed minimal test set)
# ---------------------------------------------------------------------------
UP_MID = 3546660365928495          # Empty_old_City
UP_NAME_EXPECTED = "Empty_old_City"
DUR_MIN, DUR_MAX = 180, 200        # seconds, inclusive
CONFIG_PATH = ROOT / "test_config.yaml"

RESULTS: List[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail else ""))


def reset_sandbox() -> None:
    for pattern in ("test_bilibili.db", "test_bilibili.db-wal", "test_bilibili.db-shm", "test_bilibili.db-journal"):
        (ROOT / pattern).unlink(missing_ok=True)
    test_log = ROOT / "test_crawler.log"
    test_log.unlink(missing_ok=True)
    # Keep test_downloads for inspection on failure; clear it for a fresh run.
    for f in (ROOT / "test_downloads").rglob("*"):
        if f.is_file():
            f.unlink()


def ffprobe_cmd(app: App, path: Path) -> List[str]:
    ffmpeg = Path(app.downloader.ffmpeg_path)
    ffprobe = ffmpeg.parent / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    if not ffprobe.exists():
        raise FileNotFoundError(f"ffprobe not found next to ffmpeg: {ffprobe}")
    return [str(ffprobe)]


def probe_file(app: App, path: Path) -> Optional[dict]:
    """Return {width, has_audio, duration} via ffprobe, or None on failure."""
    try:
        base = ffprobe_cmd(app, path)
        v = subprocess.run(
            base + ["-v", "error", "-select_streams", "v:0", "-show_entries",
                    "stream=width,height,codec_name", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        a = subprocess.run(
            base + ["-v", "error", "-select_streams", "a", "-show_entries",
                    "stream=codec_name", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        f = subprocess.run(
            base + ["-v", "error", "-show_entries", "format=duration", "-of", "json",
                    str(path)],
            capture_output=True, text=True, timeout=60,
        )
        vj = json.loads(v.stdout or "{}")
        aj = json.loads(a.stdout or "{}")
        fj = json.loads(f.stdout or "{}")
        streams = vj.get("streams") or []
        if not streams:
            return None
        audio = bool(aj.get("streams"))
        dur = float((fj.get("format") or {}).get("duration", -1))
        return {
            "width": streams[0].get("width", 0),
            "height": streams[0].get("height", 0),
            "has_audio": audio,
            "duration": dur,
            "codec": streams[0].get("codec_name", ""),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"    ffprobe error for {path}: {exc}")
        return None


def wait_downloads(app: App, expected_bvids: set, timeout_s: int = 1200) -> dict:
    """Wait until every expected bvid settles; return final status map."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        statuses = {}
        for bvid in expected_bvids:
            v = app.repo.get_video(bvid)
            statuses[bvid] = v.download_status if v else None
        if all(s in (DownloadStatus.DOWNLOADED, DownloadStatus.FAILED)
               for s in statuses.values()):
            return statuses
        time.sleep(2)
    return statuses


def main() -> int:
    if not CONFIG_PATH.is_file():
        print(f"missing {CONFIG_PATH.name}; create the test sandbox config first")
        return 1

    reset_sandbox()
    app = App(config_path=str(CONFIG_PATH))

    print("=" * 70)
    print(f"UP: mid={UP_MID}  expected name={UP_NAME_EXPECTED}")
    print(f"duration window: [{DUR_MIN}, {DUR_MAX}]s | qn: {app.config['download']['qn']}")
    print(f"max_pages: {app.config['crawler']['max_pages']} | login: {app.login.is_logged_in}")
    print(f"ffmpeg: {app.downloader.ffmpeg_path}")
    print("=" * 70)

    # ---- T1 add UP ----------------------------------------------------------
    try:
        up = app.add_up(UP_MID)
        check("T1 add UP", up.mid == UP_MID and up.name == UP_NAME_EXPECTED,
              f"name={up.name!r}")
    except Exception as exc:  # noqa: BLE001
        check("T1 add UP", False, f"exception: {exc}")
        return 1

    # ---- T2 scan (bounded full scan) ----------------------------------------
    try:
        stats = app.scan(UP_MID)
        check("T2 scan discovers videos", stats.new > 0,
              f"new={stats.new} existing={stats.existing} eligible={stats.eligible} "
              f"filtered={stats.filtered} failed={stats.failed}")
    except Exception as exc:  # noqa: BLE001
        check("T2 scan discovers videos", False, f"exception: {exc}")
        return 1

    videos = app.repo.list_videos(UP_MID)
    bvids = [v.bvid for v in videos]
    unique = len(bvids) == len(set(bvids))
    check("T2 bvid uniqueness", unique, f"{len(bvids)} rows, {len(set(bvids))} unique")

    # ---- T3 duration filter --------------------------------------------------
    in_window = [v for v in videos
                 if v.duration is not None and DUR_MIN <= v.duration <= DUR_MAX]
    out_window = [v for v in videos if v not in in_window]
    bad_pending = [v.bvid for v in in_window if v.download_status is not DownloadStatus.PENDING]
    bad_skipped = [v.bvid for v in out_window if v.download_status is not DownloadStatus.SKIPPED]
    check("T3 filter marks in-window PENDING", not bad_pending,
          f"in_window={len(in_window)} wrong={bad_pending}")
    check("T3 filter marks others SKIPPED", not bad_skipped,
          f"out_window={len(out_window)} wrong={bad_skipped}")
    print(f"    in-window bvids: {[v.bvid for v in in_window]}")

    if not in_window:
        print("no in-window videos found; cannot continue to T4/T5")
        return 1

    # ---- T4 download ----------------------------------------------------------
    app.download_manager.start()
    try:
        statuses = wait_downloads(app, {v.bvid for v in in_window})
    finally:
        app.download_manager.stop()
        app.download_manager.join(timeout=5)

    failed = {b for b, s in statuses.items() if s is DownloadStatus.FAILED}
    downloaded = {b for b, s in statuses.items() if s is DownloadStatus.DOWNLOADED}
    check("T4 all in-window DOWNLOADED", not failed and downloaded == {v.bvid for v in in_window},
          f"downloaded={len(downloaded)} failed={len(failed)}")

    # Re-fetch fresh rows: download_path is written by the download workers
    # *after* the scan, so the T3-time Video objects are stale.
    fresh = {v.bvid: app.repo.get_video(v.bvid) for v in in_window}

    missing_files = []
    for v in in_window:
        fv = fresh[v.bvid]
        path = Path(fv.download_path) if fv and fv.download_path else None
        if not path or not path.is_file() or not path.name.endswith(f"[{v.bvid}].mp4"):
            missing_files.append(v.bvid)
    check("T4 files exist with [BVxxx] name", not missing_files, f"missing={missing_files}")

    # ---- T5 media verification ------------------------------------------------
    bad_media = []
    for v in in_window:
        fv = fresh[v.bvid]
        path = Path(fv.download_path) if fv and fv.download_path else None
        if path is None or not path.is_file():
            bad_media.append((v.bvid, "file missing"))
            continue
        info = probe_file(app, path)
        if info is None:
            bad_media.append((v.bvid, "ffprobe failed"))
            continue
        problems = []
        if info["width"] < 1920:
            problems.append(f"width={info['width']}x{info['height']}")
        if not info["has_audio"]:
            problems.append("no audio stream")
        if abs(info["duration"] - (v.duration or 0)) > 2:
            problems.append(f"duration={info['duration']:.1f}s vs api={v.duration}s")
        print(f"    {v.bvid}: {info['width']}x{info['height']} {info['codec']} "
              f"audio={info['has_audio']} dur={info['duration']:.1f}s")
        if problems:
            bad_media.append((v.bvid, "; ".join(problems)))
    check("T5 media >=1080p + audio + duration", not bad_media,
          f"bad={bad_media}")

    # ---- T6 incremental re-scan ------------------------------------------------
    try:
        stats2 = app.scan(UP_MID)
        check("T6 re-scan finds no new", stats2.new == 0,
              f"new={stats2.new} existing={stats2.existing}")
        still_downloaded = all(
            app.repo.get_video(v.bvid).download_status is DownloadStatus.DOWNLOADED
            for v in in_window
        )
        check("T6 downloaded state unchanged", still_downloaded)
    except Exception as exc:  # noqa: BLE001
        check("T6 re-scan finds no new", False, f"exception: {exc}")

    # ---- summary ------------------------------------------------------------------
    print("=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"SUMMARY: {passed}/{total} passed")
    for name, ok, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
