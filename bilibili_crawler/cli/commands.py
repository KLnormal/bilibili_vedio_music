"""Command-line interface (headless operation).

The core system is fully usable without the TUI (plan principle #4). Commands:

    python main.py login                 # interactive login (QR / cookie)
    python main.py add <mid>             # add an UP
    python main.py remove <mid>          # remove an UP
    python main.py list                  # list UPs
    python main.py scan [--mid MID]      # discover videos (full/incremental)
    python main.py download              # download all PENDING videos (once)
    python main.py retry                 # reset FAILED -> PENDING
    python main.py run [--mid MID]       # scan + download loop (headless)
    python main.py limit <MB/s>          # print/change download speed limit

With no subcommand, the btop-style TUI is launched.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from collections import Counter
from typing import List, Optional

from .. import __version__
from ..app import App, check_ffmpeg
from ..filter.explain import explain
from ..options import DownloadOptions, MEDIA_TYPES, QUALITY_TO_QN


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bili-crawler",
        description="Bilibili UP-main video discovery and download system (v0.1)",
    )
    parser.add_argument("--config", help="path to a config.yaml override")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("login", help="interactive login (QR code or cookie paste)")

    p_add = sub.add_parser("add", help="add an UP by mid")
    p_add.add_argument("mid", type=int)

    p_rm = sub.add_parser("remove", help="remove an UP by mid")
    p_rm.add_argument("mid", type=int)

    sub.add_parser("list", help="list all UPs")

    p_scan = sub.add_parser("scan", help="scan submissions (full or incremental)")
    p_scan.add_argument("--mid", type=int, default=None, help="scan a single mid")

    p_dl = sub.add_parser("download", help="download PENDING videos once")
    p_dl.add_argument("--mid", type=int, default=None)
    p_dl.add_argument("--quality", choices=list(QUALITY_TO_QN.keys()), default=None,
                      help="720p/1080p/1080p60/4k")
    p_dl.add_argument("--type", dest="media_type", choices=MEDIA_TYPES, default="video",
                      help="video (mp4) or audio (m4a)")
    p_dl.add_argument("--min-duration", type=int, default=None, help="seconds, inclusive")
    p_dl.add_argument("--max-duration", type=int, default=None, help="seconds, inclusive")

    p_retry = sub.add_parser("retry", help="reset FAILED videos to PENDING")
    p_retry.add_argument("--mid", type=int, default=None)

    p_status = sub.add_parser("status", help="show download status (global or per UP)")
    p_status.add_argument("mid", type=int, nargs="?", default=None)

    p_check = sub.add_parser("check", help="check DB vs local files, recover MISSING -> PENDING")
    p_check.add_argument("mid", type=int, nargs="?", default=None)

    p_preview = sub.add_parser("preview", help="preview download decisions (dry-run)")
    p_preview.add_argument("mid", type=int, nargs="?", default=None)
    p_preview.add_argument("--quality", choices=list(QUALITY_TO_QN.keys()), default=None)
    p_preview.add_argument("--type", dest="media_type", choices=MEDIA_TYPES, default="video")
    p_preview.add_argument("--min-duration", type=int, default=None)
    p_preview.add_argument("--max-duration", type=int, default=None)
    p_preview.add_argument("--explain", metavar="BVID", default=None, help="explain a single video")

    p_dlbv = sub.add_parser("download-bv", help="download video(s) directly by bvid (bypass UP rules)")
    p_dlbv.add_argument("bvids", nargs="+", help="one or more BV ids")
    p_dlbv.add_argument("--quality", choices=list(QUALITY_TO_QN.keys()), default=None,
                        help="720p/1080p/1080p60/4k")
    p_dlbv.add_argument("--type", dest="media_type", choices=MEDIA_TYPES, default="video",
                        help="video (mp4) or audio (m4a)")

    p_bl = sub.add_parser("blacklist", help="manage UP title blacklist")
    bl_sub = p_bl.add_subparsers(dest="bl_action", required=True)
    bl_add = bl_sub.add_parser("add", help="add a keyword")
    bl_add.add_argument("mid", type=int)
    bl_add.add_argument("keyword")
    bl_rm = bl_sub.add_parser("remove", help="remove a keyword")
    bl_rm.add_argument("mid", type=int)
    bl_rm.add_argument("keyword")
    bl_list = bl_sub.add_parser("list", help="list keywords")
    bl_list.add_argument("mid", type=int)

    p_limit = sub.add_parser("limit", help="show or set download speed limit (MB/s)")
    p_limit.add_argument("mbps", type=float, nargs="?")

    p_run = sub.add_parser("run", help="scan + download loop (headless)")
    p_run.add_argument("--mid", type=int, default=None)
    p_run.add_argument("--once", action="store_true", help="single scan pass then exit")

    return parser


def _cmd_login(app: App) -> int:
    ok = app.login.login()
    return 0 if ok else 1


def _cmd_add(app: App, mid: int) -> int:
    up = app.add_up(mid)
    print(f"UP added: {up.name or up.mid} (mid={up.mid})")
    return 0


def _cmd_remove(app: App, mid: int) -> int:
    if app.remove_up(mid):
        print(f"UP removed: mid={mid}")
        return 0
    print(f"UP not found: mid={mid}")
    return 1


def _cmd_list(app: App) -> int:
    ups = app.list_ups()
    if not ups:
        print("(no UP added yet)")
        return 0
    for up in ups:
        count = app.repo.count_videos(up.mid)
        flag = "" if up.enabled else " [disabled]"
        last = up.last_crawl_time or "-"
        print(f"mid={up.mid:<10} videos={count:<6} last_crawl={last:<20} {up.name}{flag}")
    return 0


def _cmd_scan(app: App, mid: Optional[int]) -> int:
    print("Scanning...")
    stats = app.scan(mid)
    print(
        f"new={stats.new} existing={stats.existing} eligible={stats.eligible} "
        f"filtered={stats.filtered} failed={stats.failed}"
    )
    return 0


def _download_once(app: App, mid: Optional[int], options: DownloadOptions) -> None:
    # Foreground download loop for headless CLI use.
    if app.has_ffmpeg is False:
        print("[warn] ffmpeg not found; DASH streams will fall back to progressive.")
    # Re-evaluate rules (duration / blacklist) before downloading: only READY
    # videos stay PENDING; the rest are marked FILTERED with a reason.
    counts = app.prepare_download(mid, options)
    if counts["filtered"]:
        print(f"filtered {counts['filtered']} video(s) by rules (blacklist/duration).")
    pending = app.repo.list_pending(mid=mid, limit=1000)
    print(f"{len(pending)} PENDING video(s) to download.")
    app.state.set_pending_count(len(pending))
    # Restrict the workers to the requested UP (if any) and apply options.
    app.download_manager.set_options(options)
    app.download_manager.set_mid(mid)
    app.download_manager.start()
    try:
        # Wait until BOTH pending and in-flight (DOWNLOADING) tasks are done.
        # Checking only PENDING is buggy: a worker flips PENDING -> DOWNLOADING
        # the moment it claims a video, so PENDING briefly hits 0 while a
        # download is still running, and the loop would exit early.
        while True:
            statuses = app.repo.count_by_status(mid)
            active = statuses.get("PENDING", 0) + statuses.get("DOWNLOADING", 0)
            if active == 0:
                break
            time.sleep(0.5)
    finally:
        app.download_manager.stop()
        app.download_manager.join(timeout=10)
    snap = app.state.snapshot()
    print(f"downloaded={snap.downloaded_count} failed={snap.failed_count}")


def _cmd_download(app: App, mid: Optional[int], options: DownloadOptions) -> int:
    _download_once(app, mid, options)
    return 0


def _cmd_retry(app: App, mid: Optional[int]) -> int:
    n = app.reset_failed(mid)
    print(f"Reset {n} FAILED video(s) to PENDING.")
    return 0


def _cmd_status(app: App, mid: Optional[int]) -> int:
    s = app.status(mid)
    if mid is not None and "up" in s:
        up = s["up"]
        print(f"UP: {up['name']} (mid={mid})")
        print(f"  last crawl: {up['last_crawl_time'] or '-'}")
    print(f"total: {s['total']}")
    for status, count in s["counts"].items():
        print(f"  {status:<12} {count}")
    return 0


def _cmd_check(app: App, mid: Optional[int]) -> int:
    result = app.check_files(mid)
    print(f"checked {result['checked']} DOWNLOADED video(s).")
    if result["missing"]:
        print(f"MISSING {len(result['missing'])} -> reset to PENDING:")
        for bvid in result["missing"]:
            print(f"  {bvid}")
    else:
        print("no missing files.")
    return 0


def _cmd_preview(app: App, mid: Optional[int], options: DownloadOptions, explain_bvid: Optional[str]) -> int:
    result = app.preview(mid, options)

    if explain_bvid:
        for video, decision in result["decisions"]:
            if video.bvid == explain_bvid:
                print(explain(decision, video, result["min_duration"], result["max_duration"]))
                return 0
        print(f"video not found: {explain_bvid}")
        return 1

    stats = result["stats"]
    filtered = Counter()
    for _, d in result["decisions"]:
        if d.decision == "FILTERED":
            if d.reason.startswith("blacklist"):
                filtered["BLACKLIST"] += 1
            else:
                filtered["DURATION"] += 1

    print(f"READY       {stats.get('READY', 0)}")
    print(f"DOWNLOADED  {stats.get('DOWNLOADED', 0)}")
    print(f"MISSING     {stats.get('MISSING', 0)}")
    print(f"DURATION    {filtered.get('DURATION', 0)}")
    print(f"BLACKLIST   {filtered.get('BLACKLIST', 0)}")
    print(f"FAILED      {stats.get('FAILED', 0)}")
    print(f"DOWNLOADING {stats.get('DOWNLOADING', 0)}")
    return 0


def _cmd_download_bv(app: App, bvids: List[str], options: DownloadOptions) -> int:
    if app.has_ffmpeg is False:
        print("[warn] ffmpeg not found; DASH streams will fall back to progressive.")
    print(f"Downloading {len(bvids)} video(s) by bvid (bypassing UP rules)...")
    results = app.download_bv(bvids, options)
    ok = 0
    for bvid, success, msg in results:
        if success:
            ok += 1
            print(f"  OK   {bvid} -> {msg}")
        else:
            print(f"  FAIL {bvid} -> {msg}")
    print(f"done: {ok}/{len(results)} succeeded.")
    return 0 if ok == len(results) else 1


def _cmd_blacklist(app: App, action: str, mid: int, keyword: Optional[str]) -> int:
    if action == "add":
        ok = app.add_blacklist(mid, keyword)
        print(f"{'added' if ok else 'already exists'}: {keyword} (mid={mid})")
        return 0
    if action == "remove":
        ok = app.remove_blacklist(mid, keyword)
        print(f"{'removed' if ok else 'not found'}: {keyword} (mid={mid})")
        return 0
    if action == "list":
        keywords = app.list_blacklist(mid)
        if not keywords:
            print("(empty)")
        for kw in keywords:
            print(kw)
        return 0
    return 1


def _cmd_limit(app: App, mbps: Optional[float]) -> int:
    if mbps is None:
        print(f"current limit: {app.limiter.rate / (1024 * 1024):.1f} MB/s")
        return 0
    if mbps <= 0:
        print("limit must be > 0")
        return 1
    app.set_limit(mbps)
    print(f"download speed limit set to {mbps} MB/s")
    return 0


def _cmd_run(app: App, mid: Optional[int], once: bool) -> int:
    app.state.set_scan("", "运行中...")
    if once:
        app.download_manager.start()
        try:
            stats = app.scan(mid)
            print(
                f"scan done: new={stats.new} existing={stats.existing} "
                f"eligible={stats.eligible} filtered={stats.filtered}"
            )
            _download_once(app, mid, DownloadOptions())
        finally:
            app.download_manager.stop()
        return 0

    thread = threading.Thread(target=app.scheduler.run, kwargs={"once": False}, daemon=True)
    thread.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.state.request_stop()
    app.scheduler.stop()
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)

    app = App(config_path=args.config)

    try:
        if args.command == "login":
            return _cmd_login(app)
        if args.command == "add":
            return _cmd_add(app, args.mid)
        if args.command == "remove":
            return _cmd_remove(app, args.mid)
        if args.command == "list":
            return _cmd_list(app)
        if args.command == "scan":
            return _cmd_scan(app, args.mid)
        if args.command == "download":
            opts = DownloadOptions(
                quality=args.quality,
                media_type=args.media_type,
                min_duration=args.min_duration,
                max_duration=args.max_duration,
            )
            return _cmd_download(app, args.mid, opts)
        if args.command == "download-bv":
            opts = DownloadOptions(quality=args.quality, media_type=args.media_type)
            return _cmd_download_bv(app, args.bvids, opts)
        if args.command == "retry":
            return _cmd_retry(app, args.mid)
        if args.command == "status":
            return _cmd_status(app, args.mid)
        if args.command == "check":
            return _cmd_check(app, args.mid)
        if args.command == "preview":
            opts = DownloadOptions(
                quality=args.quality,
                media_type=args.media_type,
                min_duration=args.min_duration,
                max_duration=args.max_duration,
            )
            return _cmd_preview(app, args.mid, opts, args.explain)
        if args.command == "blacklist":
            return _cmd_blacklist(app, args.bl_action, args.mid, getattr(args, "keyword", None))
        if args.command == "limit":
            return _cmd_limit(app, args.mbps)
        if args.command == "run":
            return _cmd_run(app, args.mid, args.once)

        # Default: launch the btop-style TUI.
        from .tui import run_tui

        return run_tui(app)
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
