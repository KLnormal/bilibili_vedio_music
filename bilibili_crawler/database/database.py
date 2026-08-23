"""SQLite connection management and schema initialization."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS up (
    mid              INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL DEFAULT '',
    face             TEXT    NOT NULL DEFAULT '',
    description      TEXT    NOT NULL DEFAULT '',
    first_crawl_time TEXT,
    last_crawl_time  TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1,
    save_path        TEXT    NOT NULL DEFAULT '',
    scan_next_page   INTEGER NOT NULL DEFAULT 1,
    scan_incomplete  INTEGER NOT NULL DEFAULT 0,
    scan_complete    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS video (
    bvid            TEXT PRIMARY KEY,
    mid             INTEGER NOT NULL,
    duration        INTEGER,
    created         INTEGER,
    title           TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    pic             TEXT    NOT NULL DEFAULT '',
    url             TEXT    NOT NULL DEFAULT '',
    update_time     TEXT,
    download_status TEXT    NOT NULL DEFAULT 'PENDING',
    download_path   TEXT    NOT NULL DEFAULT '',
    download_time   TEXT,
    download_error  TEXT    NOT NULL DEFAULT '',
    filter_reason   TEXT    NOT NULL DEFAULT '',
    FOREIGN KEY (mid) REFERENCES up (mid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS up_blacklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mid        INTEGER NOT NULL,
    keyword    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (mid, keyword),
    FOREIGN KEY (mid) REFERENCES up (mid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS up_filter_settings (
    mid          INTEGER PRIMARY KEY,
    min_duration INTEGER,
    max_duration INTEGER,
    min_date     TEXT,
    max_date     TEXT,
    FOREIGN KEY (mid) REFERENCES up (mid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_video_mid ON video (mid);
CREATE INDEX IF NOT EXISTS idx_video_status ON video (download_status);
CREATE INDEX IF NOT EXISTS idx_blacklist_mid ON up_blacklist (mid);
"""


class Database:
    """Thin wrapper around a single SQLite connection.

    ``check_same_thread=False`` is deliberate: the repository is used from the
    crawler/scheduler threads as well as the TUI thread. Access is serialized
    with a lock held per repository call.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Enable WAL for better read/write concurrency.
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; transactions are explicit
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Idempotent schema migrations for databases created by older versions."""
        cols = [row[1] for row in self._conn.execute("PRAGMA table_info(video)")]
        up_cols = [row[1] for row in self._conn.execute("PRAGMA table_info(up)")]
        if "scan_next_page" not in up_cols:
            self._conn.execute(
                "ALTER TABLE up ADD COLUMN scan_next_page INTEGER NOT NULL DEFAULT 1"
            )
        if "scan_incomplete" not in up_cols:
            self._conn.execute(
                "ALTER TABLE up ADD COLUMN scan_incomplete INTEGER NOT NULL DEFAULT 0"
            )
        if "scan_complete" not in up_cols:
            self._conn.execute(
                "ALTER TABLE up ADD COLUMN scan_complete INTEGER NOT NULL DEFAULT 0"
            )
        if "filter_reason" not in cols:
            self._conn.execute(
                "ALTER TABLE video ADD COLUMN filter_reason TEXT NOT NULL DEFAULT ''"
            )
        if "created" not in cols:
            self._conn.execute(
                "ALTER TABLE video ADD COLUMN created INTEGER"
            )
        # v0.1 used SKIPPED for "filtered out"; v0.2 renamed it to FILTERED.
        self._conn.execute(
            "UPDATE video SET download_status = 'FILTERED' "
            "WHERE download_status = 'SKIPPED'"
        )

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
