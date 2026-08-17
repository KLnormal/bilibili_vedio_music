"""Bilibili authentication (Interactive Login Manager).

Design constraints from the plan (section 9):

* Username / password are **never** persisted anywhere. Only the resulting
  session cookies (SESSDATA etc.) are saved, and only to a gitignored file.
* If the login flow hits a captcha / slider / secondary verification, the
  program must **pause** and let a human take over — it must never attempt to
  bypass the challenge.

For a terminal-first v0.1 the practical, fully-working path is **QR-code
login**: the program prints a QR code, the human scans it with the Bilibili
mobile app (which performs the account + captcha steps on the phone), and the
program polls for completion. A manual cookie paste path is also provided for
air-gapped / headless setups.

The ``LoginManager`` orchestrates the "automatic flow ⇄ human intervention"
switch and is the single place that owns the persisted cookie file.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional

import qrcode
from rich.console import Console

from .client import BilibiliApiError, BilibiliClient, BilibiliError

# Cookies that constitute a usable Bilibili web login session.
SESSION_COOKIE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")


class LoginManager:
    def __init__(self, client: BilibiliClient, cookie_file: str | Path, console: Optional[Console] = None):
        self.client = client
        self.cookie_file = Path(cookie_file)
        self.console = console or Console()

    # ------------------------------------------------------------- status --
    @property
    def is_logged_in(self) -> bool:
        """True when the in-memory session carries a usable SESSDATA cookie."""
        return bool(self._get_cookie("SESSDATA"))

    # -------------------------------------------------------------- cookies --
    def _get_cookie(self, name: str) -> Optional[str]:
        """Read a cookie value without raising on duplicate names.

        ``requests`` raises ``CookieConflictError`` when the jar holds the same
        cookie name on several domains (which Bilibili's QR-login redirect chain
        does). We pick the copy with the most specific (longest) domain instead.
        """
        copies = [
            c
            for c in self.client.session.cookies
            if c.name == name and c.value
        ]
        if not copies:
            return None
        copies.sort(key=lambda c: len(c.domain or ""), reverse=True)
        return copies[0].value

    def _normalize_session_cookies(self) -> None:
        """Dedupe session cookies, keeping one canonical copy per name.

        After the QR-login finalize redirect, SESSDATA / bili_jct / DedeUserID
        can be planted on several domains. Collapse them onto ``.bilibili.com``
        so later requests send exactly one value and reads never conflict.
        """
        for name in SESSION_COOKIE_KEYS:
            value = self._get_cookie(name)
            if not value:
                continue
            # ``del jar[name]`` removes *every* copy of that name.
            try:
                del self.client.session.cookies[name]
            except KeyError:
                pass
            self.client.session.cookies.set(name, value, domain=".bilibili.com", path="/")

    # ---------------------------------------------------------- persistence --
    def save_cookies(self) -> None:
        """Persist session cookies to the cookie file (never to the database)."""
        self._normalize_session_cookies()
        cookies = {
            name: self._get_cookie(name)
            for name in SESSION_COOKIE_KEYS
            if self._get_cookie(name)
        }
        if not cookies:
            return
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookie_file.write_text(json.dumps(cookies, indent=2), encoding="utf-8")

    def load_cookies(self) -> bool:
        """Restore persisted cookies into the session. Returns True if any exist."""
        if not self.cookie_file.is_file():
            return False
        try:
            cookies = json.loads(self.cookie_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        for name, value in cookies.items():
            if value:
                self.client.session.cookies.set(name, value, domain=".bilibili.com")
        return bool(self._get_cookie("SESSDATA"))

    def clear_cookies(self) -> None:
        for name in SESSION_COOKIE_KEYS:
            try:
                del self.client.session.cookies[name]
            except KeyError:
                pass
        if self.cookie_file.is_file():
            self.cookie_file.unlink(missing_ok=True)

    # ----------------------------------------------------------- login flow --
    def login(self) -> bool:
        """Run the interactive login flow. Returns True on success."""
        self.console.print("[bold cyan]Bilibili 登录[/bold cyan]")
        self.console.print("凭证仅用于本次登录，程序只保存会话 Cookie（不保存账号密码）。")

        choice = self._prompt_choice()
        if choice == "qr":
            return self._login_qrcode()
        if choice == "cookie":
            return self._login_cookie()
        return False

    def _prompt_choice(self) -> str:
        while True:
            self.console.print("[1] 扫码登录（推荐，验证码在手机上完成）")
            self.console.print("[2] 手动粘贴 Cookie（SESSDATA 等）")
            raw = self._input("[bold]请选择 (1/2): [/bold]").strip()
            if raw in ("1", "qr"):
                return "qr"
            if raw in ("2", "cookie"):
                return "cookie"

    # ------------------------------------------------------------ QR login --
    def _login_qrcode(self) -> bool:
        try:
            data = self.client.get_json(
                f"{BilibiliClient.PASSPORT}/x/passport-login/web/qrcode/generate"
            )
        except BilibiliError as exc:
            self.console.print(f"[red]无法获取登录二维码：{exc}[/red]")
            return False

        qrcode_key = data.get("qrcode_key")
        qr_url = data.get("url")
        if not qrcode_key or not qr_url:
            self.console.print("[red]登录二维码响应缺失字段。[/red]")
            return False

        self._print_qrcode(qr_url)
        self.console.print("请使用 Bilibili 手机 App 扫码并确认登录。")
        self.console.print("[dim]若终端无法显示二维码，请直接打开链接：[/dim]" + qr_url)

        # Poll for the scan result. This is the "human intervention" pause:
        # the automated flow blocks until the user finishes verification.
        try:
            return self._poll_qrcode(qrcode_key)
        except BilibiliError as exc:
            self.console.print(f"[red]登录失败：{exc}[/red]")
            return False

    def _poll_qrcode(self, qrcode_key: str) -> bool:
        params = {"qrcode_key": qrcode_key}
        deadline = time.time() + 180
        last_state = None
        while time.time() < deadline:
            resp = self.client.request(
                "GET",
                f"{BilibiliClient.PASSPORT}/x/passport-login/web/qrcode/poll",
                params=params,
            )
            data = resp.json()
            code = data.get("code", -1)
            message = data.get("message", "")

            if code == 0:
                # Finalize the session by visiting the returned redirect URL,
                # which plants SESSDATA / bili_jct / DedeUserID cookies.
                url = data.get("data", {}).get("url")
                if url:
                    try:
                        self.client.request(
                            "GET", url, allow_redirects=True
                        )
                    except BilibiliError:
                        pass
                if self.is_logged_in:
                    self.save_cookies()
                    self.console.print("[green]登录成功。[/green]")
                    return True
                # Some flows need a second poll after confirm; give it one more shot.
                time.sleep(1)

            if code == 86038:
                self.console.print("[yellow]二维码已过期，请重新登录。[/yellow]")
                return False
            if code == 86090 and last_state != "scanned":
                self.console.print("[cyan]已扫码，请在手机上确认登录...[/cyan]")
                last_state = "scanned"

            time.sleep(1.5)

        self.console.print("[yellow]登录超时。[/yellow]")
        return False

    def _print_qrcode(self, url: str) -> None:
        """Render a QR code using unicode block characters (no PIL needed)."""
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        for row in matrix:
            line = "".join("██" if cell else "  " for cell in row)
            self.console.print(line)

    # --------------------------------------------------------- cookie login --
    def _login_cookie(self) -> bool:
        self.console.print(
            "[dim]从浏览器开发者工具（F12 → Application → Cookies）复制以下值：[/dim]"
        )
        sessdata = self._input("[bold]SESSDATA: [/bold]").strip()
        if not sessdata:
            self.console.print("[red]SESSDATA 不能为空。[/red]")
            return False
        bili_jct = self._input("[bold]bili_jct (可留空): [/bold]").strip()
        dedeuserid = self._input("[bold]DedeUserID (可留空): [/bold]").strip()

        self.client.session.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")
        if bili_jct:
            self.client.session.cookies.set("bili_jct", bili_jct, domain=".bilibili.com")
        if dedeuserid:
            self.client.session.cookies.set("DedeUserID", dedeuserid, domain=".bilibili.com")

        if self.verify_login():
            self.save_cookies()
            self.console.print("[green]登录成功。[/green]")
            return True
        self.console.print("[red]Cookie 无效，登录失败。[/red]")
        self.clear_cookies()
        return False

    # ------------------------------------------------------------ verification --
    def verify_login(self) -> bool:
        """Check whether the current session is logged in via the nav API."""
        try:
            resp = self.client.request("GET", f"{BilibiliClient.BASE}/x/web-interface/nav")
            data = resp.json()
        except BilibiliError:
            return False
        return bool(data.get("data", {}).get("isLogin", False))

    # ------------------------------------------------------------------ util --
    def _input(self, prompt: str) -> str:
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return ""
