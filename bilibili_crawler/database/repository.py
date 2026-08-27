"""Repository layer: all SQL access lives here.

The database is the single source of truth (plan principle #1). Video
"discovered" and "downloaded" are independent concepts (principle #2).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from .database import Database
from .models import DownloadStatus, MediaDownload, Up, UpFilterSettings, Video


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, db: Database):
        self._db = db
        self._lock = threading.RLock()

    @staticmethod
    def _validate_media_type(media_type: str) -> str:
        if media_type not in ("video", "audio"):
            raise ValueError("media_type must be 'video' or 'audio'")
        return media_type

    # ------------------------------------------------------------------ UP --
    def upsert_up(self, up: Up) -> None:
        """Insert or update a UP record keyed by ``mid``."""
        with self._lock:
            self._db.connection.execute(
                """
                INSERT INTO up (mid, name, face, description, first_crawl_time,
                                last_crawl_time, enabled, save_path,
                                scan_next_page, scan_incomplete, scan_complete)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mid) DO UPDATE SET
                    name = excluded.name,
                    face = excluded.face,
                    description = excluded.description,
                    enabled = excluded.enabled,
                    save_path = excluded.save_path
                """,
                (
                    up.mid,
                    up.name,
                    up.face,
                    up.description,
                    up.first_crawl_time,
                    up.last_crawl_time,
                    int(up.enabled),
                    up.save_path,
                    up.scan_next_page,
                    int(up.scan_incomplete),
                    int(up.scan_complete),
                ),
            )

    def get_up(self, mid: int) -> Optional[Up]:
        with self._lock:
            row = self._db.connection.execute(
                "SELECT * FROM up WHERE mid = ?", (mid,)
            ).fetchone()
        return self._up_from_row(row) if row else None

    def list_ups(self, enabled_only: bool = False) -> List[Up]:
        query = "SELECT * FROM up"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY mid"
        with self._lock:
            rows = self._db.connection.execute(query).fetchall()
        return [self._up_from_row(r) for r in rows]

    def delete_up(self, mid: int) -> bool:
        with self._lock:
            cur = self._db.connection.execute("DELETE FROM up WHERE mid = ?", (mid,))
        return cur.rowcount > 0

    def set_up_enabled(self, mid: int, enabled: bool) -> None:
        with self._lock:
            self._db.connection.execute(
                "UPDATE up SET enabled = ? WHERE mid = ?", (int(enabled), mid)
            )

    def touch_up_crawl(self, mid: int, first: bool = False) -> None:
        now = _now_iso()
        with self._lock:
            if first:
                self._db.connection.execute(
                    "UPDATE up SET first_crawl_time = COALESCE(first_crawl_time, ?), "
                    "last_crawl_time = ? WHERE mid = ?",
                    (now, now, mid),
                )
            else:
                self._db.connection.execute(
                    "UPDATE up SET last_crawl_time = ? WHERE mid = ?", (now, mid)
                )

    @staticmethod
    def _up_from_row(row) -> Up:
        return Up(
            mid=row["mid"],
            name=row["name"],
            face=row["face"],
            description=row["description"],
            first_crawl_time=row["first_crawl_time"],
            last_crawl_time=row["last_crawl_time"],
            enabled=bool(row["enabled"]),
            save_path=row["save_path"],
            scan_next_page=row["scan_next_page"] if "scan_next_page" in row.keys() else 1,
            scan_incomplete=bool(row["scan_incomplete"]) if "scan_incomplete" in row.keys() else False,
            scan_complete=bool(row["scan_complete"]) if "scan_complete" in row.keys() else False,
        )

    def set_scan_progress(self, mid: int, next_page: int, incomplete: bool,
                          complete: Optional[bool] = None) -> None:
        with self._lock:
            if complete is None:
                self._db.connection.execute(
                    "UPDATE up SET scan_next_page = ?, scan_incomplete = ? WHERE mid = ?",
                    (max(1, int(next_page)), int(incomplete), mid),
                )
            else:
                self._db.connection.execute(
                    "UPDATE up SET scan_next_page = ?, scan_incomplete = ?, scan_complete = ? WHERE mid = ?",
                    (max(1, int(next_page)), int(incomplete), int(complete), mid),
                )

    # ---------------------------------------------------------------- Video --
    def video_exists(self, bvid: str) -> bool:
        with self._lock:
            row = self._db.connection.execute(
                "SELECT 1 FROM video WHERE bvid = ?", (bvid,)
            ).fetchone()
        return row is not None

    def get_video(self, bvid: str) -> Optional[Video]:
        with self._lock:
            row = self._db.connection.execute(
                "SELECT * FROM video WHERE bvid = ?", (bvid,)
            ).fetchone()
        return Video.from_row(dict(row)) if row else None

    def insert_video(self, video: Video) -> bool:
        """Create a new video record; ``bvid`` is the unique identity.

        Uses ``INSERT OR IGNORE`` so a concurrent scan (e.g. manual refresh
        while the scheduler runs) never crashes on a duplicate bvid. Returns
        True when a row was actually inserted.
        """
        row = video.to_row()
        columns = list(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        with self._lock:
            cur = self._db.connection.execute(
                f"INSERT OR IGNORE INTO video ({', '.join(columns)}) VALUES ({placeholders})",
                [row[c] for c in columns],
            )
            inserted = cur.rowcount > 0
            if inserted:
                self._ensure_media_row(
                    video.bvid, "video", video.download_status,
                    video.download_path, video.download_time,
                    video.download_error, video.filter_reason,
                )
                self._ensure_media_row(video.bvid, "audio")
        return inserted

    def _ensure_media_row(self, bvid: str, media_type: str,
                          status: DownloadStatus = DownloadStatus.PENDING,
                          path: str = "", download_time: Optional[str] = None,
                          error: str = "", reason: str = "") -> None:
        self._db.connection.execute(
            """INSERT OR IGNORE INTO video_media
               (bvid, media_type, download_status, download_path,
                download_time, download_error, filter_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (bvid, media_type, status.value, path or "", download_time,
             error or "", reason or ""),
        )

    def get_media(self, bvid: str, media_type: str = "video") -> MediaDownload:
        self._validate_media_type(media_type)
        with self._lock:
            self._ensure_media_row(bvid, media_type)
            row = self._db.connection.execute(
                "SELECT * FROM video_media WHERE bvid = ? AND media_type = ?",
                (bvid, media_type),
            ).fetchone()
        return MediaDownload(
            bvid=row["bvid"], media_type=row["media_type"],
            status=DownloadStatus(row["download_status"]),
            download_path=row["download_path"] or "",
            download_time=row["download_time"],
            download_error=row["download_error"] or "",
            filter_reason=row["filter_reason"] or "",
        )

    def update_video_meta(self, video: Video) -> None:
        """Refresh metadata + update_time for an already-known video."""
        with self._lock:
            self._db.connection.execute(
                """
                UPDATE video SET
                    mid = ?, duration = ?, title = ?, description = ?, pic = ?,
                    url = ?, update_time = ?
                WHERE bvid = ?
                """,
                (
                    video.mid,
                    video.duration,
                    video.title,
                    video.description,
                    video.pic,
                    video.url,
                    video.update_time,
                    video.bvid,
                ),
            )

    def update_download_status(
        self,
        bvid: str,
        status: DownloadStatus,
        *,
        path: Optional[str] = None,
        error: Optional[str] = None,
        media_type: str = "video",
    ) -> None:
        if media_type not in ("video", "audio"):
            raise ValueError("media_type must be 'video' or 'audio'")
        with self._lock:
            self._ensure_media_row(bvid, media_type)
            if status is DownloadStatus.DOWNLOADING:
                self._db.connection.execute(
                    "UPDATE video_media SET download_status = ?, download_error = '' "
                    "WHERE bvid = ? AND media_type = ?",
                    (status.value, bvid, media_type),
                )
            elif status is DownloadStatus.DOWNLOADED:
                self._db.connection.execute(
                    "UPDATE video_media SET download_status = ?, download_path = ?, "
                    "download_time = ?, download_error = '' WHERE bvid = ? AND media_type = ?",
                    (status.value, path or "", _now_iso(), bvid, media_type),
                )
            elif status is DownloadStatus.FAILED:
                self._db.connection.execute(
                    "UPDATE video_media SET download_status = ?, download_error = ? "
                    "WHERE bvid = ? AND media_type = ?",
                    (status.value, error or "", bvid, media_type),
                )
            else:
                self._db.connection.execute(
                    "UPDATE video_media SET download_status = ? WHERE bvid = ? AND media_type = ?",
                    (status.value, bvid, media_type),
                )
            if media_type == "video":
                row = self._db.connection.execute(
                    "SELECT download_status, download_path, download_time, download_error, filter_reason "
                    "FROM video_media WHERE bvid = ? AND media_type = 'video'", (bvid,)
                ).fetchone()
                self._db.connection.execute(
                    "UPDATE video SET download_status = ?, download_path = ?, download_time = ?, "
                    "download_error = ?, filter_reason = ? WHERE bvid = ?",
                    (row["download_status"], row["download_path"], row["download_time"],
                     row["download_error"], row["filter_reason"], bvid),
                )

    def _sync_legacy_video(self, bvid: str) -> None:
        row = self._db.connection.execute(
            "SELECT download_status, download_path, download_time, download_error, filter_reason "
            "FROM video_media WHERE bvid = ? AND media_type = 'video'", (bvid,)
        ).fetchone()
        if row:
            self._db.connection.execute(
                "UPDATE video SET download_status = ?, download_path = ?, download_time = ?, "
                "download_error = ?, filter_reason = ? WHERE bvid = ?",
                (row["download_status"], row["download_path"], row["download_time"],
                 row["download_error"], row["filter_reason"], bvid),
            )

    def _sync_legacy_all(self) -> None:
        # Avoid SQLite-version-specific ``UPDATE ... FROM``.  The desktop
        # package may run against an older embedded SQLite, so synchronize via
        # the portable correlated subqueries in one statement.
        self._db.connection.execute(
            """UPDATE video SET
                download_status = (SELECT m.download_status FROM video_media m WHERE m.bvid = video.bvid AND m.media_type = 'video'),
                download_path = (SELECT m.download_path FROM video_media m WHERE m.bvid = video.bvid AND m.media_type = 'video'),
                download_time = (SELECT m.download_time FROM video_media m WHERE m.bvid = video.bvid AND m.media_type = 'video'),
                download_error = (SELECT m.download_error FROM video_media m WHERE m.bvid = video.bvid AND m.media_type = 'video'),
                filter_reason = (SELECT m.filter_reason FROM video_media m WHERE m.bvid = video.bvid AND m.media_type = 'video')
              WHERE EXISTS (SELECT 1 FROM video_media m WHERE m.bvid = video.bvid AND m.media_type = 'video')"""
        )

    def reset_failed(self, mid: Optional[int] = None, media_type: str = "video") -> int:
        """Move FAILED videos back to PENDING so they can be retried."""
        self._validate_media_type(media_type)
        with self._lock:
            if mid is None:
                cur = self._db.connection.execute(
                    "UPDATE video_media SET download_status = 'PENDING', download_error = '' "
                    "WHERE download_status = 'FAILED' AND media_type = ?", (media_type,)
                )
            else:
                cur = self._db.connection.execute(
                    "UPDATE video_media SET download_status = 'PENDING', download_error = '' "
                    "WHERE download_status = 'FAILED' AND media_type = ? AND bvid IN "
                    "(SELECT bvid FROM video WHERE mid = ?)", (media_type, mid),
                )
            if media_type == "video":
                self._sync_legacy_all()
        return cur.rowcount

    def recover_orphan_downloading(self, mid: Optional[int] = None, media_type: Optional[str] = None) -> int:
        """Reset stuck DOWNLOADING videos (e.g. after a crash) back to PENDING."""
        if media_type is not None:
            self._validate_media_type(media_type)
        with self._lock:
            if mid is None:
                query = "UPDATE video_media SET download_status = 'PENDING', download_error = '' WHERE download_status = 'DOWNLOADING'"
                params = []
                if media_type:
                    query += " AND media_type = ?"; params.append(media_type)
                cur = self._db.connection.execute(query, params)
            else:
                query = "UPDATE video_media SET download_status = 'PENDING', download_error = '' WHERE download_status = 'DOWNLOADING' AND bvid IN (SELECT bvid FROM video WHERE mid = ?)"
                params = [mid]
                if media_type:
                    query += " AND media_type = ?"; params.append(media_type)
                cur = self._db.connection.execute(query, params)
            self._sync_legacy_all()
        return cur.rowcount

    def set_filtered(self, bvid: str, reason: str, media_type: str = "video") -> None:
        """Mark a video FILTERED with an explainable ``filter_reason``."""
        self._validate_media_type(media_type)
        with self._lock:
            self._db.connection.execute(
                "UPDATE video_media SET download_status = 'FILTERED', filter_reason = ? "
                "WHERE bvid = ? AND media_type = ?",
                (reason, bvid, media_type),
            )
            if media_type == "video": self._sync_legacy_video(bvid)

    def set_pending(self, bvid: str, media_type: str = "video") -> None:
        """Return a previously filtered video to the download queue."""
        self._validate_media_type(media_type)
        with self._lock:
            self._db.connection.execute(
                "UPDATE video_media SET download_status = 'PENDING', filter_reason = '' "
                "WHERE bvid = ? AND media_type = ?",
                (bvid, media_type),
            )
            if media_type == "video": self._sync_legacy_video(bvid)

    def list_videos(self, mid: Optional[int] = None, media_type: str = "video") -> List[Video]:
        self._validate_media_type(media_type)
        query = "SELECT * FROM video"
        params: list = []
        if mid is not None:
            query += " WHERE mid = ?"
            params.append(mid)
        query += " ORDER BY update_time DESC"
        with self._lock:
            rows = self._db.connection.execute(query, params).fetchall()
        videos = [Video.from_row(dict(r)) for r in rows]
        for video in videos:
            media = self.get_media(video.bvid, media_type)
            video.download_status = media.status
            video.download_path = media.download_path
            video.download_time = media.download_time
            video.download_error = media.download_error
            video.filter_reason = media.filter_reason
        return videos

    def list_downloaded(self, mid: Optional[int] = None, media_type: str = "video") -> List[Video]:
        """Return videos whose status is DOWNLOADED (for file-consistency checks)."""
        self._validate_media_type(media_type)
        query = "SELECT v.* FROM video v JOIN video_media m ON m.bvid = v.bvid AND m.media_type = ? WHERE m.download_status = 'DOWNLOADED'"
        params: list = [media_type]
        if mid is not None:
            query += " AND v.mid = ?"
            params.append(mid)
        with self._lock:
            rows = self._db.connection.execute(query, params).fetchall()
        videos = [Video.from_row(dict(r)) for r in rows]
        for video in videos:
            media = self.get_media(video.bvid, media_type)
            video.download_status, video.download_path = media.status, media.download_path
            video.download_time, video.download_error = media.download_time, media.download_error
            video.filter_reason = media.filter_reason
        return videos

    def list_pending(self, mid: Optional[int] = None, limit: int = 100, media_type: str = "video") -> List[Video]:
        self._validate_media_type(media_type)
        query = "SELECT v.* FROM video v JOIN video_media m ON m.bvid = v.bvid AND m.media_type = ? WHERE m.download_status = 'PENDING'"
        params: list = [media_type]
        if mid is not None:
            query += " AND v.mid = ?"
            params.append(mid)
        query += " ORDER BY update_time LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.connection.execute(query, params).fetchall()
        videos = [Video.from_row(dict(r)) for r in rows]
        for video in videos:
            media = self.get_media(video.bvid, media_type)
            video.download_status, video.download_path = media.status, media.download_path
            video.download_time, video.download_error = media.download_time, media.download_error
            video.filter_reason = media.filter_reason
        return videos

    def claim_next_pending(self, mid: Optional[int] = None, media_type: str = "video") -> Optional[Video]:
        """Atomically claim one PENDING video (flip it to DOWNLOADING).

        The SELECT-then-UPDATE runs inside the repository lock and the UPDATE
        is guarded by ``download_status = 'PENDING'``, so two concurrent
        workers can never claim the same row. Returns the claimed video, or
        ``None`` when no PENDING video remains.
        """
        with self._lock:
            query = "SELECT v.* FROM video v JOIN video_media m ON m.bvid = v.bvid AND m.media_type = ? WHERE m.download_status = 'PENDING'"
            params: list = [media_type]
            if mid is not None:
                query += " AND v.mid = ?"
                params.append(mid)
            query += " ORDER BY update_time LIMIT 1"
            row = self._db.connection.execute(query, params).fetchone()
            if row is None:
                return None
            bvid = row["bvid"]
            cur = self._db.connection.execute(
                "UPDATE video_media SET download_status = 'DOWNLOADING', download_error = '' "
                "WHERE bvid = ? AND media_type = ? AND download_status = 'PENDING'",
                (bvid, media_type),
            )
            if cur.rowcount != 1:
                return None
            video = Video.from_row(dict(row))
            media = self.get_media(bvid, media_type)
            video.download_status, video.download_path = media.status, media.download_path
            video.download_time, video.download_error = media.download_time, media.download_error
            video.filter_reason = media.filter_reason
            return video

    def count_videos(self, mid: Optional[int] = None) -> int:
        query = "SELECT COUNT(*) AS c FROM video"
        params: list = []
        if mid is not None:
            query += " WHERE mid = ?"
            params.append(mid)
        with self._lock:
            return self._db.connection.execute(query, params).fetchone()["c"]

    def count_by_status(self, mid: Optional[int] = None, media_type: str = "video") -> dict:
        """Return a mapping status -> count."""
        self._validate_media_type(media_type)
        query = "SELECT m.download_status, COUNT(*) AS c FROM video v JOIN video_media m ON m.bvid = v.bvid AND m.media_type = ?"
        params: list = [media_type]
        if mid is not None:
            query += " WHERE v.mid = ?"
            params.append(mid)
        query += " GROUP BY m.download_status"
        with self._lock:
            rows = self._db.connection.execute(query, params).fetchall()
        result = {s.value: 0 for s in DownloadStatus}
        for r in rows:
            result[r["download_status"]] = r["c"]
        return result

    def bvids_for_mid(self, mid: int) -> set:
        with self._lock:
            rows = self._db.connection.execute(
                "SELECT bvid FROM video WHERE mid = ?", (mid,)
            ).fetchall()
        return {r["bvid"] for r in rows}

    # ------------------------------------------------------------- blacklist --
    def add_blacklist(self, mid: int, keyword: str) -> bool:
        """Add a keyword to an UP's blacklist. Returns True if inserted."""
        keyword = keyword.strip()
        if not keyword:
            return False
        with self._lock:
            cur = self._db.connection.execute(
                "INSERT OR IGNORE INTO up_blacklist (mid, keyword) VALUES (?, ?)",
                (mid, keyword),
            )
        return cur.rowcount > 0

    def remove_blacklist(self, mid: int, keyword: str) -> bool:
        """Remove a keyword from an UP's blacklist. Returns True if deleted."""
        with self._lock:
            cur = self._db.connection.execute(
                "DELETE FROM up_blacklist WHERE mid = ? AND keyword = ?",
                (mid, keyword.strip()),
            )
        return cur.rowcount > 0

    def list_blacklist(self, mid: int) -> List[str]:
        """Return an UP's blacklist keywords in insertion order."""
        with self._lock:
            rows = self._db.connection.execute(
                "SELECT keyword FROM up_blacklist WHERE mid = ? ORDER BY id", (mid,)
            ).fetchall()
        return [r["keyword"] for r in rows]

    # ------------------------------------------------------------- allowlist --
    def add_allowlist(self, mid: int, keyword: str) -> bool:
        """Add a title keyword to an UP's specified-download list."""
        keyword = keyword.strip()
        if not keyword:
            return False
        with self._lock:
            cur = self._db.connection.execute(
                "INSERT OR IGNORE INTO up_allowlist (mid, keyword) VALUES (?, ?)",
                (mid, keyword),
            )
        return cur.rowcount > 0

    def remove_allowlist(self, mid: int, keyword: str) -> bool:
        with self._lock:
            cur = self._db.connection.execute(
                "DELETE FROM up_allowlist WHERE mid = ? AND keyword = ?",
                (mid, keyword.strip()),
            )
        return cur.rowcount > 0

    def list_allowlist(self, mid: int) -> List[str]:
        with self._lock:
            rows = self._db.connection.execute(
                "SELECT keyword FROM up_allowlist WHERE mid = ? ORDER BY id", (mid,)
            ).fetchall()
        return [r["keyword"] for r in rows]

    # ---------------------------------------------------------- UP filters --
    def get_up_filter_settings(self, mid: int) -> UpFilterSettings:
        with self._lock:
            row = self._db.connection.execute(
                "SELECT * FROM up_filter_settings WHERE mid = ?", (mid,)
            ).fetchone()
        if row is None:
            return UpFilterSettings(mid=mid)
        return UpFilterSettings(
            mid=mid,
            min_duration=row["min_duration"],
            max_duration=row["max_duration"],
            min_date=row["min_date"],
            max_date=row["max_date"],
        )

    def upsert_up_filter_settings(self, settings: UpFilterSettings) -> None:
        with self._lock:
            self._db.connection.execute(
                """INSERT INTO up_filter_settings
                   (mid, min_duration, max_duration, min_date, max_date)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(mid) DO UPDATE SET
                   min_duration=excluded.min_duration,
                   max_duration=excluded.max_duration,
                   min_date=excluded.min_date,
                   max_date=excluded.max_date""",
                (settings.mid, settings.min_duration, settings.max_duration,
                 settings.min_date, settings.max_date),
            )
