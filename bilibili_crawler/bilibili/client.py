"""Low-level Bilibili HTTP client with WBI signing, retries and cookies.

Bilibili's web endpoints (space, player playurl, ...) require a WBI signature
that is derived from ``img_key`` / ``sub_key`` published by ``/x/web-interface/nav``.
This module implements that signing scheme and wraps ``requests.Session`` with:

* a browser-like User-Agent,
* real buvid3/buvid4 + b_nut cookies (obtained from the spi endpoint),
* per-request timeout + exponential-backoff retries,
* a cookie jar that can be persisted / restored for login sessions.

All network access is treated as unreliable (plan principle #5): callers get a
typed ``BilibiliError`` and the retry logic never raises a bare exception for
transient failures.
"""
from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class BilibiliError(Exception):
    """Raised for any Bilibili API / network failure."""


class BilibiliApiError(BilibiliError):
    """Raised when Bilibili returns a non-zero ``code``."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Bilibili API error code={code}: {message}")


# ---------------------------------------------------------------------------
# WBI signing
# ---------------------------------------------------------------------------
# Official mixin-key permutation table (Bilibili web frontend).
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _extract_key(url: str) -> str:
    """Extract the 32-char key from a wbi_img URL (filename w/o extension)."""
    name = url.rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


# Anti-crawl (risk-control) parameters required by Bilibili's WBI space
# endpoints. These mirror the values the web frontend sends and greatly reduce
# the chance of -352 / 412 responses.
ANTICRAWL_PARAMS: Dict[str, str] = {
    "web_location": "1550101",
    "dm_img_list": "[]",
    "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
    "dm_cover_img_str": (
        "QU5HTEUgKEludGVsLCBJbnRlbChSKSBVSEQgR3JhcGhpY3MgNjMwICgwLjAuMSks"
        "IENocm9taXVtIEdyYXBoaWNzIFdpbmRvd3MoMC4wLjEpKQ"
    ),
    "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
}


class BilibiliClient:
    BASE = "https://api.bilibili.com"
    PASSPORT = "https://passport.bilibili.com"

    def __init__(
        self,
        user_agent: str,
        referer: str = "https://www.bilibili.com",
        timeout: int = 15,
        retries: int = 3,
        retry_backoff: float = 1.0,
    ):
        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = retry_backoff
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Referer": referer,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self._wbi_keys: Optional[tuple[str, str]] = None
        self._wbi_fetch_time = 0.0
        self._cookies_ready = False

    # ------------------------------------------------------------ requests --
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json,
                    timeout=self.timeout,
                    allow_redirects=allow_redirects,
                )
                if resp.status_code == 412:
                    # Risk control (deep pagination on space endpoints). Use a
                    # longer backoff so the scan can push further into history.
                    raise BilibiliError("HTTP 412 (risk control)")
                if resp.status_code >= 500:
                    raise BilibiliError(f"HTTP {resp.status_code} (server error)")
                return resp
            except (requests.RequestException, BilibiliError) as exc:  # noqa: PERF203
                last_exc = exc
                if attempt < self.retries - 1:
                    if isinstance(exc, BilibiliError) and "412" in str(exc):
                        time.sleep(5 * (attempt + 1))  # 5s / 10s / 15s
                    else:
                        time.sleep(self.retry_backoff * (2 ** attempt))
        raise BilibiliError(
            f"request failed after {self.retries} attempts: {last_exc}"
        )

    # ---------------------------------------------------------------- JSON --
    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        wbi: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """GET a JSON API and return its ``data`` payload.

        Transient failures — connection errors, empty/non-JSON bodies (risk
        control returns an empty 412/200 body), and risk-control codes
        (-352 / -799) — are retried with backoff; on risk-control the buvid
        cookies are refreshed before retrying. All failures surface as
        :class:`BilibiliError` so callers never see raw ``requests`` errors.
        """
        last_exc: Optional[Exception] = None
        attempts = max(1, self.retries)
        for attempt in range(attempts):
            try:
                if wbi:
                    self._ensure_cookies()
                    signed = self._sign_wbi(params or {})
                else:
                    signed = params
                resp = self.request("GET", url, params=signed, headers=headers)
                try:
                    data = resp.json()
                except requests.JSONDecodeError as exc:
                    raise BilibiliError(
                        f"non-JSON response (HTTP {resp.status_code}) from {url}"
                    ) from exc
            except BilibiliError as exc:
                last_exc = exc
                if "412" in str(exc):
                    # Deep-pagination risk control: refresh device cookies and
                    # the WBI keys, then back off longer before retrying.
                    self._cookies_ready = False
                    self._wbi_keys = None
                    time.sleep(5 * (attempt + 1))
                else:
                    time.sleep(self.retry_backoff * (2 ** attempt))
                continue

            code = data.get("code", -1)
            if code in (-352, -799) and attempt < attempts - 1:
                # Risk control / rate limit: refresh device cookies and retry.
                self._cookies_ready = False
                self._wbi_keys = None
                time.sleep(self.retry_backoff * (2 ** (attempt + 1)))
                continue
            if code != 0:
                hint = ""
                if code in (-352, -799):
                    hint = " (Bilibili risk control; try `login` first)"
                raise BilibiliApiError(code, data.get("message", "unknown error") + hint)
            return data.get("data")
        raise BilibiliError(
            f"request failed after {attempts} attempts: {last_exc}"
        )

    # -------------------------------------------------------------- cookies --
    def _ensure_cookies(self) -> None:
        """Obtain real buvid3/buvid4 + b_nut cookies once per session.

        These are required by Bilibili's risk-control layer; a hand-rolled
        buvid value is rejected with -352.
        """
        if self._cookies_ready:
            return
        try:
            resp = self.request("GET", f"{self.BASE}/x/frontend/finger/spi")
            data = resp.json().get("data") or {}
            b3 = data.get("b_3")
            b4 = data.get("b_4")
            if b3:
                self.session.cookies.set("buvid3", b3, domain=".bilibili.com")
            if b4:
                self.session.cookies.set("buvid4", b4, domain=".bilibili.com")
        except (BilibiliError, ValueError):
            pass
        try:
            # The homepage plants b_nut, which the space endpoints expect.
            self.request("GET", "https://www.bilibili.com/")
        except BilibiliError:
            pass
        self._cookies_ready = True

    @staticmethod
    def anticrawl_params() -> Dict[str, str]:
        """Return the anti-crawl params to merge into WBI space requests."""
        return dict(ANTICRAWL_PARAMS)

    # ------------------------------------------------------------------ WBI --
    def _refresh_wbi_keys(self) -> tuple[str, str]:
        """Fetch and cache img_key / sub_key from the nav endpoint."""
        try:
            resp = self.request("GET", f"{self.BASE}/x/web-interface/nav")
            data = resp.json()
            wbi = data.get("data", {}).get("wbi_img", {})
            img = wbi.get("img_url", "")
            sub = wbi.get("sub_url", "")
            if not img or not sub:
                raise BilibiliError("nav endpoint did not expose wbi_img keys")
            self._wbi_keys = (_extract_key(img), _extract_key(sub))
            self._wbi_fetch_time = time.time()
        except Exception as exc:  # noqa: BLE001
            raise BilibiliError(f"failed to fetch WBI keys: {exc}") from exc
        return self._wbi_keys

    def _sign_wbi(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Refresh keys if missing or older than 1 hour.
        if self._wbi_keys is None or (time.time() - self._wbi_fetch_time) > 3600:
            self._refresh_wbi_keys()
        img_key, sub_key = self._wbi_keys
        mixin = _mixin_key(img_key, sub_key)

        clean: Dict[str, str] = {}
        for k, v in params.items():
            if v is None:
                continue
            # The search endpoint historically chokes on some characters.
            clean[k] = re_safe(str(v))

        clean["wts"] = str(int(time.time()))
        query = urllib.parse.urlencode(
            sorted(clean.items())
        )
        w_rid = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
        clean["w_rid"] = w_rid
        return clean


def re_safe(value: str) -> str:
    """Strip characters known to break Bilibili query signing."""
    return (
        value.replace("!", "")
        .replace("'", "")
        .replace("(", "")
        .replace(")", "")
        .replace("*", "")
    )
