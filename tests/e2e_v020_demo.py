"""v0.2 端到端测试案例：Empty_old_City 1080p+ / 120~300s / Buffer 黑名单

测试案例（用户指定）：
    UP          : 3546660365928495 (Empty_old_City)
    清晰度      : 1080p+（1080P 高码率，qn 112）
    时长窗口    : 120s ~ 300s（含边界）
    黑名单关键字: Buffer（大小写不敏感子串匹配）

验证链路（对应 v0.2 验收）：
    add -> scan -> 窗口筛选 -> 黑名单 add -> download(1080p+)
    -> Buffer 命中 FILTERED / 其余 READY 下载 -> ffprobe 验证 1080P
    -> preview 统计 -> 增量 scan

运行（需已登录 cookies.json + ffmpeg）：
    python tests/e2e_v020_demo.py

退出码 0 = 全部通过。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bilibili_crawler.app import App  # noqa: E402
from bilibili_crawler.database.models import DownloadStatus  # noqa: E402
from bilibili_crawler.options import DownloadOptions  # noqa: E402

# ---------------------------------------------------------------------------
# 测试案例参数（可修改后复用）
# ---------------------------------------------------------------------------
UP_MID = 3546660365928495
UP_NAME_EXPECTED = "Empty_old_City"
DUR_MIN, DUR_MAX = 120, 300
BLACKLIST_KW = "Buffer"
QUALITY = "1080p+"

RESULTS: List[tuple] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail else ""))


def find_ffmpeg() -> Optional[str]:
    """Locate ffmpeg: PATH first, then the winget Gyan build."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    winget = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for pkg in sorted(winget.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe")):
        return str(pkg)
    return None


