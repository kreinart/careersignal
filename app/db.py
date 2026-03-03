from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.config import BASE_DIR

DB_PATH = Path(str((BASE_DIR / "data" / "app.db").resolve()))


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _add_column_if_missing(cur: sqlite3.Cursor, table: str, column: str, ddl: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    con = connect()
    cur = con.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          discord_webhook TEXT,
          email_notifications_enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS watchers (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          url TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1,
          keywords TEXT,
          notify_on_change INTEGER NOT NULL DEFAULT 1,
          notify_on_new_jobs INTEGER NOT NULL DEFAULT 1,
          notify_on_keyword INTEGER NOT NULL DEFAULT 1,
          discord_webhook_override TEXT,

          last_status TEXT,
          last_checked_at TEXT,
          last_error TEXT,
          last_http_status INTEGER,
          last_content_hash TEXT,
          blocked_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,

          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS check_results (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          watcher_id INTEGER NOT NULL,
          checked_at TEXT NOT NULL,
          status TEXT NOT NULL,
          http_status INTEGER,
          error_message TEXT,
          content_hash TEXT,
          changed INTEGER NOT NULL DEFAULT 0,
          keyword_hits_json TEXT,
          new_links_count INTEGER NOT NULL DEFAULT 0,
          sample_links_json TEXT,

          FOREIGN KEY(watcher_id) REFERENCES watchers(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          watcher_id INTEGER NOT NULL,
          url TEXT NOT NULL,
          title TEXT,
          first_seen_at TEXT NOT NULL,
          UNIQUE(watcher_id, url),
          FOREIGN KEY(watcher_id) REFERENCES watchers(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_watchers_user ON watchers(user_id);
        CREATE INDEX IF NOT EXISTS idx_watchers_last_checked ON watchers(last_checked_at);
        CREATE INDEX IF NOT EXISTS idx_check_results_watcher_time ON check_results(watcher_id, checked_at DESC);
        CREATE INDEX IF NOT EXISTS idx_job_links_watcher_time ON job_links(watcher_id, first_seen_at DESC);
        """
    )

    # Lightweight migrations (keep running DBs alive)
    _add_column_if_missing(cur, "users", "discord_webhook", "discord_webhook TEXT")
    _add_column_if_missing(cur, "users", "email_notifications_enabled", "email_notifications_enabled INTEGER NOT NULL DEFAULT 1")

    _add_column_if_missing(cur, "watchers", "last_http_status", "last_http_status INTEGER")
    _add_column_if_missing(cur, "watchers", "last_content_hash", "last_content_hash TEXT")
    _add_column_if_missing(cur, "watchers", "blocked_count", "blocked_count INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(cur, "watchers", "failed_count", "failed_count INTEGER NOT NULL DEFAULT 0")

    con.commit()
    con.close()
