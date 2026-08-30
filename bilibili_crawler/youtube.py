"""Small, isolated YouTube channel scanner/downloader.

The YouTube implementation deliberately lives beside (rather than inside) the
Bilibili repository.  Its database is independent, while the public methods
mirror the subset used by the CLI and desktop controller.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .download_directory import ensure_writable_root, normalize_download_root
from .downloader.downloader import sanitize_filename

YOUTUBE_ID_RE = re.compile(r"^UC[a-zA-Z0-9_-]{20,}$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def identify_channel(value: str) -> tuple[str, str]:
    """Return ``(kind, canonical_value)`` or raise ValueError.

    Numeric values remain Bilibili UIDs; YouTube input is limited to channel
    IDs, channel URLs and @handles.  Video and playlist URLs are rejected.
    """
    raw = str(value).strip()
    if not raw:
        raise ValueError("UP 标识不能为空")
    if raw.isdigit():
        return "bilibili", raw
    if raw.startswith("@") and len(raw) > 1:
        return "youtube", raw
    if YOUTUBE_ID_RE.fullmatch(raw):
        return "youtube", raw
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        raise ValueError("无法识别 UP 标识：请输入 Bilibili UID、YouTube 频道 URL、@账号或 UC ID")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "channel" and YOUTUBE_ID_RE.fullmatch(parts[1]):
        return "youtube", parts[1]
    if parts and parts[0].startswith("@"):
        return "youtube", parts[0]
    raise ValueError("YouTube 仅支持频道 URL、@账号或 UC ID，不支持视频/播放列表链接")


@dataclass
class YouTubeChannel:
    channel_id: str
    name: str = ""
    description: str = ""
    url: str = ""
    enabled: bool = True
    last_scan: Optional[str] = None

    @property
    def mid(self) -> str: return self.channel_id

    @property
    def last_crawl_time(self) -> Optional[str]: return self.last_scan


@dataclass
class YouTubeVideo:
    video_id: str
    channel_id: str
    title: str = ""
    description: str = ""
    duration: Optional[int] = None
    created: Optional[int] = None
    url: str = ""
    media_type: str = "video"
    status: str = "PENDING"
    download_path: str = ""
    error: str = ""
    filter_reason: str = ""

    @property
    def bvid(self) -> str: return self.video_id

    @property
    def download_error(self) -> str: return self.error

    @property
    def download_status(self):
        from .database.models import DownloadStatus
        return DownloadStatus(self.status)


SCHEMA = """
CREATE TABLE IF NOT EXISTS channel (
 channel_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
 url TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, last_scan TEXT
);
CREATE TABLE IF NOT EXISTS video (
 video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', duration INTEGER, created INTEGER, url TEXT NOT NULL DEFAULT '',
 FOREIGN KEY(channel_id) REFERENCES channel(channel_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS media (
 video_id TEXT NOT NULL, media_type TEXT NOT NULL CHECK(media_type IN ('video','audio')),
 status TEXT NOT NULL DEFAULT 'PENDING', download_path TEXT NOT NULL DEFAULT '',
 download_time TEXT, error TEXT NOT NULL DEFAULT '', filter_reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(video_id, media_type), FOREIGN KEY(video_id) REFERENCES video(video_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS blacklist (channel_id TEXT NOT NULL, keyword TEXT NOT NULL,
 UNIQUE(channel_id, keyword), FOREIGN KEY(channel_id) REFERENCES channel(channel_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS allowlist (channel_id TEXT NOT NULL, keyword TEXT NOT NULL,
 UNIQUE(channel_id, keyword), FOREIGN KEY(channel_id) REFERENCES channel(channel_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS filter_settings (channel_id TEXT PRIMARY KEY, min_duration INTEGER,
 max_duration INTEGER, min_date TEXT, max_date TEXT, FOREIGN KEY(channel_id) REFERENCES channel(channel_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
"""


class YouTubeService:
    def __init__(self, db_path: str | Path, save_root: str | Path, *, ffmpeg_path: str = "", min_duration: int = 0, max_duration: int = 0):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.save_root = normalize_download_root(save_root) / "YouTube"
        ensure_writable_root(self.save_root)
        self.ffmpeg_path = ffmpeg_path
        self.min_duration = min_duration
        self.max_duration = max_duration
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    @property
    def db(self):
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def _ydl(self):
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("YouTube 功能需要 yt-dlp，请先执行 pip install yt-dlp") from exc
        return yt_dlp

    @staticmethod
    def _channel_url(identifier: str) -> str:
        return (f"https://www.youtube.com/channel/{identifier}/videos" if identifier.startswith("UC")
                else f"https://www.youtube.com/{identifier}/videos")

    def add_channel(self, identifier: str) -> YouTubeChannel:
        kind, value = identify_channel(identifier)
        if kind != "youtube":
            raise ValueError("该标识是 Bilibili UID，不属于 YouTube")
        info = self._ydl().YoutubeDL({"quiet": True, "skip_download": True, "extract_flat": True}).extract_info(self._channel_url(value), download=False)
        channel_id = str(info.get("channel_id") or info.get("uploader_id") or value)
        channel = YouTubeChannel(channel_id, info.get("channel") or info.get("uploader") or value,
                                 info.get("description") or "", info.get("channel_url") or self._channel_url(value))
        with self._lock:
            self.db.execute("INSERT INTO channel(channel_id,name,description,url) VALUES(?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET name=excluded.name,description=excluded.description,url=excluded.url",
                            (channel.channel_id, channel.name, channel.description, channel.url))
            self.db.commit()
        return channel

    def list_channels(self):
        with self._lock:
            return [YouTubeChannel(r["channel_id"], r["name"], r["description"], r["url"], bool(r["enabled"]), r["last_scan"])
                    for r in self.db.execute("SELECT * FROM channel ORDER BY name, channel_id")]

    def remove_channel(self, channel_id: str) -> bool:
        with self._lock:
            cur = self.db.execute("DELETE FROM channel WHERE channel_id=?", (channel_id,)); self.db.commit(); return cur.rowcount > 0

    def scan(self, channel_id: str) -> dict[str, int]:
        channel = next((c for c in self.list_channels() if c.channel_id == channel_id), None)
        if not channel:
            raise ValueError(f"YouTube 频道不存在：{channel_id}")
        ydl_opts = {"quiet": True, "skip_download": True, "extract_flat": True, "ignoreerrors": True}
        info = self._ydl().YoutubeDL(ydl_opts).extract_info(channel.url or self._channel_url(channel_id), download=False)
        entries = info.get("entries") or []
        new = 0
        with self._lock:
            for item in entries:
                if not item or not item.get("id") or not VIDEO_ID_RE.fullmatch(str(item["id"])):
                    continue
                vid = str(item["id"]); exists = self.db.execute("SELECT 1 FROM video WHERE video_id=?", (vid,)).fetchone()
                self.db.execute("INSERT INTO video(video_id,channel_id,title,duration,created,url) VALUES(?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET title=excluded.title,duration=excluded.duration,created=excluded.created,url=excluded.url",
                                 (vid, channel_id, item.get("title") or vid, item.get("duration"), self._created(item), item.get("webpage_url") or f"https://youtu.be/{vid}"))
                self.db.execute("INSERT OR IGNORE INTO media(video_id,media_type) VALUES(?, 'video'), (?, 'audio')", (vid, vid))
                new += not bool(exists)
            self.db.execute("UPDATE channel SET last_scan=? WHERE channel_id=?", (datetime.utcnow().isoformat(), channel_id)); self.db.commit()
        return {"new": new, "existing": max(0, len(entries) - new)}

    @staticmethod
    def _created(item: dict[str, Any]) -> Optional[int]:
        value = item.get("timestamp")
        if value: return int(value)
        date = item.get("upload_date")
        if date and len(str(date)) == 8:
            try: return int(datetime.strptime(str(date), "%Y%m%d").timestamp())
            except ValueError: pass
        return None

    def _rows(self, channel_id: Optional[str], media_type: str):
        query = "SELECT v.*,m.status,m.download_path,m.error,m.filter_reason FROM video v JOIN media m ON m.video_id=v.video_id AND m.media_type=?"
        args: list[Any] = [media_type]
        if channel_id: query += " WHERE v.channel_id=?"; args.append(channel_id)
        return self.db.execute(query + " ORDER BY COALESCE(v.created,0) DESC", args).fetchall()

    def list_videos(self, channel_id: Optional[str] = None, media_type: str = "video") -> list[YouTubeVideo]:
        with self._lock:
            return [YouTubeVideo(r["video_id"], r["channel_id"], r["title"], r["description"], r["duration"], r["created"], r["url"], media_type, r["status"], r["download_path"], r["error"], r["filter_reason"]) for r in self._rows(channel_id, media_type)]

    def status(self, channel_id: Optional[str] = None, media_type: str = "video") -> dict:
        counts = {s: 0 for s in ("PENDING", "DOWNLOADING", "DOWNLOADED", "FAILED", "FILTERED")}
        for v in self.list_videos(channel_id, media_type): counts[v.status] = counts.get(v.status, 0) + 1
        return {"total": sum(counts.values()), "counts": counts}

    def reset_failed(self, channel_id: Optional[str] = None, media_type: str = "video") -> int:
        with self._lock:
            if channel_id:
                cur = self.db.execute("UPDATE media SET status='PENDING',error='' WHERE media_type=? AND status='FAILED' AND video_id IN (SELECT video_id FROM video WHERE channel_id=?)", (media_type, channel_id))
            else:
                cur = self.db.execute("UPDATE media SET status='PENDING',error='' WHERE media_type=? AND status='FAILED'", (media_type,))
            self.db.commit(); return cur.rowcount

    def check_files(self, channel_id: Optional[str] = None, media_type: str = "video") -> dict:
        checked = missing = 0
        for video in self.list_videos(channel_id, media_type):
            if video.status != "DOWNLOADED": continue
            checked += 1
            try: valid = Path(video.download_path).is_file() and Path(video.download_path).stat().st_size > 0 and Path(video.download_path).is_relative_to(self.save_root)
            except OSError: valid = False
            if not valid:
                self.db.execute("UPDATE media SET status='PENDING',download_path='',download_time=NULL WHERE video_id=? AND media_type=?", (video.video_id, media_type)); missing += 1
        self.db.commit(); return {"checked": checked, "missing": missing, "root": str(self.save_root)}

    def _keywords(self, table: str, channel_id: str) -> list[str]:
        return [r[0] for r in self.db.execute(f"SELECT keyword FROM {table} WHERE channel_id=?", (channel_id,))]

    def preview(self, channel_id: Optional[str] = None, media_type: str = "video", options=None) -> dict:
        result = []
        for v in self.list_videos(channel_id, media_type):
            black = self._keywords("blacklist", v.channel_id); allow = self._keywords("allowlist", v.channel_id)
            reason = ""
            min_d = (getattr(options, "min_duration", None) if options else None)
            max_d = (getattr(options, "max_duration", None) if options else None)
            if min_d is None: min_d = self.min_duration
            if max_d is None: max_d = self.max_duration
            if min_d and (v.duration is None or v.duration < min_d): reason = "duration_out_of_range"
            elif max_d and (v.duration is None or v.duration > max_d): reason = "duration_out_of_range"
            elif allow and not any(k.casefold() in v.title.casefold() for k in allow): reason = "allowlist_miss"
            elif any(k.casefold() in v.title.casefold() for k in black): reason = "blacklist"
            decision = "DOWNLOADED" if v.status == "DOWNLOADED" else ("FILTERED" if reason else "READY")
            result.append((v, decision, reason))
        from collections import Counter
        return {"stats": dict(Counter(d for _, d, _ in result)), "decisions": result}

    def download(self, channel_id: Optional[str] = None, media_type: str = "video", *, quality: Optional[str] = None, options=None, stop_event: Optional[threading.Event] = None) -> dict:
        rows = self.preview(channel_id, media_type, options)["decisions"]; done = failed = 0
        for video, decision, reason in rows:
            if decision != "READY": continue
            if stop_event and stop_event.is_set(): break
            channel = next((c for c in self.list_channels() if c.channel_id == video.channel_id), None)
            folder = self.save_root / sanitize_filename(channel.name if channel else video.channel_id, 60); folder.mkdir(parents=True, exist_ok=True)
            height = {"720p": 720, "1080p": 1080, "1080p+": 1080, "1080p60": 1080, "4k": 2160}.get((quality or "").lower())
            fmt = (f"bestvideo[height<={height}]+bestaudio/best[height<={height}]" if height and media_type == "video" else "bestvideo+bestaudio/best" if media_type == "video" else "bestaudio[ext=m4a]/bestaudio")
            out = str(folder / f"%(title)s [{video.video_id}].%(ext)s")
            opts = {"quiet": True, "no_warnings": True, "format": fmt, "outtmpl": out, "noplaylist": True, "merge_output_format": "mp4" if media_type == "video" else "m4a", "ffmpeg_location": self.ffmpeg_path or None}
            def _progress_hook(_status):
                if stop_event and stop_event.is_set():
                    raise RuntimeError("下载已停止")
            opts["progress_hooks"] = [_progress_hook]
            try:
                self.db.execute("UPDATE media SET status='DOWNLOADING',error='' WHERE video_id=? AND media_type=?", (video.video_id, media_type)); self.db.commit()
                self._ydl().YoutubeDL({k: v for k, v in opts.items() if v is not None}).download([video.url])
                path = next((p for p in folder.glob("*") if f"[{video.video_id}]" in p.name and p.suffix.lower() in {".mp4", ".m4a", ".webm"} and p.stat().st_size > 0), None)
                if not path: raise RuntimeError("yt-dlp 未生成有效文件")
                self.db.execute("UPDATE media SET status='DOWNLOADED',download_path=?,download_time=?,error='' WHERE video_id=? AND media_type=?", (str(path), datetime.utcnow().isoformat(), video.video_id, media_type)); self.db.commit(); done += 1
            except Exception as exc:
                status = "PENDING" if stop_event and stop_event.is_set() else "FAILED"
                self.db.execute("UPDATE media SET status=?,error=? WHERE video_id=? AND media_type=?", (status, str(exc), video.video_id, media_type)); self.db.commit(); failed += status == "FAILED"
        return {"downloaded": done, "failed": failed}