def ffprobe_info(ffmpeg: str, path: Path) -> Optional[dict]:
    ffprobe = str(Path(ffmpeg).parent / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe"))
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,codec_name", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        import json
        streams = json.loads(r.stdout or "{}").get("streams") or []
        if not streams:
            return None
        return {"width": streams[0].get("width", 0), "height": streams[0].get("height", 0)}
    except Exception as exc:  # noqa: BLE001
        print(f"    ffprobe error: {exc}")
        return None


def wait_downloads(app: App, mid: int, timeout_s: int = 1800) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        statuses = app.repo.count_by_status(mid)
        active = statuses.get("PENDING", 0) + statuses.get("DOWNLOADING", 0)
        if active == 0:
            return
        time.sleep(2)


def main() -> int:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg not found; 1080p+ DASH merge requires ffmpeg.")
        return 1

    # ---- 独立测试沙箱（数据库 / 下载目录） --------------------------------
    tmp = Path(tempfile.mkdtemp())
    cfg = {
        "database": {"path": str(tmp / "test_v020.db")},
        "download": {
            "save_root": str(tmp / "dl"),
            "ffmpeg_path": ffmpeg,
            "qn": 80,          # scan 阶段不用 qn；下载用 options 覆盖为 1080p+
            "concurrency": 2,
        },
        # auth.cookie_file 保持默认 -> 读项目根目录 cookies.json（已登录）
        "filter": {"min_duration": DUR_MIN, "max_duration": DUR_MAX},
        "crawler": {"max_pages": 0, "request_interval": 0.5},
    }
    cfg_path = tmp / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    app = App(config_path=str(cfg_path))
    print("=" * 70)
    print(f"测试案例: UP={UP_NAME_EXPECTED}({UP_MID}) quality={QUALITY} "
          f"window=[{DUR_MIN},{DUR_MAX}]s blacklist={BLACKLIST_KW}")
    print(f"login: {app.login.is_logged_in} | ffmpeg: {ffmpeg}")
    print("=" * 70)

    try:
        # ---- T1 添加 UP ----------------------------------------------------
        up = app.add_up(UP_MID)
        check("T1 add UP", up.mid == UP_MID and up.name == UP_NAME_EXPECTED, f"name={up.name!r}")

        # ---- T2 全量扫描 ----------------------------------------------------
        stats = app.scan(UP_MID)
        check("T2 scan discovers videos", stats.new > 0, f"new={stats.new}")

        # ---- T3 时长窗口筛选 ------------------------------------------------
        videos = app.repo.list_videos(UP_MID)
        in_window = [v for v in videos if v.duration is not None and DUR_MIN <= v.duration <= DUR_MAX]
        out_window = [v for v in videos if v.duration is not None and v not in in_window]
        bad_pending = [v.bvid for v in in_window if v.download_status is not DownloadStatus.PENDING]
        check("T3 in-window -> PENDING", not bad_pending,
              f"window={len(in_window)} wrong={bad_pending}")
        print(f"    window bvids: {[v.bvid for v in in_window]}")

        # ---- T4 黑名单 -------------------------------------------------------
        app.add_blacklist(UP_MID, BLACKLIST_KW)
        hit_bvids = [v.bvid for v in in_window if BLACKLIST_KW.lower() in v.title.casefold()]
        check("T4 blacklist added", len(hit_bvids) > 0, f"hits={len(hit_bvids)} {hit_bvids}")

        # ---- T5 下载（1080p+，决策器重新评估黑名单/时长） ----------------------
        options = DownloadOptions(quality=QUALITY, media_type="video",
                                  min_duration=DUR_MIN, max_duration=DUR_MAX)
        prepared = app.prepare_download(UP_MID, options)
        check("T5 prepare: Buffer hit -> FILTERED, rest READY",
              prepared["filtered"] == len(hit_bvids) and prepared["ready"] == len(in_window) - len(hit_bvids),
              f"ready={prepared['ready']} filtered={prepared['filtered']}")

        app.download_manager.set_options(options)
        app.download_manager.set_mid(UP_MID)
        app.download_manager.start()
        try:
            wait_downloads(app, UP_MID)
        finally:
            app.download_manager.stop()
            app.download_manager.join(timeout=10)

        # ---- T6 状态验证 -----------------------------------------------------
        fresh = {v.bvid: app.repo.get_video(v.bvid) for v in in_window}
        downloaded = [b for b, v in fresh.items() if v.download_status is DownloadStatus.DOWNLOADED]
        filtered = {b: v.filter_reason for b, v in fresh.items()
                    if v.download_status is DownloadStatus.FILTERED}
        check("T6 Buffer-hit videos FILTERED with reason",
              set(filtered) == set(hit_bvids)
              and all(r == f"blacklist: {BLACKLIST_KW}" for r in filtered.values()),
              f"filtered={list(filtered.items())}")
        check("T6 others DOWNLOADED", len(downloaded) == len(in_window) - len(hit_bvids),
              f"downloaded={len(downloaded)}")

        # ---- T7 ffprobe 清晰度验证（>=1080P） --------------------------------
        bad_media = []
        for bvid in downloaded:
            path = Path(fresh[bvid].download_path) if fresh[bvid].download_path else None
            if path is None or not path.is_file():
                bad_media.append((bvid, "file missing"))
                continue
            info = ffprobe_info(ffmpeg, path)
            if info is None or info["width"] < 1920:
                bad_media.append((bvid, info))
        check("T7 media >= 1080P", not bad_media, f"bad={bad_media}")

        # ---- T8 preview 统计 --------------------------------------------------
        # preview 统计全部视频的决策：窗口外(时长过滤) + 窗口内(黑名单过滤) 均为 FILTERED。
        pv = app.preview(UP_MID, options)
        pv_stats = pv["stats"]
        expected_filtered = len(videos) - len(downloaded)
        check("T8 preview stats consistent",
              pv_stats.get("DOWNLOADED", 0) == len(downloaded)
              and pv_stats.get("FILTERED", 0) == expected_filtered,
              f"stats={pv_stats} expected FILTERED={expected_filtered}")

        # ---- T9 增量 scan -----------------------------------------------------
        stats2 = app.scan(UP_MID)
        check("T9 re-scan finds no new", stats2.new == 0,
              f"new={stats2.new} existing={stats2.existing}")

        # ---- summary ------------------------------------------------------------
        print("=" * 70)
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"SUMMARY: {passed}/{len(RESULTS)} passed")
        for name, ok, detail in RESULTS:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
        print(f"沙箱目录（如需检查文件）: {tmp}")
        print("=" * 70)
        return 0 if passed == len(RESULTS) else 1
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
