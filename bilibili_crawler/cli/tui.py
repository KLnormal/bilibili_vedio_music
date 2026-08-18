"""btop-style terminal dashboard (plan section 11).

A ``rich`` live view refreshes several stacked panels in real time:

* UP LIST       — every UP with its video count (current one highlighted).
* CURRENT TASK  — scan phase + new/existing/filtered counters.
* DOWNLOAD      — active download progress + speed vs. the rate limit.
* LOG           — rolling recent events.

Keyboard controls (no mouse, btop-like):

    q  Quit            p  Pause/resume     r  Scan now
    l  Set limit       (type MB/s + Enter, e.g. ``l 20`` or ``l 80``)

The scheduler runs in a background thread; the dashboard merely renders the
shared :class:`RuntimeState`, so the core system also runs headless.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from .. import __version__
from ..app import App
from ..options import quality_name
from .keyreader import KeyReader

HELP_TEXT = " q Quit    p Pause    r Scan now    l Limit"


def _up_table(snapshot) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("cursor", width=2)
    table.add_column("name", ratio=3, overflow="fold")
    table.add_column("mid", width=10)
    table.add_column("videos", justify="right", width=8)
    table.add_column("last_crawl", width=21)
    for name, mid, count, enabled, last_crawl in snapshot.ups:
        cursor = ">" if name == snapshot.current_up else " "
        style = "" if enabled else "dim"
        table.add_row(
            cursor,
            Text(name or str(mid), style=style),
            str(mid),
            str(count),
            last_crawl or "-",
        )
    if not snapshot.ups:
        table.add_row("", "(no UP added — use `add <mid>` in CLI)", "", "", "")
    return table


def _task_panel(snapshot) -> Panel:
    lines = []
    if snapshot.current_up:
        lines.append(Text(f"UP: {snapshot.current_up}", style="bold cyan"))
    else:
        lines.append(Text("UP: -", style="dim"))
    lines.append(Text(f"Status: {snapshot.scan_status or 'idle'}", style="yellow"))
    lines.append(Text(""))
    lines.append(
        Text(
            f"New: {snapshot.new_count}    Existing: {snapshot.existing_count}    "
            f"Filtered: {snapshot.filtered_count}"
        )
    )
    lines.append(
        Text(
            f"Downloaded: {snapshot.downloaded_count}    Failed: {snapshot.failed_count}    "
            f"Pending: {snapshot.pending_count}"
        )
    )
    return Panel(Group(*lines), title="CURRENT TASK", border_style="blue")


def _download_panel(snapshot) -> Panel:
    p = snapshot.progress
    if p.status == "starting" or (p.bvid and not p.downloaded):
        title = f"Downloading {p.bvid}"
        label = f"{p.title[:40]}..."
    elif p.bvid:
        title = "DOWNLOAD"
        label = f"{p.title[:40]} [BV{p.bvid}]"
    else:
        title = "DOWNLOAD"
        label = "(waiting for task)"

    body = [Text(label, style="bold")]
    if p.bvid:
        percent = p.percent
        if percent is not None:
            body.append(
                ProgressBar(
                    total=100, completed=percent, width=50,
                    complete_style="green", finished_style="green",
                )
            )
            body.append(Text(f"{percent:5.1f}%", style="green"))
        else:
            body.append(Text("starting...", style="yellow"))
        speed = p.speed or "0.0 MB/s"
        body.append(
            Text(
                f"{speed} / {snapshot.rate_mbps:.0f} MB/s   "
                f"({_fmt_bytes(p.downloaded)} / {_fmt_bytes(p.total)})"
            )
        )
    else:
        body.append(Text("no active download", style="dim"))

    return Panel(Group(*body), title=title, border_style="magenta")


def _log_panel(snapshot) -> Panel:
    lines = snapshot.logs[-8:] or ["..."]
    body = Group(*[Text(ln, style="dim") for ln in lines])
    return Panel(body, title="LOG", border_style="bright_black")


def _fmt_bytes(n: int) -> str:
    if n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def _header(app: App, snapshot) -> Text:
    login = "logged-in" if app.login.is_logged_in else "anonymous"
    dash = "dash+ffmpeg" if app.has_ffmpeg else "progressive"
    paused = " [PAUSED]" if snapshot.paused else ""
    quality = quality_name(app.downloader.qn)
    return Text(
        f"BILIBILI CRAWLER  v{__version__}  [{login}]  [{dash}]  "
        f"quality {quality}  limit {snapshot.rate_mbps:.0f} MB/s{paused}",
        style="bold white on dark_blue",
    )


def _render(app: App, snapshot, limit_buffer: str, limit_mode: bool) -> Group:
    footer = HELP_TEXT
    if limit_mode:
        footer = f" enter new limit (MB/s): {limit_buffer}_   Enter=apply Esc=cancel"
    panels = [
        _header(app, snapshot),
        _up_table(snapshot),
        _task_panel(snapshot),
        _download_panel(snapshot),
        _log_panel(snapshot),
        Panel(Text(footer, style="bold"), border_style="green", padding=(0, 1)),
    ]
    return Group(*panels)


def _apply_key(app: App, key: str, limit_buffer: list, limit_mode: list) -> bool:
    """Handle one key. Returns True to keep running, False to quit."""
    if limit_mode[0]:
        if key == "\x1b":  # Esc cancels
            limit_mode[0] = False
            limit_buffer[0] = ""
            return True
        if key in ("\r", "\n"):
            value = limit_buffer[0].strip().rstrip("M").rstrip("m")
            limit_mode[0] = False
            limit_buffer[0] = ""
            if value:
                try:
                    mbps = float(value)
                    if mbps > 0:
                        app.set_limit(mbps)
                except ValueError:
                    app.state.log(f"无效的限速值: {value}")
            return True
        if key in ("\x08", "\x7f"):  # Backspace
            limit_buffer[0] = limit_buffer[0][:-1]
            return True
        if key.isdigit() or key == ".":
            limit_buffer[0] += key
            return True
        return True

    if key in ("q", "Q"):
        return False
    if key in ("p", "P"):
        app.state.set_paused(not app.state.paused)
        app.state.log("已暂停" if app.state.paused else "已恢复")
        return True
    if key in ("r", "R"):
        _trigger_scan(app)
        return True
    if key in ("l", "L"):
        limit_mode[0] = True
        limit_buffer[0] = ""
        return True
    return True


def _trigger_scan(app: App) -> None:
    def work() -> None:
        app.state.log("手动扫描触发...")
        try:
            app.scan()
        except Exception as exc:  # noqa: BLE001
            app.state.log(f"扫描异常: {exc}")

    threading.Thread(target=work, name="manual-scan", daemon=True).start()


def run_tui(app: App) -> int:
    """Launch the live dashboard. Blocks until the user quits."""
    console = Console()
    reader = KeyReader()
    limit_buffer = [""]
    limit_mode = [False]

    # Start the scan + download loop in the background.
    scheduler_thread = threading.Thread(
        target=app.scheduler.run, kwargs={"once": False}, name="scheduler", daemon=True
    )
    scheduler_thread.start()

    interval = float(app.config["tui"].get("refresh_interval", 0.5))
    code = 0
    try:
        with Live(
            _render(app, app.state.snapshot(), limit_buffer[0], limit_mode[0]),
            console=console,
            screen=True,
            auto_refresh=False,
        ) as live:
            while not app.state.stopped:
                live.update(
                    _render(app, app.state.snapshot(), limit_buffer[0], limit_mode[0]),
                    refresh=True,
                )
                key = reader.read(timeout=interval)
                if key is not None:
                    if not _apply_key(app, key, limit_buffer, limit_mode):
                        break
            app.state.request_stop()
    except KeyboardInterrupt:
        code = 0
    finally:
        reader.restore()
        app.state.request_stop()
        app.scheduler.stop()

    console.print("[green]Bye.[/green]")
    return code
